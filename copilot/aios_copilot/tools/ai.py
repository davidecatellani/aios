"""Modelli AI del dispositivo: proposte, installazione e funzioni (vista, voce, dettatura, immagini)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .. import engines, memory
from ..fastpath import Intent, normalize
from ..hardware import Device
from ..models import CAPABILITIES, Queue, best_for, describe_proposals, propose
from ..xdg import resolve_folder
from .base import Runner, Tool, params

CAP_WORDS = {"lettura": "lettura", "ocr": "lettura", "vista": "vista", "immagini": "immagini", "foto": "vista", "dettatura": "dettatura",
             "riconoscimento vocale": "dettatura", "voce": "voce", "lettura": "voce", "testo": "testo",
             "significato": "significato", "lingue": "significato", "modello più potente": "testo",
             "creare immagini": "immagini", "generazione di immagini": "immagini"}


def make_management_tools(get_device: Callable[[], Device], installed: Callable[[], list[str]],
                          get_queue: Callable[[], Queue], runner: Runner | None = None) -> list[Tool]:
    def suggest_models() -> str:
        device = get_device()
        return describe_proposals(device, propose(device, installed()))

    def install_models(which: str = "tutti") -> str:
        device = get_device()
        proposals = propose(device, installed())
        wanted = which.lower().strip()
        if wanted not in ("tutti", "tutto", "all", ""):
            caps = {CAP_WORDS.get(w.strip(), w.strip()) for w in re.split(r",|\s+e\s+", wanted)}
            proposals = [p for p in proposals if p.capability in caps]
            if not proposals:
                best = [best_for(device, c) for c in caps if c in CAPABILITIES]
                if any(best):
                    return "È già installato il modello migliore per questo dispositivo."
                return f"Su questo dispositivo non posso installare: {which}."
        queue = get_queue()
        added = [p for p in proposals if queue.add(p.model)]
        if not added:
            return "Niente di nuovo da scaricare: è già tutto in coda o installato."
        total = sum(p.model.size_gb for p in added)
        names = ", ".join(f"{p.capability} ({p.model.name})" for p in added)
        return (f"In coda: {names}. Circa {total:.1f} GB: li scarico quando il computer è a riposo e in carica, "
                "e li collego al sistema appena pronti. «stato dei modelli» per vedere a che punto sono.")

    def models_status() -> str:
        queue = get_queue()
        lines = [f"Attivi: " + (", ".join(f"{c} → {m}" for c, m in engines.available().items()) or "nessuno oltre al minimo")]
        for d in queue.items:
            pct = f" {100 * d.done_bytes // d.total_bytes}%" if d.total_bytes else ""
            lines.append(f"  {d.capability}: {d.name} — {d.status}{pct}{(': ' + d.error) if d.error else ''}")
        return "\n".join(lines)

    def restore(capability: str = "testo") -> str:
        from ..learning import restore_model

        return restore_model(CAP_WORDS.get(capability.lower().strip(), capability.lower().strip()))

    def memory_status() -> str:
        return memory.describe(get_device())

    def optimize_memory() -> str:
        plan = memory.plan_for(get_device())
        errors = memory.apply_plan(plan, runner)
        if errors:
            return "Non sono riuscito a completare tutto (serve la password di amministratore):\n" + "\n".join(errors)
        return (f"Fatto: RAM compressa da {plan.zram_mb / 1024:.1f} GB con zstd, memoria della conversazione del modello a "
                f"{'4' if plan.kv_cache == 'q4_0' else '8'} bit, contesto di {plan.context} token. "
                "Ora i modelli AI hanno più spazio e le app ferme occupano meno.")

    return [
        Tool("memory_status", "Mostra quanta memoria c'è e se la compressione (RAM compressa, memoria del modello) è attiva.",
             params(), memory_status),
        Tool("optimize_memory", "Attiva la compressione della memoria adatta al dispositivo (RAM compressa zram, memoria "
             "della conversazione del modello a 8/4 bit). Serve la password di amministratore.", params(), optimize_memory,
             requires_confirmation=True),
        Tool("restore_model", "Torna al modello AI usato prima per una capacità (es. dopo un aggiornamento che non piace).",
             params(capability=("Capacità", list(CAPABILITIES))), restore),
        Tool("suggest_models", "Analizza il dispositivo e propone i modelli AI gratuiti più completi che può usare.",
             params(), suggest_models),
        Tool("install_models", "Mette in coda lo scaricamento dei modelli proposti ('tutti' o capacità: vista, dettatura, "
             "voce, testo, significato, immagini).", params(which="Cosa installare"), install_models,
             requires_confirmation=True),
        Tool("models_status", "Mostra i modelli attivi e gli scaricamenti in corso.", params(), models_status),
    ]


def make_capability_tools(ready: dict[str, str]) -> list[Tool]:
    """Solo le funzioni davvero disponibili: il modello non vede strumenti che non può usare."""
    tools = []
    if "vista" in ready:
        model = ready["vista"]

        def describe_image(path: str, question: str = "") -> str:
            return engines.describe_image(Path(path), question, model)

        def look_at_screen(question: str = "") -> str:
            shot = engines.capture_screen()
            if shot is None:
                return "Non riesco a catturare lo schermo."
            try:
                return engines.describe_image(shot, question or "Descrivi cosa c'è sullo schermo.", model)
            finally:
                shot.unlink(missing_ok=True)

        tools += [
            Tool("describe_image", "Guarda un'immagine (foto, scansione, schermata) e risponde: descrizione, testo letto, domande.",
                 params(["path"], path="Percorso dell'immagine", question="Cosa chiedere"), describe_image, reads_private=True),
            Tool("look_at_screen", "Guarda lo schermo dell'utente e risponde (es. «cosa dice questo errore?»).",
                 params([], question="Cosa chiedere"), look_at_screen, reads_private=True),
        ]
    # leggere un documento: il testo vero dei PDF, l'OCR solo per foto, scansioni e pagine fatte di immagini (lettore.py)
    from .. import lettore

    ocr_model = ready.get("lettura")

    def want_best_reader() -> bool:
        """Il lettore migliore (OvisOCR2) si scarica da solo la prima volta che serve; → True se è in arrivo."""
        try:
            from ..models import Queue, find_model

            model = find_model(lettore.BEST)
            return model is not None and (Queue().add(model) or any(d.name == model.name for d in Queue().pending()))
        except Exception:
            return False

    def ocr_or_queue(p: Path) -> str:
        if ocr_model != lettore.BEST:
            coming = want_best_reader()
            if ocr_model is None:
                return ("Questa è una foto o una scansione: per leggerla scarico il lettore di documenti (1 GB, una volta sola). "
                        "Riprova tra qualche minuto." if coming else "Per leggere foto e scansioni manca il modello di lettura.")
        return engines.read_document(Path(p), ocr_model)

    ocr = ocr_or_queue
    tools.append(Tool("read_scanned_document", "Legge un documento e ne dà il testo (markdown): PDF, bollette, contratti, "
                      "tabelle, anche fotografati o scansionati. Più preciso di describe_image per il testo.",
                      params(path="Percorso del PDF o dell'immagine"),
                      lambda path: lettore.read(Path(path), ocr), reads_private=True))
    if "voce" in ready:
        voice = ready["voce"]
        tools.append(Tool("read_aloud", "Legge un testo ad alta voce.", params(text="Testo"),
                          lambda text: engines.speak(text, voice)))
    from .. import parakeet, parlanti

    by_speaker = parakeet.available() and parlanti.available()
    if "dettatura" in ready or by_speaker:
        whisper = ready.get("dettatura", "")

        def transcribe_audio(path: str) -> str:
            # prima divisa per persona (Parakeet + chi parla), altrimenti whisper.cpp
            text = engines.transcribe_speakers(Path(path)) if by_speaker else None
            return text if text is not None else engines.transcribe(Path(path), whisper)

        tools.append(Tool("transcribe_audio", "Trascrive un file audio (riunione, messaggio vocale), divisa per persona "
                          "quando parlano in più.", params(path="Percorso del file audio"), transcribe_audio,
                          reads_private=True))
    if "immagini" in ready:
        sd = ready["immagini"]

        def create_image(prompt: str) -> str:
            out = engines.generate_image(prompt, sd, resolve_folder("PICTURES") / "SoIA")
            return f"Immagine creata: {out}" if out else "Non sono riuscito a creare l'immagine."

        tools.append(Tool("create_image", "Crea un'immagine da una descrizione (meglio in inglese, dettagliata).",
                          params(prompt="Descrizione"), create_image))
    return tools


RE_SUGGEST = re.compile(
    r"^(?:che|quali)\s+modelli\s+(?:ai\s+)?posso\s+(?:usare|installare|avere)|^(?:com'è|come è|quanto è potente)\s+il\s+mio\s+"
    r"(?:computer|pc|telefono|dispositivo)|^(?:voglio|vorrei)\s+(?:un\s+)?(?:modello|ai|intelligenza artificiale)\s+più\s+(?:potente|completo)"
    r"|^(?:posso\s+avere\s+)?modelli\s+(?:ai\s+)?migliori")
RE_INSTALL = re.compile(
    r"^(?:aggiorna|migliora)\s+(?:i\s+modelli|l'ai|l'intelligenza artificiale|il copilota|nova)$"
    r"|^(?:installa|scarica|metti|aggiungi)\s+(?:tutti\s+)?(?:i\s+)?(?:modelli\s+)?(?:consigliati|suggeriti|proposti)$"
    r"|^(?:installa|scarica)\s+(?:tutti\s+)?i\s+modelli(?:\s+(?:ai|consigliati))?$"
    r"|^(?:installa|attiva|aggiungi)\s+(?:la\s+|il\s+|le\s+)?(?P<cap>vista|dettatura|riconoscimento vocale|voce|lettura|"
    r"creare immagini|generazione di immagini|modello più potente)$")
RE_RESTORE = re.compile(r"^(?:torna|ritorna|rimetti)\s+(?:al|il)\s+modello\s+(?:di\s+)?(?:prima|precedente)")
RE_STATUS = re.compile(r"^(?:stato|a che punto sono)\s+(?:dei|i)\s+modelli|^modelli installati$")
RE_OPTIMIZE = re.compile(r"^(?:ottimizza|comprimi|libera|alleggerisci)\s+(?:la\s+)?(?:memoria|ram)$"
                         r"|^(?:attiva|abilita)\s+(?:la\s+)?(?:compressione(?:\s+della\s+memoria)?|ram\s+compressa|zram)$")
RE_MEMORY = re.compile(r"^(?:stato\s+della\s+|com'è\s+la\s+|quanta\s+)?(?:memoria|ram)(?:\s+(?:ho|c'è|libera))?$"
                       r"|^(?:la\s+)?compressione\s+(?:della\s+memoria\s+)?è\s+attiva")
RE_LOOK = re.compile(r"^(?:cosa|che cosa)\s+(?:c'è|vedi|dice)\s+(?:sullo|nello|lo)\s+schermo|^guarda\s+(?:lo\s+)?schermo")


class ModelsRouter:
    def __init__(self, ready: Callable[[], dict[str, str]] = engines.available):
        self.ready = ready

    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        if RE_SUGGEST.match(low):
            return Intent("suggest_models", {})
        m = RE_INSTALL.match(low)
        if m:
            return Intent("install_models", {"which": m.group("cap") or "tutti"})
        if RE_RESTORE.match(low):
            return Intent("restore_model", {"capability": "testo"})
        if RE_STATUS.match(low):
            return Intent("models_status", {})
        if RE_OPTIMIZE.match(low):
            return Intent("optimize_memory", {})
        if RE_MEMORY.match(low):
            return Intent("memory_status", {})
        if RE_LOOK.match(low) and "vista" in self.ready():
            return Intent("look_at_screen", {"question": text})
        return None
