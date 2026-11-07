"""Struttura per un modello piccolo che lavora in più passi (come fa Claude Code, ma con meno libertà).

Un modello da 4 miliardi di parametri sa usare gli strumenti, ma nei compiti lunghi perde il filo, fa i conti
a occhio e a volte chiede all'utente quello che potrebbe cercare da solo. Qui il codice fa ciò che il codice
fa meglio, e al modello resta da decidere solo dove serve davvero:

1. **Piano** (plan): prima di agire il modello scrive i passi, uno per riga («cerco le bollette», «leggo gli
   importi», «calcolo la media con calculate»), e li segue. Come la lista delle cose da fare di Claude Code.
2. **Ricerca preventiva** (prefetch): se la richiesta parla di mail, documenti o impegni, la ricerca la fa il
   sistema prima del modello e gliene mette davanti l'elenco: non deve indovinare che serve cercare.
3. **Guardie** (guard): prima di eseguire un'azione si controlla che abbia senso. Una mail che dovrebbe dire
   «quanto» ma non contiene numeri non parte; un promemoria nel passato nemmeno. Il modello riceve l'errore e
   corregge, invece di fare un danno.
4. **Verifica** (verify): prima che la risposta arrivi all'utente, controlli semplici. Ha chiesto «quanto» e
   manca il numero? Ha chiesto di mandare una mail e non è partita? Fa una domanda senza aver cercato? Il
   modello riprova, al massimo due volte.

Tutto con regole e parole italiane, niente modelli in più: costa millisecondi. Solo il piano chiede una
risposta breve al modello.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

MAX_STEPS = 6
MAX_CHECKS = 2

PLAN_PROMPT = """Sei il pianificatore di Nova, l'assistente di SoIA. Scomponi la richiesta in passi brevi da fare con gli strumenti.
Oggi è {today}.
Strumenti: {tools}

Regole:
- Al massimo {max} passi, uno per riga, numerati («1. …»). Ogni passo dice cosa fare e con quale strumento.
- I conti (somme, medie, differenze, giorni tra due date) sempre con calculate.
- Non chiedere all'utente quello che puoi trovare con gli strumenti (mail, file, agenda).
- Se basta un passo solo, scrivi un passo solo. Nient'altro oltre ai passi.

