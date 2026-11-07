"""La prova agentica di Nova: compiti in più passi in un «mondo finto», con il vero ciclo di Nova (agent.Agent).

    python addestramento/agentica.py --modello qwen3.5:4b [--ragiona] [--url http://localhost:11434]

Il mondo ha bollette, polizze e ricevute nei file, mail, agenda e qualche pagina web; gli strumenti sono quelli
di Nova (stessi nomi, descrizioni e campi), ma leggono e scrivono nel mondo finto. Ogni compito ha un controllo
automatico sul risultato (la risposta, una mail mandata, un promemoria creato…), così modelli diversi si
confrontano sugli stessi compiti in italiano. Niente scorciatoie né smistatore: si misura il modello.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

OGGI = "lunedì 5 ottobre 2026, ore 10:00"
H = "/home/utente"

FILES = {
    f"{H}/Documenti/Bollette/luce-2026-07.pdf": "Enel Energia - Bolletta luce luglio 2026. Periodo 1-31 luglio. Totale da pagare: 84,20 euro. "
                                                 "Scadenza: 20 agosto 2026. Stato: pagata. Contratto IT001E12345678.",
    f"{H}/Documenti/Bollette/luce-2026-08.pdf": "Enel Energia - Bolletta luce agosto 2026. Totale da pagare: 91,10 euro. Scadenza: 20 settembre 2026. "
                                                 "Stato: pagata. Contratto IT001E12345678.",
    f"{H}/Documenti/Bollette/luce-2026-09.pdf": "Enel Energia - Bolletta luce settembre 2026. Totale da pagare: 78,50 euro. Scadenza: 20 ottobre 2026. "
                                                 "Stato: da pagare. Contratto IT001E12345678.",
    f"{H}/Documenti/Bollette/gas-2026-07.pdf": "Italgas Vendita - Bolletta gas luglio 2026. Importo: 32,00 euro. Scadenza 25 agosto 2026. Pagata.",
    f"{H}/Documenti/Bollette/gas-2026-08.pdf": "Italgas Vendita - Bolletta gas agosto 2026. Importo: 28,40 euro. Scadenza 25 settembre 2026. Pagata.",
    f"{H}/Documenti/Bollette/gas-2026-09.pdf": "Italgas Vendita - Bolletta gas settembre 2026. Importo: 41,60 euro. Scadenza 25 ottobre 2026. Da pagare.",
    f"{H}/Documenti/Bollette/internet-2026.pdf": "Fastweb - Contratto internet casa: canone mensile 29,90 euro, addebito automatico il 1° di ogni mese.",
    f"{H}/Documenti/Auto/polizza-rca.pdf": "Polizza RCA auto targa GH123KL - Compagnia Assicurazioni Sicure. Premio annuo 412,00 euro. "
                                            "Decorrenza 15 novembre 2025, scadenza 15 novembre 2026.",
    f"{H}/Documenti/Auto/bollo-2026.pdf": "Avviso bollo auto targa GH123KL: importo 186,00 euro, da pagare entro il 31 ottobre 2026.",
    f"{H}/Documenti/Casa/tari-2026-ricevuta.pdf": "Ricevuta di pagamento TARI 2026 (tassa sui rifiuti), Comune di Bologna: 238,00 euro pagati il 30 maggio 2026.",
    f"{H}/Documenti/Salute/dieta.pdf": "Dieta settimanale: lunedì pranzo pasta integrale e verdure, cena pesce al forno…",
    f"{H}/Documenti/Lavoro/commercialista.txt": "Studio Bianchi commercialista - dott. Paolo Bianchi - paolo.bianchi@studiobianchi.it - tel 051 123456",
}

MAILS = [
    {"id": 1, "da": "Marco Rossi <marco.rossi@gmail.com>", "oggetto": "Ci vediamo domani?", "testo": "Ciao! Domani alle 10 riusciamo a vederci al bar per parlare del viaggio?", "letta": False, "giorno": "2026-10-04"},
    {"id": 2, "da": "Laura Verdi (capo) <laura.verdi@acme-srl.com>", "oggetto": "URGENTE: consegna report entro oggi", "testo": "Il cliente vuole il report trimestrale entro le 17 di oggi, mi raccomando.", "letta": False, "giorno": "2026-10-05"},
    {"id": 3, "da": "Zalando <newsletter@zalando.it>", "oggetto": "Saldi d'autunno: -40%", "testo": "Approfitta dei saldi.", "letta": False, "giorno": "2026-10-05"},
    {"id": 4, "da": "Zalando <offerte@zalando.it>", "oggetto": "Nuovi arrivi per te", "testo": "Scopri la nuova collezione.", "letta": True, "giorno": "2026-10-02"},
    {"id": 5, "da": "Giulia Neri <giulia.neri@acme-srl.com>", "oggetto": "Riunione progetto", "testo": "Ti propongo la riunione di progetto giovedì 8 ottobre alle 15 in sala blu. Va bene?", "letta": False, "giorno": "2026-10-03"},
    {"id": 6, "da": "Studio dentistico Sorriso <info@sorriso.it>", "oggetto": "Conferma appuntamento", "testo": "Le confermiamo l'appuntamento di domani 6 ottobre alle 9:00. Porti la tessera sanitaria.", "letta": True, "giorno": "2026-10-03"},
    {"id": 7, "da": "Amazon.it <conferma-ordine@amazon.it>", "oggetto": "Il tuo ordine è stato spedito", "testo": "Ordine del 12 settembre 2026: cuffie bluetooth. Totale 23,99 euro.", "letta": True, "giorno": "2026-09-12"},
    {"id": 8, "da": "Amazon.it <conferma-ordine@amazon.it>", "oggetto": "Conferma ordine", "testo": "Ordine del 27 settembre 2026: zaino da trekking. Totale 45,50 euro.", "letta": True, "giorno": "2026-09-27"},
    {"id": 9, "da": "Amazon.it <conferma-ordine@amazon.it>", "oggetto": "Conferma ordine", "testo": "Ordine del 2 ottobre 2026: libro. Totale 14,90 euro.", "letta": True, "giorno": "2026-10-02"},
    {"id": 10, "da": "Marco Rossi <marco.rossi@gmail.com>", "oggetto": "Foto vacanze", "testo": "Ti mando le foto di Rimini!", "letta": True, "giorno": "2026-10-01"},
    {"id": 11, "da": "Marco Rossi <marco.rossi@gmail.com>", "oggetto": "Re: partita", "testo": "Sabato giochiamo alle 18.", "letta": True, "giorno": "2026-10-02"},
]
CONTACTS = {"marco": "marco.rossi@gmail.com", "sara": "sara.colombo@libero.it", "luca": "luca.ferrari@gmail.com",
            "giulia": "giulia.neri@acme-srl.com", "laura": "laura.verdi@acme-srl.com", "commercialista": "paolo.bianchi@studiobianchi.it",
            "paolo bianchi": "paolo.bianchi@studiobianchi.it"}
AGENDA = [
    {"titolo": "Dentista (Studio Sorriso)", "quando": "2026-10-06T09:00", "tipo": "appuntamento"},
    {"titolo": "Appuntamento con il commercialista Bianchi", "quando": "2026-10-09T11:00", "tipo": "appuntamento"},
    {"titolo": "Palestra", "quando": "2026-10-07T19:00", "tipo": "appuntamento"},
]
WEB = {
    "treni": [("Italo - Milano Roma da 29,90 €", "https://www.italotreno.it/milano-roma", "Biglietti Milano-Roma a partire da 29,90 euro."),
              ("Trenitalia Frecciarossa Milano Roma", "https://www.trenitalia.com/frecciarossa-milano-roma", "Frecciarossa Milano-Roma da 34,90 euro.")],
    "inflazione": [("Istat: inflazione a settembre al 1,6%", "https://www.ansa.it/economia/inflazione-settembre", "ANSA - L'Istat stima l'inflazione all'1,6% su base annua."),
                   ("Prezzi, l'inflazione rallenta", "https://www.corriere.it/economia/inflazione", "Corriere della Sera - I prezzi rallentano a settembre.")],
}
PAGES = {
    "https://www.italotreno.it/milano-roma": "Italo: Milano Centrale - Roma Termini, offerta Low Cost 29,90 euro, durata 2h59.",
    "https://www.trenitalia.com/frecciarossa-milano-roma": "Frecciarossa Milano - Roma: tariffa Economy 34,90 euro, durata 2h55.",
    "https://www.ansa.it/economia/inflazione-settembre": "ANSA. Secondo la stima preliminare dell'Istat, a settembre l'inflazione è all'1,6%.",
    "https://www.corriere.it/economia/inflazione": "Corriere della Sera. L'inflazione rallenta all'1,6% secondo l'Istat.",
}


def norm(s: Any) -> str:
    s = unicodedata.normalize("NFKD", str(s).lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def words(q: str) -> list[str]:
    return [w[:5] for w in re.findall(r"[a-z0-9]+", norm(q)) if len(w) >= 3 and w not in {"del", "della", "delle", "dei", "che",
            "per", "con", "una", "mio", "mia", "miei", "mie", "gli", "nel", "nei", "sul", "tra", "fra", "the", "sono", "come"}]


@dataclass
class World:
    files: dict[str, str] = field(default_factory=lambda: dict(FILES))
    mails: list[dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(MAILS))
    agenda: list[dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(AGENDA))
    sent: list[dict[str, Any]] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    reminders: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def tools(self) -> dict[str, Callable[..., str]]:
        w = self

        def score(text: str, q: str) -> int:
            t = norm(text)
            return sum(1 for x in words(q) if x in t)

        def search_files(query: str = "") -> str:
            hits = sorted(((score(p + " " + t, query), p) for p, t in w.files.items()), reverse=True)
            hits = [p for s, p in hits if s > 0][:8]
            return "\n".join(f"{p} — {w.files[p][:90]}…" for p in hits) or "Nessun file trovato."

        def read_file(path: str = "") -> str:
            p = path if path in w.files else next((x for x in w.files if x.endswith(path.split("/")[-1])), None)
            return w.files[p] if p else f"Non trovo il file {path}."

        def show_document(query: str = "") -> str:
            best = max(w.files, key=lambda p: score(p + " " + w.files[p], query))
            return f"Ho aperto {best}.\n{w.files[best]}" if score(best + w.files[best], query) else "Non trovo il documento."

        def line(m: dict[str, Any]) -> str:
            return f"[{m['id']}] {m['giorno']} da {m['da']}: {m['oggetto']}" + ("" if m["letta"] else " (non letta)")

        def mail_overview() -> str:
            unread = [m for m in w.mails if not m["letta"]]
            return f"{len(unread)} non lette:\n" + "\n".join(line(m) for m in unread) + "\nUltime lette:\n" + \
                "\n".join(line(m) for m in w.mails if m["letta"])[:900]

        def search_mail(query: str = "") -> str:
            hits = [m for m in w.mails if score(f"{m['da']} {m['oggetto']} {m['testo']}", query)]
            return "\n".join(line(m) for m in hits) or "Nessuna mail trovata."

        def read_mail(mail_id: Any = "") -> str:
            m = next((m for m in w.mails if str(m["id"]) == str(mail_id).strip("[] ")), None)
            if m is None:
                return f"Non trovo la mail {mail_id}."
            m["letta"] = True
            return f"[{m['id']}] Da: {m['da']}\nData: {m['giorno']}\nOggetto: {m['oggetto']}\n\n{m['testo']}"

        def categorize_mail(who: str = "", category: str = "") -> str:
            w.moved.append((who, category))
            return f"Fatto: le mail di {who} vanno in «{category}», anche quelle future."

        def send_email(to: str = "", subject: str = "", body: str = "", reply_to: Any = "") -> str:
            addr = CONTACTS.get(norm(to).strip(), to)
            w.sent.append({"to": addr, "subject": subject, "body": body, "reply_to": reply_to})
            return f"Mail inviata a {addr}."

        def add_reminder(what: str = "", when: str = "", repeat: str = "") -> str:
            w.reminders.append({"what": what, "when": when})
            return f"Promemoria creato: {what}" + (f" per {when}" if when else "") + "."

        def add_event(title: str = "", when: str = "", location: str = "", repeat: str = "") -> str:
            w.agenda.append({"titolo": title, "quando": when, "luogo": location, "tipo": "nuovo"})
            return f"Appuntamento aggiunto: {title} il {when}" + (f" ({location})" if location else "") + "."

        def list_agenda(period: str = "") -> str:
            day = {"oggi": "2026-10-05", "domani": "2026-10-06"}.get(norm(period).strip(), period[:10])
            items = [a for a in w.agenda if norm(period) in ("settimana", "") or a["quando"].startswith(day)]
            return "\n".join(f"{a['quando']} {a['titolo']}" for a in items) or "Nessun impegno."

        def delete_agenda_item(query: str = "") -> str:
            hit = next((a for a in w.agenda if score(a["titolo"], query)), None)
            if hit is None:
                return "Non trovo quell'impegno."
            w.agenda.remove(hit)
            w.deleted.append(hit["titolo"])
            return f"Eliminato: {hit['titolo']} ({hit['quando']})."

        def search_web(query: str = "") -> str:
            key = "treni" if re.search(r"tren|bigliett|milano|roma|italo|frecc", norm(query)) else \
                "inflazione" if "infla" in norm(query) or "prezz" in norm(query) else ""
            return "\n\n".join(f"[{i}] {t}\n{u}\n{s}" for i, (t, u, s) in enumerate(WEB.get(key, []), 1)) or "Nessun risultato."

        def read_webpage(url: str = "") -> str:
            return PAGES.get(url.strip(), "Pagina non trovata.")

        def current_time() -> str:
            return f"Adesso è {OGGI}."

        return {k: v for k, v in locals().items() if callable(v) and k not in ("score", "line", "w")}


@dataclass
class Task:
    text: str
    domains: list[str]
    check: Callable[[World, str], bool]
    tipo: str = "multi"


def num(answer: str, value: str) -> bool:
    """Il numero nella risposta, scritto con la virgola o col punto, con o senza zeri finali."""
    a = norm(answer).replace(" ", "").replace("\u202f", "")
    v = value.replace(".", ",")
    variants = {v, v.replace(",", "."), v.rstrip("0").rstrip(",") if "," in v else v,
                v.replace(",", ".").rstrip("0").rstrip(".") if "," in v else v}
    # il numero intero, non un pezzo di un altro (41 non deve valere dentro 412)
    return any(x and re.search(rf"(?<![0-9]){re.escape(x)}(?![0-9])", a) for x in variants)


def has(text: str, *needles: str) -> bool:
    return all(norm(n) in norm(text) for n in needles)


TASKS = [
    Task("quanto ho speso di luce e gas negli ultimi tre mesi?", ["file"], lambda w, a: num(a, "355,80")),
    Task("quanto spendo in media al mese di utenze tra luce, gas e internet?", ["file"], lambda w, a: num(a, "148,50")),
    Task("qual è la bolletta più cara degli ultimi mesi?", ["file"], lambda w, a: num(a, "91,10")),
    Task("ricordami di pagare la bolletta della luce il giorno della scadenza", ["file", "agenda"],
         lambda w, a: any("2026-10-20" in str(r["when"]) for r in w.reminders)),
    Task("quanti giorni mancano alla scadenza dell'assicurazione dell'auto?", ["file", "computer"], lambda w, a: num(a, "41")),
    Task("ho già pagato la tassa sui rifiuti quest'anno?", ["file"], lambda w, a: has(a, "pagat") and (has(a, "30 maggio") or has(a, "238"))),
    Task("qual è la differenza tra la bolletta della luce di luglio e quella di settembre?", ["file"], lambda w, a: num(a, "5,70")),
    Task("segna come promemoria le scadenze di ottobre che trovi nei documenti", ["file", "agenda"],
         lambda w, a: {r["when"][:10] for r in w.reminders} >= {"2026-10-20", "2026-10-31"}),
    Task("leggi le mail non lette e dimmi se c'è qualcosa di urgente", ["posta"], lambda w, a: has(a, "report") or has(a, "laura")),
    Task("sposta le mail di Zalando nelle newsletter", ["posta"],
         lambda w, a: any("zalando" in norm(x) and c == "newsletter" for x, c in w.moved)),
    Task("rispondi a Marco che domani alle 10 per me va bene", ["posta"],
         lambda w, a: any(s["to"] == CONTACTS["marco"] and "10" in s["body"] for s in w.sent)),
    Task("metti in agenda la riunione che mi ha proposto Giulia", ["posta", "agenda"],
         lambda w, a: any(x["tipo"] == "nuovo" and str(x["quando"]).startswith("2026-10-08T15") for x in w.agenda)),
    Task("cosa ho domani e c'è qualche mail che lo riguarda?", ["agenda", "posta"], lambda w, a: has(a, "dentist") and has(a, "tessera")),
    Task("chi mi ha scritto più mail negli ultimi giorni?", ["posta"], lambda w, a: has(a, "marco")),
    Task("quanto ho speso su Amazon a settembre?", ["posta"], lambda w, a: num(a, "69,49")),
    Task("trova il numero del contratto della luce e mandalo a Luca per mail", ["file", "posta"],
         lambda w, a: any(s["to"] == CONTACTS["luca"] and "IT001E12345678" in s["body"] for s in w.sent)),
    Task("manda a Sara quanto ho speso in tutto di bollette a settembre", ["file", "posta"],
         lambda w, a: any(s["to"] == CONTACTS["sara"] and num(s["body"], "150") for s in w.sent)),
    Task("cancella l'appuntamento con il commercialista e scrivigli una mail per avvisarlo", ["agenda", "posta", "file"],
         lambda w, a: any("commercialista" in norm(d) for d in w.deleted) and any(s["to"] == CONTACTS["commercialista"] for s in w.sent)),
    Task("cerca il biglietto del treno più economico da Milano a Roma", ["web"], lambda w, a: num(a, "29,90") and has(a, "italo")),
    Task("cerca le ultime notizie sull'inflazione e dimmi da dove vengono", ["web"],
         lambda w, a: num(a, "1,6") and (has(a, "ansa") or has(a, "corriere") or has(a, "istat"))),
]


GIORNO = date(2026, 10, 5)


def make_tools(world: World, domains: list[str], real: dict[str, Any], groups: dict[str, list[str]],
               calculator: bool = True) -> list[Any]:
    from aios_copilot.tools import calcolo

    fake = world.tools()
    names = {"current_time"} | {n for d in domains for n in groups.get(d, [])}
    out = []
    if calculator:  # la calcolatrice è un conto puro: quella vera, con il giorno del mondo finto
        calc = calcolo.make_tools(today=lambda: GIORNO)[0]

        def counted(espressione: str = "", _f: Callable[..., str] = calc.func) -> str:
            world.calls.append("calculate")
            return _f(espressione)

        out.append(replace(calc, func=counted))
    for n in sorted(names):
        if n in real and n in fake:
            fn = fake[n]

            def logged(*a: Any, _fn: Callable[..., str] = fn, _n: str = n, **k: Any) -> str:
                world.calls.append(_n)
                return _fn(*a, **k)

            out.append(replace(real[n], func=logged, requires_confirmation=False))
    return out


def run(model_name: str, url: str, think: bool, only: int | None = None, bare: bool = False) -> dict[str, Any]:
    import dati
    from aios_copilot.agent import Agent
    from aios_copilot.llm import OllamaClient
    from aios_copilot.pianifica import Planner

    groups, real = dati.nova_tools()
    rows = []
    for i, t in enumerate(TASKS if only is None else TASKS[:only]):
        world = World()
        model = OllamaClient(url=url, model=model_name, timeout=900)
        agent = Agent(model, make_tools(world, t.domains, real, groups, calculator=not bare), confirm=lambda *a, **k: True,
                      max_steps=12, percorso=(lambda _t: ("ragionamento", 1.0)) if think else None,
                      pianificatore=None if bare else Planner(model, today=lambda: GIORNO))
        start = time.monotonic()
        try:
            answer = agent.ask(f"{t.text}\n\n(Oggi è {OGGI}.)")
        except Exception as exc:  # il modello non risponde o sbaglia formato: compito fallito
            answer = f"ERRORE: {exc}"
        took = time.monotonic() - start
        ok = False
        try:
            ok = bool(t.check(world, answer))
        except Exception:
            pass
        rows.append({"compito": t.text, "ok": ok, "secondi": round(took, 1), "strumenti": world.calls, "risposta": answer[:400]})
        print(f"{'✓' if ok else '✗'} {t.text} [{took:.0f}s, {len(world.calls)} strumenti: {' '.join(world.calls)}]\n   → {answer[:160]!r}", flush=True)
    good = sum(r["ok"] for r in rows)
    return {"modello": model_name, "ragiona": think, "struttura": not bare, "riusciti": good, "compiti": len(rows),
            "secondi_medi": round(sum(r["secondi"] for r in rows) / max(len(rows), 1), 1), "dettagli": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modello", required=True)
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--ragiona", action="store_true")
    ap.add_argument("--nuda", action="store_true", help="solo il modello, senza piano, verifica e calcolatrice")
    ap.add_argument("--solo", type=int, default=None, help="solo i primi N compiti (prova veloce)")
    ap.add_argument("--uscita", default="")
    args = ap.parse_args()
    res = run(args.modello, args.url, args.ragiona, args.solo, args.nuda)
    print(f"\n{res['modello']}{' (ragiona)' if res['ragiona'] else ''}: {res['riusciti']}/{res['compiti']} compiti, "
          f"{res['secondi_medi']} s in media")
    if args.uscita:
        Path(args.uscita).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
