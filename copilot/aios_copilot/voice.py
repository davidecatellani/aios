"""Nova a voce: sempre in ascolto della parola «Nova», tutto sul computer.

- **Ascolto** (`aios-voce`, servizio dell'utente): il microfono passa da un riconoscitore
  Vosk ristretto a poche parole («Nova», «ehi Nova», «ok Nova»): costa pochissimo e non
  trascrive nient'altro. L'audio non viene salvato né inviato.
- **Richiesta**: sentita la parola, un piccolo suono; la frase che segue (anche detta di
  seguito: «Nova, alza il volume») è trascritta in italiano con il riconoscitore completo,
  fino a una pausa. La richiesta va alla finestra di Nova, che la mostra e la esegue.
- **Risposta**: letta ad alta voce con Piper (voce italiana naturale) o, se manca, eSpeak.
  Mentre Nova parla l'ascolto si sospende, così non si «sente» da sola.
- **Conferme a voce**: per le azioni importanti Nova chiede «Procedo?» e accetta «sì» / «no»
  detti a voce (oppure i pulsanti).
- **Batteria**: con la batteria quasi finita l'ascolto si sospende (energy.py), e riprende in
  carica. «Smetti di ascoltare» / «ascoltami» lo spengono e lo riaccendono.

Modelli: /usr/share/aios/voce (immagine AIOS) oppure ~/.local/share/aios/voce.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

RATE = 16000
CHUNK = 3200  # 0,1 s di audio a 16 bit
WAKE_WORDS = ("nova", "ehi nova", "ok nova", "hey nova")
WAKE_GRAMMAR = json.dumps(list(WAKE_WORDS) + ["[unk]"])
YES = re.compile(r"\b(?:s[iì]|certo|procedi|vai|conferma|ok|okay|va bene|fallo)\b")
NO = re.compile(r"\b(?:no|annulla|lascia stare|ferma|non farlo|aspetta)\b")


def voice_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "voce"
    return [Path(os.environ["AIOS_VOCE"])] if os.environ.get("AIOS_VOCE") else [home, Path("/usr/share/aios/voce")]


def find_model(kind: str) -> Path | None:
    """kind: «vosk» (cartella del modello di riconoscimento) o «piper» (voce .onnx)."""
    for base in voice_dirs():
        if kind == "vosk":
            found = sorted(p for p in base.glob("vosk-model*") if p.is_dir())
        else:
            found = sorted(base.glob("*.onnx"))
        if found:
            return found[0]
    return None


def speaking_flag() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "aios-nova-parla"


# --- testo ---------------------------------------------------------------------------------------------
def strip_wake(text: str) -> str:
    """«nova alza il volume» → «alza il volume»."""
    low = text.strip()
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        if low.lower().startswith(w):
            return low[len(w):].lstrip(" ,.")
    return low


def heard_wake(text: str) -> bool:
    return any(re.search(rf"\b{w}\b", text.lower()) for w in WAKE_WORDS)


def yes_no(text: str) -> bool | None:
    low = text.lower()
    if NO.search(low):
        return False
    if YES.search(low):
        return True
    return None


def for_speech(text: str, limit: int = 600) -> str:
    """Il testo da leggere ad alta voce: niente emoji, simboli, indirizzi lunghi o elenchi infiniti."""
    text = re.sub(r"https?://\S+", "un link", text)
    text = re.sub(r"[`*_#>|]", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", text)
    text = text.replace("«", "").replace("»", "").replace("•", ",")
    lines = [l.strip(" -") for l in text.splitlines() if l.strip(" -")]
    if len(lines) > 6:
        lines = lines[:5] + [f"e altre {len(lines) - 5} righe: le trovi sullo schermo."]
    text = ". ".join(l.rstrip(".") for l in lines)
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[-1] not in ".!?…":
        text += "."
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "… il resto è sullo schermo."


# --- audio ---------------------------------------------------------------------------------------------
def capture_command(which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    if which("pw-record"):
        return ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"]
    if which("parecord"):
        return ["parecord", "--raw", f"--rate={RATE}", "--channels=1", "--format=s16le"]
    if which("arecord"):
        return ["arecord", "-q", "-t", "raw", "-r", str(RATE), "-c", "1", "-f", "S16_LE"]
    return None


def play_command(path: Path, which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    for prog in ("pw-play", "paplay", "aplay"):
        if which(prog):
            return [prog, str(path)]
    return None


def speak(text: str, which: Callable[[str], str | None] = shutil.which,
          run: Callable[..., Any] = subprocess.run) -> bool:
    """Legge il testo ad alta voce (Piper, altrimenti eSpeak). Sospende l'ascolto mentre parla."""
    text = for_speech(text)
    if not text:
        return False
    flag = speaking_flag()
    try:
        flag.touch()
        voice = find_model("piper")
        piper = which("piper") or (str(Path("/usr/lib/aios/piper/piper")) if Path("/usr/lib/aios/piper/piper").exists() else None)
        if piper and voice:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav = Path(tmp.name)
            try:
                run([piper, "--model", str(voice), "--output_file", str(wav)], input=text, text=True,
                    capture_output=True, timeout=60)
                play = play_command(wav, which)
                if play and wav.stat().st_size > 44:
                    run(play, capture_output=True, timeout=120)
                    return True
            finally:
                wav.unlink(missing_ok=True)
        for prog in ("espeak-ng", "espeak"):
            if which(prog):
                run([prog, "-v", "it", "-s", "165", text], capture_output=True, timeout=120)
                return True
        return False
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        time.sleep(0.3)  # l'eco della stanza
        flag.unlink(missing_ok=True)