Richiesta: {text}"""

# richieste che chiedono un'azione: (parole, strumento che la fa, come dirlo)
ACTIONS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(?:ricordami|promemoria|ricordarmi|avvisami)\b"), "add_reminder", "creare il promemoria"),
    (re.compile(r"\b(?:metti|mettilo|mettila|segna|segnalo|aggiungi|fissa)\b[^.?!]*\b(?:agenda|calendario)\b"), "add_event",
     "mettere l'appuntamento in agenda"),
    (re.compile(r"\b(?:manda|mandalo|mandala|mandagli|mandale|invia|scrivi|scrivigli|scrivile|rispondi|rispondigli)\b"
                r"(?![^.?!]*\b(?:messaggio|sms|whatsapp)\b)"), "send_email", "mandare la mail"),
    (re.compile(r"\b(?:cancella|elimina|togli|annulla)\b[^.?!]*\b(?:appuntament\w*|impegn\w*|riunion\w*|agenda)\b"),
     "delete_agenda_item", "cancellare l'impegno dall'agenda"),
    (re.compile(r"\b(?:sposta|metti|archivia)\b[^.?!]*\bmail\b"), "categorize_mail", "spostare le mail"),
]
NUMBER_QUESTION = re.compile(r"\b(?:quant[oiae]|differenza|media|totale|in tutto|somma|costo|cost[ae]|spes[oa]|"
                             r"spendo|importo|giorni mancano|quanti giorni)\b")
COMPUTE = re.compile(r"\b(?:differenza|media|totale|in tutto|somma|quanto ho speso|quanto spendo|spes[oa] in tutto|"
                     r"quanti giorni|giorni mancano|percentuale)\b")
DIGIT = re.compile(r"\d")
# la richiesta riguarda la posta, i documenti o l'agenda: si cerca prima del modello
ABOUT_MAIL = re.compile(r"\b(?:mail|email|e-mail|posta|scritto|scritta|risposto|proposto|proposta|mandato|ordin\w*|amazon|"
                        r"rispondi\w*)\b")
ABOUT_FILES = re.compile(r"\b(?:bollett\w*|document\w*|file|ricevut\w*|polizz\w*|assicurazion\w*|contratt\w*|scadenz\w*|"
                         r"fattur\w*|tass\w*|bollo|utenz\w*|luce|gas|spes[oa]|speso|spendo)\b")
ABOUT_AGENDA = re.compile(r"\b(?:agenda|appuntament\w*|impegn\w*|riunion\w*|domani|dopodomani|settimana|calendario)\b")
PREFETCH_CHARS = 1200


def wants_plan(text: str) -> bool:
    """Un piano serve ai compiti in più passi, non a «che ore sono» o «ciao»."""
    low = text.lower()
    if len(low.split()) < 4:
        return False
    return bool(COMPUTE.search(low) or ABOUT_FILES.search(low) or ABOUT_MAIL.search(low) or ABOUT_AGENDA.search(low)
                or any(p.search(low) for p, _, _ in ACTIONS) or re.search(r"\b(?:e poi|e dopo|e mandal|e scrivi|e dimmi)\b", low))


def parse_plan(text: str) -> list[str]:
    steps = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:\d+[.)]|[-*•])\s*(.+?)\s*$", line)
        if m and len(m.group(1)) > 3:
            steps.append(m.group(1)[:200])
    return steps[:MAX_STEPS]


def make_plan(model: Any, text: str, tool_names: list[str], today: date | None = None) -> list[str]:
    prompt = PLAN_PROMPT.format(today=(today or date.today()).isoformat(), tools=", ".join(tool_names), max=MAX_STEPS,
                                text=text)
    think = getattr(model, "think", None)
    try:
        if think is not None:
            model.think = False  # il piano è corto: niente ragionamento lungo
        reply = model.chat([{"role": "user", "content": prompt}], [])
    finally:
        if think is not None:
            model.think = think
    return parse_plan(reply.get("content") or "")


def plan_note(steps: list[str]) -> str:
    lines = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return ("(Piano da seguire, un passo alla volta, con gli strumenti; alla fine rispondi all'utente con il "
            f"risultato, breve:\n{lines})")


def prefetch(text: str, tools: dict[str, Any]) -> list[tuple[str, str]]:
    """Le ricerche che servono quasi certamente, fatte subito: [(cosa, risultato)]."""
    low = text.lower()
    out = []

    def run(name: str, **args: Any) -> None:
        tool = tools.get(name)
        if tool is None:
            return
        try:
            result = str(tool.func(**args))
        except Exception:
            return
        if result and not result.startswith(("Nessun", "Non trovo", "Errore")) and all(r != result[:PREFETCH_CHARS] for _, r in out):
            out.append((name, result[:PREFETCH_CHARS]))

    if ABOUT_MAIL.search(low) and "search_mail" in tools:
        run("search_mail", query=text)
    if ABOUT_FILES.search(low) and "search_files" in tools:
        run("search_files", query=text)
    if ABOUT_AGENDA.search(low) and "list_agenda" in tools:
        run("list_agenda", period="")
        # «cosa ho domani e c'è qualche mail che lo riguarda?»: le mail di ciascun impegno, cercate per titolo
        if ABOUT_MAIL.search(low) and "search_mail" in tools and out and out[-1][0] == "list_agenda":
            for line in out[-1][1].splitlines()[:4]:
                title = re.sub(r"^\S*\d\S*\s+", "", line).strip()  # via la data
                words = [w for w in re.findall(r"[A-Za-zÀ-ù]{4,}", title)][:3]
                if words:
                    run("search_mail", query=" ".join(words))
    return out


def prefetch_note(found: list[tuple[str, str]]) -> str:
    parts = "\n\n".join(f"[{name}]\n{result}" for name, result in found)
    return ("(Ho già cercato per te: qui sotto quello che potrebbe servire. Leggi gli elementi che ti servono "
            f"(read_mail, read_file) invece di chiedere all'utente.\n{parts})")


def expected_actions(text: str, available: set[str]) -> list[tuple[str, str]]:
    low = text.lower()
    return [(tool, what) for pattern, tool, what in ACTIONS if tool in available and pattern.search(low)]


def _when(value: Any, today: date) -> date | None:
    from .tools.calcolo import CalcError, parse_date

    try:
        return parse_date(str(value), today)
    except (CalcError, ValueError):
        return None


def guard(text: str, name: str, args: dict[str, Any], today: date | None = None) -> str | None:
    """Un'azione che di sicuro non è quella chiesta: → l'errore da dare al modello invece di eseguirla."""
    today = today or date.today()
    low = text.lower()
    if name == "send_email":
        body = str(args.get("body", ""))
        if NUMBER_QUESTION.search(low) and not DIGIT.search(body):
            return ("Errore: mail NON inviata. Il testo deve contenere il dato chiesto dall'utente (l'importo o il "
                    "numero), ma non c'è nessun numero. Trovalo, calcolalo con calculate se serve, e riprova.")
        if not body.strip():
            return "Errore: mail NON inviata, il testo è vuoto."
    if name in ("add_reminder", "add_event"):
        when = _when(args.get("when", ""), today)
        if when is not None and when < today:
            return (f"Errore: {when.isoformat()} è già passato (oggi è {today.isoformat()}). Controlla la data giusta "
                    "(per una bolletta: quella ancora da pagare) e riprova.")
    return None


def verify(text: str, answer: str, called: list[str], available: set[str], checks_done: int) -> str | None:
    """Prima di rispondere: manca qualcosa? → la nota da dare al modello, o None se va bene."""
    if checks_done >= MAX_CHECKS:
        return None
    low = text.lower()
    done = set(called)
    missing = [(tool, what) for tool, what in expected_actions(text, available) if tool not in done]
    if missing:
        tool, what = missing[0]
        return f"Non hai ancora fatto quello che l'utente ha chiesto: {what}. Fallo adesso con {tool}, poi rispondi."
    if NUMBER_QUESTION.search(low) and not DIGIT.search(answer) and called:
        return "L'utente ha chiesto un numero, ma la risposta non lo contiene. Trova i dati, fai il conto con calculate e rispondi col numero."
    if COMPUTE.search(low) and "calculate" in available and "calculate" not in done and DIGIT.search(answer):
        return ("Controlla il conto con calculate (somme, medie, differenze o giorni tra date: non a mente), poi "
                "rispondi con il risultato giusto.")
    if not called and answer.rstrip().endswith("?") and available & {"search_mail", "search_files", "list_agenda"}:
        return ("Non fare domande all'utente prima di aver cercato: usa gli strumenti (search_mail, search_files, "
                "list_agenda) per trovare quello che serve, poi agisci.")
    return None


class Planner:
    """Il pacchetto per l'agente: piano, ricerca preventiva, guardie e verifica."""

    def __init__(self, model: Any, today: Callable[[], date] = date.today, plan: bool = True, fetch: bool = True):
        self.model, self.today, self.plan_on, self.fetch_on = model, today, plan, fetch

    def prepare(self, text: str, tools: dict[str, Any], emit: Callable[[str, dict[str, Any]], None]) -> tuple[str, list[str]]:
        """→ (note da aggiungere alla richiesta, testi privati letti)."""
        notes, private = [], []
        if self.fetch_on:
            found = prefetch(text, tools)
            if found:
                emit("prefetch", {"tools": [n for n, _ in found]})
                notes.append(prefetch_note(found))
                private += [r for n, r in found if getattr(tools.get(n), "reads_private", False)]
        if self.plan_on and wants_plan(text):
            try:
                steps = make_plan(self.model, text, sorted(tools), self.today())
            except Exception:
                steps = []
            if len(steps) >= 2:
                emit("plan", {"steps": steps})
                notes.append(plan_note(steps))
        return "\n\n".join(notes), private

    def guard(self, text: str, name: str, args: dict[str, Any]) -> str | None:
        return guard(text, name, args, self.today())

    def verify(self, text: str, answer: str, called: list[str], available: set[str], checks_done: int) -> str | None:
        return verify(text, answer, called, available, checks_done)
