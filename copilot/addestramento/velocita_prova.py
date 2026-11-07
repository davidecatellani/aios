"""La misura della velocità del modello di testo su un PC modesto (un i3 con 8 GB): lo stesso giro di richieste
vere di SoIA con modi diversi di far girare lo stesso modello, per vedere cosa accorcia davvero l'attesa.

    python addestramento/velocita_prova.py --modi ollama,llama-1,llama-3,llama-3-bozza \\
        --gguf q4.gguf --bozza bozza.gguf --server /percorso/llama-server

Il giro (12 richieste, come una mezz'ora d'uso): Nova che risponde con i suoi strumenti, il programmatore che
modifica un pezzo di codice, un riassunto di posta lunga; alternati, come capita davvero. I modi:

- ollama            com'è oggi SoIA (Ollama, contesto 8192);
- ollama-fa-q8      Ollama con flash attention e memoria del contesto a 8 bit;
- llama-1           llama-server con un posto solo: i lavori diversi si rubano la memoria del contesto;
- llama-3           llama-server con un posto per tipo di lavoro: le istruzioni fisse si leggono una volta;
- llama-3-bozza     come llama-3, più la bozza (il modello piccolo scrive in anticipo, il grande controlla);
- llama-3-t4        come llama-3, con 4 thread invece dei soli core veri.

Si misurano per ogni richiesta: l'attesa prima della prima parola (lettura del contesto), le parole al secondo,
il tempo totale, i token letti davvero (quelli non già in memoria) e la RAM massima. Esce un JSON e una tabella.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_TOKENS = 200
NUM_CTX = 8192
KINDS = {"nova": 0, "codice": 1, "posta": 2}  # il posto (slot) di llama-server per ogni tipo di lavoro
NOVA_ASKS = ["che tempo fa domani a Bologna?", "quanto fa il 22% di 340 euro?", "metti un widget con l'orologio di New York",
             "ricordati che il mio dentista si chiama Rossi", "che ore sono a Tokyo?", "cerca le ricette di pasta e ceci"]


# --- le richieste -------------------------------------------------------------------------------------------
def nova_tools() -> list[dict[str, Any]]:
    import importlib

    out = []
    for name in ("web", "calcolo", "widget", "personalizza", "memoria", "azioni", "fuso", "energy", "notifiche"):
        try:
            out += [t.schema() for t in importlib.import_module(f"aios_copilot.tools.{name}").make_tools()]
        except Exception:  # un gruppo che qui non si costruisce: si fa senza
            continue
    return out


def nova(i: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from aios_copilot.agent import SYSTEM_PROMPT

    return ([{"role": "system", "content": SYSTEM_PROMPT.format(today=date(2026, 11, 7).isoformat())},
             {"role": "user", "content": NOVA_ASKS[i % len(NOVA_ASKS)]}], nova_tools())


SEARCH = {"colore": ["--turchese"], "saluto": ["#saluto"], "ora": [".orologio-ora"], "sezione": ['"procedure"'],
          "scheda": ["appunti:"], "meteo": ["METEO_EVERY"], "scuro": ["--tenue"]}  # come un piano fatto bene


def codice(i: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from aios_copilot import programmatore as p
    from programmatore_prova import TASKS

    task = list(TASKS.values())[i % len(TASKS)]
    staged = p.StagedProgrammer(None, p.Workspace(ROOT))
    plan = {"file": list(task.files), "cerca": SEARCH.get(task.name, [task.name]), "cosa": task.request, "controlli": []}
    excerpts = staged.find_code(plan)
    return ([{"role": "system", "content": p.EDIT_SYSTEM},
             {"role": "user", "content": f"Richiesta dell'utente: {task.request}\n\nIl piano:\n- cosa: {task.request}\n"
                                         f"- file: {', '.join(task.files)}\n\nIl codice che serve:\n{excerpts}"}], p.EDIT_TOOLS)


def posta(i: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from procedure_prova import task_posta

    tasks = [task_posta(random.Random(f"velocita-{i}-{w}")) for w in range(3)]  # tre settimane: ~2700 token
    mails = "\n\n".join(t for task in tasks for k, t in sorted(task.files.items()) if k.startswith("input/"))
    return ([{"role": "system", "content": "Sei Nova, l'assistente di SoIA. Rispondi in italiano, breve e preciso."},
             {"role": "user", "content": f"Ecco la posta delle ultime tre settimane:\n\n{mails}\n\nDimmi in cinque punti le cose da fare, "
                                         "con chi e entro quando. Ignora la pubblicità."}], [])


def scenario() -> list[tuple[str, int]]:
    order, n = [], {"nova": 0, "codice": 0, "posta": 0}
    for kind in ["nova", "codice", "nova", "posta"] * 3:
        order.append((kind, n[kind]))
        n[kind] += 1
    return order


BUILD = {"nova": nova, "codice": codice, "posta": posta}


# --- i modi -------------------------------------------------------------------------------------------------
def _get(url: str, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def _post(url: str, payload: dict[str, Any], timeout: float = 1800) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class Peak:
    """La RAM massima dei processi del modello, letta ogni mezzo secondo."""

    def __init__(self, names: tuple[str, ...]):
        self.names, self.max_kb, self._stop = names, 0, threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            out = subprocess.run(["ps", "-eo", "rss=,args="], capture_output=True, text=True).stdout  # anche «ollama runner»
            kb = sum(int(l.split()[0]) for l in out.splitlines() if l.split() and any(n in l for n in self.names))
            self.max_kb = max(self.max_kb, kb)
            time.sleep(0.5)

    def stop(self) -> float:
        self._stop.set()
        return round(self.max_kb / 1e6, 2)


class Ollama:
    names = ("ollama",)

    def __init__(self, model: str, env: dict[str, str]):
        self.model, self.url = model, "http://127.0.0.1:11434"
        subprocess.run(["pkill", "-f", "ollama serve"], check=False)
        time.sleep(2)
        self.proc = subprocess.Popen(["ollama", "serve"], env={**os.environ, "OLLAMA_NUM_PARALLEL": "1", **env},
                                     stdout=subprocess.DEVNULL, stderr=open("ollama-serve.log", "a"))
        for _ in range(60):
            if _get(f"{self.url}/api/version"):
                break
            time.sleep(1)

    def ask(self, kind: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        start = time.monotonic()
        data = _post(f"{self.url}/api/chat", {"model": self.model, "messages": messages, "tools": tools, "stream": False,
                                              "think": False, "keep_alive": -1,
                                              "options": {"num_ctx": NUM_CTX, "temperature": 0, "num_predict": MAX_TOKENS}})
        msg = data.get("message", {})
        return {"letti": data.get("prompt_eval_count", 0), "attesa": data.get("prompt_eval_duration", 0) / 1e9,
                "scritti": data.get("eval_count", 0), "scrittura": data.get("eval_duration", 0) / 1e9,
                "carica": data.get("load_duration", 0) / 1e9, "totale": time.monotonic() - start,
                "risposta": (msg.get("content") or "") + json.dumps(msg.get("tool_calls") or [], ensure_ascii=False)}

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(30)


class Llama:
    names = ("llama-server",)

    def __init__(self, server: str, gguf: str, slots: int, args: list[str]):
        self.url, self.slots = "http://127.0.0.1:11437", slots
        cmd = [server, "-m", gguf, "--host", "127.0.0.1", "--port", "11437", "-c", str(NUM_CTX * slots), "-np", str(slots),
               "--no-webui", "--jinja", *args]
        print("avvio:", " ".join(cmd), flush=True)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open("llama-server.log", "a"),
                                     env={**os.environ, "LD_LIBRARY_PATH": str(Path(server).parent)})
        for _ in range(600):
            if _get(f"{self.url}/health"):
                return
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server non parte: " + Path("llama-server.log").read_text()[-800:])
            time.sleep(1)
        raise RuntimeError("llama-server non risponde")

    def ask(self, kind: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        from aios_copilot.moe import to_openai

        payload: dict[str, Any] = {"messages": to_openai(messages), "max_tokens": MAX_TOKENS, "temperature": 0,
                                   "cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False}}
        if tools:
            payload["tools"] = tools
        if self.slots > 1:
            payload["id_slot"] = KINDS[kind]
        start = time.monotonic()
        data = _post(f"{self.url}/v1/chat/completions", payload)
        t = data.get("timings", {})
        msg = (data.get("choices") or [{}])[0].get("message", {})
        return {"letti": t.get("prompt_n", 0), "attesa": t.get("prompt_ms", 0) / 1000, "scritti": t.get("predicted_n", 0),
                "scrittura": t.get("predicted_ms", 0) / 1000, "carica": 0.0, "totale": time.monotonic() - start,
                "bozza": t.get("draft_n", 0), "bozza_presa": t.get("draft_n_accepted", 0),
                "risposta": (msg.get("content") or "") + json.dumps(msg.get("tool_calls") or [], ensure_ascii=False)}

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(30)


def make(mode: str, args: argparse.Namespace) -> Any:
    if mode == "ollama":
        return Ollama(args.ollama, {})
    if mode == "ollama-fa-q8":
        return Ollama(args.ollama, {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"})
    if mode == "llama-1":
        return Llama(args.server, args.gguf, 1, [])
    if mode == "llama-3":
        return Llama(args.server, args.gguf, 3, [])
    if mode == "llama-3-bozza":
        return Llama(args.server, args.gguf, 3, ["-md", args.bozza])
    if mode == "llama-3-t4":
        return Llama(args.server, args.gguf, 3, ["-t", "4"])
    # per un PC da 8 GB: la memoria dei prompt passati (prompt cache in RAM, 8 GB di base) a 1 GB
    small = ["-t", "4", "--cache-ram", "1024"]
    if mode == "llama-3-t4-poca":
        return Llama(args.server, args.gguf, 3, small)
    if mode == "llama-3-t4-bozza":  # lo 0.8B come bozza normale (da solo llama.cpp lo prenderebbe per una testa MTP)
        return Llama(args.server, args.gguf, 3, [*small, "-md", args.bozza, "--spec-type", "draft-simple"])
    if mode == "llama-3-t4-mtp":  # la testa MTP del 4B stesso: nessun secondo modello
        return Llama(args.server, args.gguf, 3, [*small, "-md", args.gguf, "--spec-type", "draft-mtp"])
    if mode == "llama-3-t4-ngram":  # previsione dai pezzi già scritti nel contesto: nessun modello
        return Llama(args.server, args.gguf, 3, [*small, "--spec-type", "ngram-mod"])
    raise ValueError(mode)


def run_mode(mode: str, args: argparse.Namespace, prompts: list[tuple[str, int, Any]]) -> dict[str, Any]:
    try:
        engine = make(mode, args)
    except Exception as exc:  # un modo che non parte (es. la bozza non supportata) si annota e si va avanti
        return {"modo": mode, "errore": f"{type(exc).__name__}: {exc}"[:1500], "richieste": []}
    peak = Peak(engine.names)
    rows, error = [], ""
    try:
        engine.ask("nova", *nova(99))  # come SoIA: il modello è già caricato e Nova pronta
        for kind, i, (messages, tools) in prompts:
            r = engine.ask(kind, messages, tools)
            r.update({"tipo": kind, "n": i})
            rows.append(r)
            print(f"{mode} {kind} {i}: attesa {r['attesa']:.1f} s ({r['letti']} token letti), "
                  f"{r['scritti']} parole in {r['scrittura']:.1f} s, totale {r['totale']:.1f} s", flush=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        ram = peak.stop()
        engine.close()
    return {"modo": mode, "errore": error, "ram_gb": ram, "richieste": rows}


def summary(result: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any]:
    rs = result["richieste"]
    if not rs:
        return {}
    wrote, writing = sum(r["scritti"] for r in rs), sum(r["scrittura"] for r in rs)
    out = {"attesa_media": sum(r["attesa"] + r["carica"] for r in rs) / len(rs), "parole_al_secondo": wrote / max(writing, 1e-9),
           "totale": sum(r["totale"] for r in rs), "letti": sum(r["letti"] for r in rs), "fatte": len(rs)}
    drafted = sum(r.get("bozza", 0) for r in rs)
    if drafted:
        out["bozza_presa"] = sum(r.get("bozza_presa", 0) for r in rs) / drafted
    if base and base.get("richieste"):
        same = sum(a["risposta"] == b["risposta"] for a, b in zip(rs, base["richieste"]))
        out["uguali"] = f"{same}/{min(len(rs), len(base['richieste']))}"
    return out


def table(results: list[dict[str, Any]]) -> str:
    base = next((r for r in results if r["modo"].startswith("llama") and r["richieste"]), None)
    first = next((r for r in results if r["richieste"]), None)
    ref = summary(first, None).get("totale") if first else None
    lines = ["| modo | attesa media prima della risposta | parole al secondo | tempo totale del giro | rispetto al primo | "
             "token letti | RAM massima | bozza accettata | risposte uguali al primo llama |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = summary(r, base if r is not base else None)
        if not s:
            lines.append(f"| {r['modo']} | non parte: {r['errore'][:200]} | | | | | | | |")
            continue
        lines.append(f"| {r['modo']} | {s['attesa_media']:.1f} s | {s['parole_al_secondo']:.1f} | {s['totale'] / 60:.1f} min "
                     f"({s['fatte']} richieste) | {ref / s['totale']:.2f}× | {s['letti']} | {r['ram_gb']} GB | "
                     f"{s.get('bozza_presa', 0):.0%} | {s.get('uguali', '')} |" + (f" errore: {r['errore'][:120]}" if r["errore"] else ""))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modi", default="ollama,ollama-fa-q8,llama-1,llama-3,llama-3-bozza,llama-3-t4")
    ap.add_argument("--ollama", default="qwen3.5:4b")
    ap.add_argument("--gguf", default="")
    ap.add_argument("--bozza", default="")
    ap.add_argument("--server", default="llama-server")
    ap.add_argument("--uscita", default="velocita.json")
    args = ap.parse_args()
    prompts = [(kind, i, BUILD[kind](i)) for kind, i in scenario()]
    machine = {"cpu": subprocess.run(["sh", "-c", "lscpu | grep -E 'Model name|^CPU\\(s\\)|Thread|Core'"], capture_output=True,
                                     text=True).stdout.strip(), "ram": subprocess.run(["free", "-g"], capture_output=True, text=True).stdout}
    print(machine["cpu"], flush=True)
    results = []
    for mode in [m.strip() for m in args.modi.split(",") if m.strip()]:
        results.append(run_mode(mode, args, prompts))
        Path(args.uscita).write_text(json.dumps({"macchina": machine, "risultati": results}, ensure_ascii=False, indent=1))
    print("\n" + table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
