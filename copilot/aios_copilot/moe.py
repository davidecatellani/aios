"""Modelli a esperti (MoE): tanti parametri, pochi usati per ogni parola.

Un modello come Qwen3 30B-A3B ha 30 miliardi di parametri divisi in «esperti», ma per
ogni parola ne usa circa 3: va veloce come un modello piccolo e ragiona quasi come uno
grande. AIOS decide dove tenerne i pezzi in base al dispositivo:

- gpu      tutto nella memoria della scheda video
- ram      tutto nella RAM (Ollama)
- gpu+ram  attenzione sulla GPU, esperti nella RAM (llama.cpp, --n-cpu-moe)
- disco    esperti letti dal disco NVMe/SSD solo quando servono, i più usati restano in RAM
           (llama.cpp con mmap: il sistema tiene in cache le pagine usate di recente)

La velocità stimata qui serve solo a scegliere; quella vera la misura la prova sul
dispositivo (trial.py), che scarta il modello se è troppo lento.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .hardware import Device

MIN_TOKENS_PER_SECOND = 4.0  # come trial.py: sotto, il copilota sembra bloccato
DISK_GBPS = {"nvme": 3.0, "ssd": 0.5, "hdd": 0.1}
GPU_GBPS = 300.0
SHARED = 0.4  # parte letta a ogni parola che non sono esperti (attenzione, incorporamenti)
MIN_CACHE = 0.25  # in modalità disco almeno un quarto degli esperti deve restare in RAM
# Gli esperti non sono usati tutti allo stesso modo: in cache restano i più richiesti, quindi le
# letture dal disco sono meno della parte di esperti fuori dalla RAM (stima; decide la prova).
POPULARITY = 1.5
SERVER_PORT = 11435


@dataclass
class Placement:
    mode: str  # gpu | ram | gpu+ram | disco
    tokens_per_second: float
    needs_server: bool  # serve llama.cpp (llama-server) invece di Ollama

    def describe(self) -> str:
        where = {"gpu": "tutto nella scheda video", "ram": "tutto in memoria",
                 "gpu+ram": "attenzione nella scheda video, esperti in memoria",
                 "disco": "esperti più usati in memoria, gli altri letti dal disco quando servono"}[self.mode]
        return f"{where}, circa {self.tokens_per_second:.0f} parole al secondo"


def ram_gbps(device: Device) -> float:
    return 40.0 if device.fast_cpu else 20.0


def ram_budget(device: Device) -> float:
    return max(0.0, device.ram_gb - max(2.0, device.ram_gb * 0.25))


def placements(device: Device, model: Any, server: bool | None = None) -> list[Placement]:
    """Tutte le sistemazioni possibili, dalla più veloce. `model` ha size_gb, ram_gb e active_gb."""
    server = has_server() if server is None else server
    active = model.active_gb
    shared = active * SHARED
    per_word = active - shared  # esperti letti per ogni parola
    experts = max(0.1, model.size_gb - shared)
    overhead = model.ram_gb - model.size_gb  # contesto e buffer
    ram, vram = ram_budget(device), device.vram_gb * 0.9 if device.vram_gb >= 4 else 0.0
    found = []
    if vram and model.ram_gb <= vram:
        found.append(Placement("gpu", GPU_GBPS / active, False))
    if model.ram_gb <= ram:
        found.append(Placement("ram", ram_gbps(device) / active, False))
    if server and vram and shared + overhead <= vram and experts <= ram:
        found.append(Placement("gpu+ram", 1 / (shared / GPU_GBPS + per_word / ram_gbps(device)), True))
    disk = DISK_GBPS.get(device.disk_kind, 0.0)
    cache = ram - shared - overhead
    if server and disk and model.ram_gb > ram and cache >= experts * MIN_CACHE:
        miss = (1 - min(1.0, cache / experts)) ** POPULARITY
        found.append(Placement("disco", 1 / (active / ram_gbps(device) + miss * per_word / disk), True))
    return sorted((p for p in found if p.tokens_per_second >= MIN_TOKENS_PER_SECOND), key=lambda p: -p.tokens_per_second)


def best_placement(device: Device, model: Any, server: bool | None = None) -> Placement | None:
    return next(iter(placements(device, model, server)), None)


def has_server() -> bool:
    return shutil.which("llama-server") is not None


# --- llama.cpp per le modalità gpu+ram e disco ------------------------------------------------------


def ollama_models_dirs() -> list[Path]:
    dirs = [Path(os.environ["OLLAMA_MODELS"])] if os.environ.get("OLLAMA_MODELS") else []
    return dirs + [Path.home() / ".ollama/models", Path("/usr/share/ollama/.ollama/models"), Path("/var/lib/ollama/models")]


def ollama_blob(name: str, dirs: list[Path] | None = None) -> Path | None:
    """Il file GGUF che Ollama ha scaricato per un modello: llama.cpp lo legge direttamente, senza copie."""
    repo, _, tag = name.partition(":")
    repo = repo if "/" in repo else f"library/{repo}"
    for base in dirs or ollama_models_dirs():
        try:
            manifest = json.loads((base / "manifests/registry.ollama.ai" / repo / (tag or "latest")).read_text())
        except (OSError, ValueError):
            continue
        for layer in manifest.get("layers", []):
            if layer.get("mediaType") == "application/vnd.ollama.image.model":
                blob = base / "blobs" / layer["digest"].replace(":", "-")
                if blob.is_file():
                    return blob
    return None


def server_command(gguf: Path, placement: Placement, context: int = 8192, port: int = SERVER_PORT) -> list[str]:
    cmd = ["llama-server", "-m", str(gguf), "--host", "127.0.0.1", "--port", str(port), "--jinja", "-c", str(context)]
    if placement.mode == "gpu+ram":
        cmd += ["-ngl", "999", "--n-cpu-moe", "999"]  # tutti gli strati sulla GPU, tranne gli esperti
    else:
        cmd += ["-ngl", "0"]  # disco: mmap (predefinito), il sistema tiene in RAM gli esperti più usati
    return cmd


def unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user/aios-esperti.service"


def write_unit(cmd: list[str]) -> Path:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Unit]\nDescription=AIOS: modello a esperti (llama.cpp)\n\n[Service]\n"
                    f"ExecStart={' '.join(cmd)}\nRestart=on-failure\nNice=5\n\n[Install]\nWantedBy=default.target\n")
    return path


def start_server(name: str, placement: Placement, run: Callable[[list[str]], tuple[int, str]] | None = None,
                 dirs: list[Path] | None = None) -> str:
    """Avvia llama-server sul modello scaricato da Ollama e restituisce il suo indirizzo."""
    from .tools.base import Runner

    gguf = ollama_blob(name, dirs)
    if gguf is None:
        raise RuntimeError(f"non trovo il file del modello {name} scaricato da Ollama")
    write_unit(server_command(gguf, placement))
    run = run or Runner().run
    for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "aios-esperti.service"],
                ["systemctl", "--user", "restart", "aios-esperti.service"]):
        code, out = run(cmd)
        if code != 0:
            raise RuntimeError(f"{' '.join(cmd)}: {out[-200:]}")
    return f"http://127.0.0.1:{SERVER_PORT}"


def server_call(base: str, post: Callable[[str, dict], dict] | None = None) -> Callable[[str, dict], dict]:
    """Adatta llama-server alle due chiamate di Ollama usate dalla prova sul dispositivo."""
    post = post or (lambda path, payload: _post(base + path, payload))

    def call(path: str, payload: dict) -> dict:
        options = payload.get("options", {})
        if path == "/api/generate":
            r = post("/completion", {"prompt": payload["prompt"], "n_predict": options.get("num_predict", 96),
                                     "temperature": options.get("temperature", 0)})
            t = r.get("timings", {})
            return {"eval_count": t.get("predicted_n", 0), "eval_duration": int(t.get("predicted_ms", 0) * 1e6)}
        r = post("/v1/chat/completions", {"messages": to_openai(payload["messages"]), "tools": payload.get("tools") or None,
                                          "temperature": options.get("temperature", 0.2)})
        return {"message": from_openai(r)}

    return call


def _post(url: str, payload: dict, timeout: int = 600) -> dict:
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps({k: v for k, v in payload.items() if v is not None}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def to_openai(messages: list[dict]) -> list[dict]:
    """Messaggi in stile Ollama → stile OpenAI (id delle chiamate, argomenti come testo)."""
    out, pending, n = [], [], 0
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                n += 1
                args = c.get("function", {}).get("arguments", {})
                calls.append({"id": f"call_{n}", "type": "function", "function": {
                    "name": c.get("function", {}).get("name", ""),
                    "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}})
            pending = [c["id"] for c in calls]
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif m.get("role") == "tool":
            out.append({"role": "tool", "tool_call_id": pending.pop(0) if pending else f"call_{n}", "content": m.get("content", "")})
        else:
            out.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    return out


def from_openai(response: dict) -> dict:
    msg = (response.get("choices") or [{}])[0].get("message", {})
    calls = []
    for c in msg.get("tool_calls") or []:
        args = c.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            args = {}
        calls.append({"function": {"name": c.get("function", {}).get("name", ""), "arguments": args}})
    return {"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})}