def chime(which: Callable[[str], str | None] = shutil.which) -> None:
    sound = Path("/usr/share/sounds/freedesktop/stereo/message-new-instant.oga")
    if sound.exists() and which("pw-play"):
        subprocess.Popen(["pw-play", str(sound)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def audio_chunks(cmd: list[str]) -> Iterator[bytes]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            data = proc.stdout.read(CHUNK)
            if not data:
                return
            yield data
    finally:
        proc.kill()


# --- riconoscimento --------------------------------------------------------------------------------------
class Ears:
    """Parola di attivazione e trascrizione (Vosk). `recognizer(grammar)` per i test."""

    def __init__(self, recognizer: Callable[[str | None], Any] | None = None):
        if recognizer is None:
            from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore

            SetLogLevel(-1)
            path = find_model("vosk")
            if path is None:
                raise RuntimeError("manca il modello vocale italiano (vosk-model-small-it)")
            model = Model(str(path))
            recognizer = lambda grammar: KaldiRecognizer(model, RATE, grammar) if grammar else KaldiRecognizer(model, RATE)  # noqa: E731
        self.new = recognizer

    def wait_for_wake(self, chunks: Iterator[bytes], recent: collections.deque) -> bool:
        rec = self.new(WAKE_GRAMMAR)
        for data in chunks:
            recent.append(data)
            if speaking_flag().exists():
                continue  # Nova sta parlando
            done = rec.AcceptWaveform(data)
            text = json.loads(rec.Result() if done else rec.PartialResult()).get("text" if done else "partial", "")
            if heard_wake(text):
                return True
            if done:
                rec = self.new(WAKE_GRAMMAR)
        return False

    def transcribe(self, chunks: Iterator[bytes], recent: collections.deque | None = None,
                   max_seconds: float = 12.0, silence_start: float = 4.0) -> str:
        """Fino alla prima pausa dopo la frase (o al tempo massimo)."""
        rec = self.new(None)
        for data in list(recent or []):  # la frase detta di seguito alla parola di attivazione
            rec.AcceptWaveform(data)
        spoken = False
        started = time.monotonic()
        for data in chunks:
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "")
                if strip_wake(text):
                    return strip_wake(text)
            elif json.loads(rec.PartialResult()).get("partial"):
                spoken = True
            elapsed = time.monotonic() - started
            if elapsed > max_seconds or (not spoken and elapsed > silence_start):
                break
        return strip_wake(json.loads(rec.FinalResult()).get("text", ""))


def deliver(text: str, run: Callable[..., Any] = subprocess.run) -> bool:
    """Passa la richiesta alla finestra di Nova (la apre se serve)."""
    exe = shutil.which("aios-copilot") or "aios-copilot"
    try:
        run([exe, "--voce", text], timeout=30, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def listen_yes_no(seconds: float = 8.0, ears: Ears | None = None,
                  chunks: Iterator[bytes] | None = None) -> bool | None:
    """«Procedo?» → sì / no / None (nessuna risposta chiara)."""
    try:
        ears = ears or Ears()
        cmd = capture_command()
        if chunks is None and cmd is None:
            return None
        text = ears.transcribe(chunks or audio_chunks(cmd), None, max_seconds=seconds, silence_start=seconds)
    except Exception:
        return None
    return yes_no(text)


# --- servizio ------------------------------------------------------------------------------------------
def state_file() -> Path:
    from .privacy import private_dir

    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "voce.json"


def listening_enabled() -> bool:
    try:
        return bool(json.loads(state_file().read_text()).get("ascolto", True))
    except (OSError, ValueError):
        return True


def set_listening(on: bool) -> None:
    state_file().write_text(json.dumps({"ascolto": on}))


def energy_allows() -> bool:
    try:
        from .energy import EnergyBrain

        return EnergyBrain().decide().mode != "riserva"
    except Exception:
        return True


def serve(ears: Ears | None = None, send: Callable[[str], bool] = deliver) -> int:
    cmd = capture_command()
    if cmd is None:
        print("Nessun programma per il microfono (pw-record, parecord o arecord).", file=sys.stderr)
        return 1
    ears = ears or Ears()
    while True:
        if not listening_enabled() or not energy_allows():
            time.sleep(30)
            continue
        recent: collections.deque = collections.deque(maxlen=15)  # 1,5 s
        chunks = audio_chunks(cmd)
        try:
            if not ears.wait_for_wake(chunks, recent):
                time.sleep(2)  # microfono non disponibile: si riprova
                continue
            chime()
            text = ears.transcribe(chunks, recent)
        finally:
            chunks.close()
        if text:
            send(text)
        else:
            threading.Thread(target=speak, args=("Dimmi pure.",), daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv) or ["servizio"]
    if args[0] == "servizio":
        return serve()
    if args[0] == "di" and len(args) > 1:
        return 0 if speak(" ".join(args[1:])) else 1
    if args[0] in ("accendi", "spegni"):
        set_listening(args[0] == "accendi")
        print("Ascolto " + ("acceso." if args[0] == "accendi" else "spento."))
        return 0
    print("aios-voce [servizio | di TESTO | accendi | spegni]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
