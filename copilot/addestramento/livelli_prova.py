"""La misura dei livelli della posta (aios_copilot/mail/livelli.py): «quali sono le cose da fare dalla posta?»
letto dal bronze (il modello legge tutte le mail mentre l'utente aspetta, come oggi) o dal gold (il modello ha letto
le mail prima, in sottofondo, una alla volta; la risposta la compone il codice).

    python addestramento/livelli_prova.py --modello qwen3.5:4b --quante 4

Le caselle sono quelle inventate della prova delle procedure (procedure_prova.task_posta: 20 mail, 5 cose da fare,
il resto informazioni e pubblicità), con la risposta giusta nota e gli stessi controlli. Si misurano: controlli
giusti, attesa dell'utente, e il lavoro in sottofondo che il gold costa (tempo e richieste al modello).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aios_copilot.mail import livelli  # noqa: E402
from aios_copilot.mail.classify import classify  # noqa: E402
from aios_copilot.mail.store import MailStore  # noqa: E402
from procedure_prova import MONTHS, Measured, task_posta  # noqa: E402

RULES = ("Una cosa da fare è una mail di una persona che chiede di fare qualcosa entro una data. Pubblicità, offerte e "
         "newsletter non sono cose da fare: non nominarle. Per ogni cosa da fare: chi la chiede (nome e cognome), cosa, "
         "entro quando (giorno e mese). In ordine di scadenza: prima la più vicina.")


def mails_of(box: str) -> list[tuple[str, str, str, str]]:
    """Il testo della casella → (nome, indirizzo, oggetto, testo)."""
    out = []
    for part in re.split(r"^### Mail \d+\n", box, flags=re.MULTILINE):
        m = re.match(r"Da: (.*?) <(.*?)>\nOggetto: (.*?)\n\n(.*)", part.strip(), re.DOTALL)
        if m:
            out.append((m.group(1), m.group(2), m.group(3), m.group(4).strip()))
    return out


def fill(store: MailStore, mails: list[tuple[str, str, str, str]], when: datetime) -> None:
    for uid, (name, address, subject, body) in enumerate(mails, 1):
        bulk = address.split("@")[0] in ("offerte", "newsletter")
        headers = {"list-unsubscribe": f"<mailto:esci@{address.split('@')[1]}>"} if bulk else {}  # come le newsletter vere
        v = classify(address, subject, body, headers)
        store.add({"account": "io@casa.it", "folder": "INBOX", "uidvalidity": 1, "uid": uid, "msgid": f"<{uid}@x>",
                   "in_reply_to": "", "sender_name": name, "sender": address, "recipients": ["io@casa.it"], "subject": subject,
                   "date": when, "snippet": body[:160], "body": body, "seen": 0, "attachments": [], "headers": headers,
                   "category": v.category, "importance": v.importance, "reason": v.reason})


def run_box(i: int, model: Measured, folder: Path) -> dict[str, Any]:
    task = task_posta(random.Random(f"livelli-{i}"))
    month = next(n for n, name in enumerate(MONTHS) if name in task.answer) + 1
    now = datetime(2026, month, 1, 10, 0)
    box = task.files["input/posta.md"]
    score = lambda text: [(name, bool(check(text))) for name, check in task.checks]  # noqa: E731

    # bronze: il modello legge tutto mentre l'utente aspetta
    model.calls = []
    start = time.monotonic()
    error = ""
    try:
        reply = model.chat([{"role": "system", "content": "Sei Nova, l'assistente di SoIA. Rispondi in italiano.\n" + RULES},
                            {"role": "user", "content": f"Ecco la mia posta:\n\n{box}\n\nQuali sono le cose da fare?"}], [])
        bronze_text = reply.get("content") or ""
    except Exception as exc:
        bronze_text, error = "", f"{type(exc).__name__}: {exc}"
    bronze = {"attesa": round(time.monotonic() - start, 1), "controlli": score(bronze_text),
              "token_letti": sum(c["prompt"] for c in model.calls), "risposta": bronze_text[:1500], "errore": error}

    # silver in sottofondo, poi il gold composto dal codice
    store = MailStore(folder / f"posta-{i}.db")
    fill(store, mails_of(box), now)
    model.calls = []
    start = time.monotonic()
    read = livelli.update(store, model, now=now, limit=100)
    background = round(time.monotonic() - start, 1)
    start = time.monotonic()
    gold_text = livelli.todo_text(store, now)
    wait = round(time.monotonic() - start, 3)
    plain = re.sub(r" \[\d+\]", "", gold_text)  # il numero della mail non è una data
    gold = {"attesa": wait, "controlli": score(plain), "sottofondo": background, "mail_lette": read,
            "richieste_al_modello": len(model.calls), "token_letti": sum(c["prompt"] for c in model.calls),
            "risposta": gold_text[:1500]}
    return {"casella": i, "giusta": task.answer, "bronze": bronze, "gold": gold}


def table(results: list[dict[str, Any]]) -> str:
    def part(level: str) -> tuple[int, int, int, float]:
        rs = [r[level] for r in results]
        ok = sum(sum(c for _, c in r["controlli"]) for r in rs)
        tot = sum(len(r["controlli"]) for r in rs)
        full = sum(all(c for _, c in r["controlli"]) for r in rs)
        return ok, tot, full, sum(r["attesa"] for r in rs) / max(len(rs), 1)

    lines = ["| livello | caselle tutte giuste | controlli giusti | attesa dell'utente | lavoro in sottofondo per casella |",
             "|---|---|---|---|---|"]
    for level, name in (("bronze", "bronze (legge tutto mentre aspetti)"), ("gold", "gold (letto prima, risponde il codice)")):
        ok, tot, full, wait = part(level)
        extra = "—"
        if level == "gold":
            bg = sum(r["gold"]["sottofondo"] for r in results) / len(results)
            calls = sum(r["gold"]["richieste_al_modello"] for r in results) / len(results)
            extra = f"{bg:.0f} s ({calls:.0f} mail lette dal modello, le altre dal codice)"
        lines.append(f"| {name} | {full}/{len(results)} | {ok}/{tot} ({ok / max(tot, 1):.0%}) | {wait:.2f} s | {extra} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modello", default="qwen3.5:4b")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--quante", type=int, default=4)
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--uscita", default="livelli.json")
    args = ap.parse_args()
    model = Measured(args.modello, args.url, args.num_ctx, False)
    folder = Path(tempfile.mkdtemp(prefix="livelli-"))
    results = []
    for i in range(args.quante):
        r = run_box(i, model, folder)
        results.append(r)
        b, g = r["bronze"], r["gold"]
        print(f"casella {i}: bronze {sum(c for _, c in b['controlli'])}/{len(b['controlli'])} in {b['attesa']} s; "
              f"gold {sum(c for _, c in g['controlli'])}/{len(g['controlli'])} in {g['attesa']} s "
              f"(sottofondo {g['sottofondo']} s, {g['richieste_al_modello']} richieste)", flush=True)
        Path(args.uscita).write_text(json.dumps({"modello": args.modello, "risultati": results}, ensure_ascii=False, indent=1))
    print("\n" + table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
