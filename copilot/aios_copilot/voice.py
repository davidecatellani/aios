"""Nova a voce: sempre in ascolto della parola «Nova», tutto sul computer.

- **Ascolto** (`aios-voce`, servizio dell'utente): il microfono passa da un riconoscitore
  Vosk ristretto a poche parole («Nova», «ehi Nova», «ok Nova»): costa pochissimo e non
  trascrive nient'altro. L'audio non viene salvato né inviato.
- **Richiesta**: sentita la parola, un piccolo suono; la frase che segue (anche detta di
  seguito: «Nova, alza il volume») è trascritta in italiano con il riconoscitore completo,
  fino a una pausa. La richiesta va alla finestra di Nova, che la mostra e la esegue.
- **Risposta**: letta ad alta voce con Kokoro (voci italiane naturali, kokoro.py), o Piper, o eSpeak.
  Mentre Nova parla l'ascolto si sospende, così non si «sente» da sola.
- **Conferme a voce**: per le azioni importanti Nova chiede «Procedo?» e accetta «sì» / «no»
  detti a voce (oppure i pulsanti).
- **Batteria**: con la batteria quasi finita l'ascolto si sospende (energy.py), e riprende in
  carica. «Smetti di ascoltare» / «ascoltami» lo spengono e lo riaccendono.

Modelli: /usr/share/aios/voce (immagine AIOS) oppure ~/.local/share/aios/voce. La frase intera, una volta
finita, la trascrive Parakeet (parakeet.py) se c'è: molti meno errori di Vosk, che resta il «guardiano».
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
import time
from pathlib import Path
from typing import Any, Callable, Iterator

RATE = 16000
CHUNK = 3200  # 0,1 s di audio a 16 bit
# Sotto questo livello (stanza in silenzio) la parola di attivazione non si cerca: il riconoscitore
# lavora solo quando c'è un suono, e a batteria la CPU resta ferma quasi sempre.
SILENCE_RMS = 260
HANGOVER = 12  # dopo un suono si continua ad ascoltare per 1,2 s
WAKE_WORDS = ("nova", "ehi nova", "ok nova", "hey nova")
# Le parole che suonano come «Nova» stanno nella grammatica come parole diverse: così il riconoscitore
# non è costretto a scambiarle per il nome («nove», «nuova», «no va», «nonna»…).
WAKE_DECOYS = ("nove", "nuova", "nuovo", "nuove", "novanta", "novella", "nona", "nonna", "noi", "no", "va", "mova",
               "lava", "nave", "nevi", "bova", "boa", "rova", "prova", "trova", "cova", "ova", "uova", "nova scotia")
WAKE_GRAMMAR = json.dumps(list(WAKE_WORDS) + list(WAKE_DECOYS) + ["[unk]"])
WAKE_LEAD = {"ehi", "ok", "hey", "eh", "allora"}  # possono precedere il nome senza che sia «a metà frase»
YES = re.compile(r"\b(?:s[iì]|certo|procedi|vai|conferma|ok|okay|va bene|fallo)\b")
NO = re.compile(r"\b(?:no|annulla|lascia stare|ferma|non farlo|aspetta)\b")


def voice_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "voce"
    return [Path(os.environ["AIOS_VOCE"])] if os.environ.get("AIOS_VOCE") else [home, Path("/usr/share/aios/voce")]


VOICE_NAMES = {"it_IT-paola-medium": "Paola", "it_IT-riccardo-x_low": "Riccardo"}


def voice_choice_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "voce.json"


KOKORO_PREFIX = "kokoro-"
# chi aveva una voce di Piper passa una volta alla voce naturale dello stesso genere (kokoro.py)
PIPER_TO_KOKORO = {"it_IT-paola-medium": "kokoro-if_sara", "it_IT-riccardo-x_low": "kokoro-im_nicola"}


def available_voices() -> list[dict[str, str]]:
    """Le voci di Nova presenti sul computer: prima quelle naturali (Kokoro), poi quelle di Piper (.onnx)."""
    seen: dict[str, dict[str, str]] = {}
    try:
        from . import kokoro

        if kokoro.available():
            for vid, name in kokoro.NAMES.items():
                seen[KOKORO_PREFIX + vid] = {"id": KOKORO_PREFIX + vid, "nome": name}
    except Exception:
        pass
    for base in voice_dirs():
        for f in sorted(base.glob("*.onnx")):
            seen.setdefault(f.stem, {"id": f.stem, "nome": VOICE_NAMES.get(f.stem, f.stem.split("-")[1].title()
                                                                        if "-" in f.stem else f.stem)})
    return list(seen.values())


def chosen_voice() -> str:
    try:
        saved = json.loads(voice_choice_file().read_text())
        choice, moved = saved.get("voce", ""), saved.get("naturale", False)
    except (OSError, ValueError, AttributeError):
        choice, moved = "", False
    ids = [v["id"] for v in available_voices()]
    if not moved and PIPER_TO_KOKORO.get(choice) in ids:  # una volta sola: poi la scelta dell'utente resta
        choice = PIPER_TO_KOKORO[choice]
        try:
            voice_choice_file().write_text(json.dumps({"voce": choice, "naturale": True}))
        except OSError:
            pass
    return choice if choice in ids else (ids[0] if ids else "")


def set_voice(voice_id: str) -> bool:
    if voice_id not in [v["id"] for v in available_voices()]:
        return False
    f = voice_choice_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"voce": voice_id, "naturale": True}))
    return True


def voice_file() -> Path | None:
    """Il file della voce di Piper scelta dall'utente (o la prima disponibile)."""
    choice = chosen_voice()
    if choice.startswith(KOKORO_PREFIX):
        choice = next((p for p, k in PIPER_TO_KOKORO.items() if k == choice), "")
    for base in voice_dirs():
        p = base / f"{choice}.onnx"
        if choice and p.is_file():
            return p
    return find_model("piper")


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
    """«Nova» all'inizio di quello che si sta dicendo (dopo una pausa), non in mezzo a una frase."""
    words = text.lower().split()
    if "nova" not in words:
        return False
    before = words[:words.index("nova")]
    return all(w in WAKE_LEAD for w in before)


def after_wake(text: str) -> str | None:
    """La richiesta dopo il nome, se il nome c'è davvero tra le prime parole della trascrizione completa;
    None se non c'è (era un falso allarme: la frase non era per Nova)."""
    words = text.strip().split()
    low = [re.sub(r"[^\w]", "", w.lower()) for w in words]
    for i, w in enumerate(low[:4]):
        if w == "nova":
            return " ".join(words[i + 1:]).lstrip(" ,.")
    return None


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
CLEAN_MIC = "aios_mic_pulito"  # microfono senza eco né rumori (PipeWire, pipewire.conf.d/50-aios-microfono.conf)


def clean_mic_available(run: Callable[..., Any] = subprocess.run, which: Callable[[str], str | None] = shutil.which) -> bool:
    """C'è il microfono «pulito»? Toglie dall'ascolto quello che suonano gli altoparlanti (un film, la musica)."""
    if not which("pactl"):
        return False
    try:
        out = run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return CLEAN_MIC in out


def capture_command(which: Callable[[str], str | None] = shutil.which,
                    clean: Callable[[], bool] | None = None) -> list[str] | None:
    if which("pw-record"):
        target = ["--target", CLEAN_MIC] if (clean or (lambda: clean_mic_available(which=which)))() else []
        return ["pw-record", *target, "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"]
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
        chosen = chosen_voice()
        if chosen.startswith(KOKORO_PREFIX):
            try:
                from . import kokoro

                def play_one(wav: Path) -> None:
                    cmd = play_command(wav, which)
                    if cmd:
                        run(cmd, capture_output=True, timeout=120)

                if kokoro.speak(text, chosen[len(KOKORO_PREFIX):], play_one):
                    return True
            except Exception:
                pass  # se Kokoro non va, parla Piper
        voice = voice_file()
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


def record(seconds: float, cmd: list[str] | None = None) -> bytes:
    """Qualche secondo di microfono (16 bit mono, 16 kHz): per imparare la voce dell'utente."""
    cmd = cmd or capture_command()
    if cmd is None:
        raise RuntimeError("microfono non disponibile")
    pcm = b""
    chunks = audio_chunks(cmd)
    try:
        for data in chunks:
            pcm += data
            if len(pcm) >= int(seconds * RATE * 2):
                break
    finally:
        chunks.close()
    return pcm


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


def loudness(data: bytes) -> float:
    """Volume medio (RMS) di un pezzo di audio a 16 bit, campionato ogni 4 valori: costa pochissimo."""
    import array
    import math

    samples = array.array("h", data[: len(data) - len(data) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    picked = samples[::4]
    return math.sqrt(sum(x * x for x in picked) / len(picked)) if picked else 0.0


# --- riconoscimento --------------------------------------------------------------------------------------
class Ears:
    """Parola di attivazione e trascrizione (Vosk). `recognizer(grammar)` per i test."""

    def __init__(self, recognizer: Callable[[str | None], Any] | None = None,
                 accept: Callable[[list[float] | None], bool] | None = None,
                 fine: Callable[[bytes], str] | None = None,
                 voices: Callable[[Any], list[tuple[float, float, int]]] | None = None):
        # la trascrizione fine della frase intera (parakeet.py), se c'è; Vosk resta per accorgersi della voce.
        # `voices(audio) → [(inizio, fine, persona)]`: chi parla nella frase (parlanti.py), per tenere la voce principale
        self.fine = fine
        self.voices = voices
        if recognizer is None and fine is None:
            try:
                from . import parakeet

                if parakeet.available():
                    self.fine = parakeet.shared().transcribe
            except Exception:
                self.fine = None
        if recognizer is None and voices is None:
            try:
                from . import parlanti

                if parlanti.available():
                    self.voices = parlanti.shared().turns
            except Exception:
                self.voices = None
        if recognizer is None:
            from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore

            SetLogLevel(-1)
            path = find_model("vosk")
            if path is None:
                raise RuntimeError("manca il modello vocale italiano (vosk-model-small-it)")
            model = Model(str(path))
            spk = None
            try:  # impronta della voce (voiceprint.py): chi sta parlando
                from vosk import SpkModel  # type: ignore

                from .voiceprint import spk_model_dir

                spk_dir = spk_model_dir()
                spk = SpkModel(str(spk_dir)) if spk_dir else None
            except Exception:
                spk = None

            def recognizer(grammar: str | None) -> Any:
                if grammar:
                    return KaldiRecognizer(model, RATE, grammar)
                rec = KaldiRecognizer(model, RATE)
                if spk is not None:
                    rec.SetSpkModel(spk)
                return rec
        self.new = recognizer
        if accept is None:
            from .voiceprint import accepted as accept
        self.accept = accept

    def refine(self, rough: str, audio: list[bytes]) -> str:
        """La frase trascritta da Parakeet al posto di quella di Vosk (se Parakeet c'è e capisce qualcosa)."""
        if not rough or not audio or self.fine is None:
            return rough
        from .giochi import active as playing

        if playing():
            return rough  # durante un gioco niente modelli in più: basta Vosk
        pcm = b"".join(audio)
        try:
            pcm = self.main_voice(pcm)
            better = self.fine(pcm).strip()
        except Exception:
            return rough
        return better[:-1].strip() if better.endswith(".") else (better or rough)

    def main_voice(self, pcm: bytes) -> bytes:
        """Con più voci nella frase (TV, ospiti) si tiene quella che parla di più (parlanti.py), se c'è."""
        if self.voices is None:
            return pcm
        import numpy as np

        from . import parlanti

        samples = parlanti.from_pcm(pcm)
        kept = parlanti.main_voice(samples, self.voices(samples))
        return (np.clip(kept, -1, 1) * 32767).astype(np.int16).tobytes()

    def next_utterance(self, chunks: Iterator[bytes], recent: collections.deque,
                       max_seconds: float = 15.0) -> str | None:
        """La prossima frase detta nella stanza, trascritta per intero (senza parola di attivazione).

        In silenzio il riconoscitore non lavora (soglia sul volume). → il testo, "" se non si è capito
        niente o la voce non è di una persona conosciuta (se l'utente ha scelto «solo la mia voce»),
        None se il microfono si è chiuso."""
        for data in chunks:
            recent.append(data)
            if speaking_flag().exists() or loudness(data) < SILENCE_RMS:
                continue  # Nova sta parlando, o silenzio
            rec = self.new(None)
            audio = list(recent)[-6:]  # l'inizio della frase, appena prima del suono forte
            for old in audio:
                rec.AcceptWaveform(old)
            started, quiet, voice_vector = time.monotonic(), 0, None
            for more in chunks:
                audio.append(more)
                if rec.AcceptWaveform(more):
                    result = json.loads(rec.Result())
                    voice_vector = result.get("spk") or voice_vector
                    text = result.get("text", "").strip()
                    return self.refine(text, audio) if text and self.accept(voice_vector) else ""
                quiet = quiet + 1 if loudness(more) < SILENCE_RMS else 0
                if quiet > HANGOVER or time.monotonic() - started > max_seconds:
                    break
            result = json.loads(rec.FinalResult())
            voice_vector = result.get("spk") or voice_vector
            text = result.get("text", "").strip()
            return self.refine(text, audio) if text and self.accept(voice_vector) else ""
        return None

    def wait_for_wake(self, chunks: Iterator[bytes], recent: collections.deque) -> bool:
        rec = self.new(WAKE_GRAMMAR)
        awake = 0  # pezzi ancora da ascoltare dopo l'ultimo suono
        for data in chunks:
            recent.append(data)
            if speaking_flag().exists():
                continue  # Nova sta parlando
            if loudness(data) >= SILENCE_RMS:
                if not awake and len(recent) > 1:
                    rec.AcceptWaveform(recent[-2])  # l'inizio della parola, appena prima del suono forte
                awake = HANGOVER
            elif awake:
                awake -= 1
            else:
                continue  # silenzio: niente lavoro per il riconoscitore
            done = rec.AcceptWaveform(data)
            text = json.loads(rec.Result() if done else rec.PartialResult()).get("text" if done else "partial", "")
            if heard_wake(text):
                return True
            if done:
                rec = self.new(WAKE_GRAMMAR)
        return False

    def transcribe(self, chunks: Iterator[bytes], recent: collections.deque | None = None,
                   max_seconds: float = 12.0, silence_start: float = 4.0, require_wake: bool = True) -> str | None:
        """Fino alla prima pausa dopo la frase (o al tempo massimo). → la richiesta, "" se c'era solo il
        nome, None se non era per Nova (nome non confermato, o voce che Nova non conosce)."""
        rec = self.new(None)
        audio = list(recent or [])
        for data in audio:  # la frase detta di seguito alla parola di attivazione
            rec.AcceptWaveform(data)
        spoken = False
        started = time.monotonic()
        voice_vector = None

        def request_of(text: str) -> str | None:
            fine = self.refine(text, audio)  # Parakeet: meno errori; se perde il nome si tiene la richiesta di Vosk
            if require_wake:
                return after_wake(fine) if after_wake(fine) is not None else after_wake(text)
            return strip_wake(fine)

        for data in chunks:
            audio.append(data)
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                voice_vector = result.get("spk") or voice_vector
                text = result.get("text", "")
                if text and after_wake(text) is None and require_wake:
                    return None  # la trascrizione vera non comincia con «Nova»: non era per lei
                if after_wake(text) if require_wake else strip_wake(text):
                    request = request_of(text)
                    return request if self.accept(voice_vector) else None
            elif json.loads(rec.PartialResult()).get("partial"):
                spoken = True
            elapsed = time.monotonic() - started
            if elapsed > max_seconds or (not spoken and elapsed > silence_start):
                break
        result = json.loads(rec.FinalResult())
        voice_vector = result.get("spk") or voice_vector
        request = request_of(result.get("text", "")) if result.get("text", "").strip() else (
            None if require_wake else "")
        # una voce che Nova non conosce (il film, un ospite) se l'utente ha scelto «solo la mia voce»
        return request if request is not None and self.accept(voice_vector) else None


def deliver(text: str, run: Callable[..., Any] = subprocess.run) -> bool:
    """Passa la richiesta a Nova: il pannello della shell di AIOS, o la finestra di Nova in altre sessioni."""
    in_shell = "AIOS" in os.environ.get("XDG_CURRENT_DESKTOP", "") and shutil.which("aios-shell")
    exe = shutil.which("aios-shell") if in_shell else (shutil.which("aios-copilot") or "aios-copilot")
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
        text = ears.transcribe(chunks or audio_chunks(cmd), None, max_seconds=seconds, silence_start=seconds,
                               require_wake=False)
    except Exception:
        return None
    return yes_no(text or "")


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


# Inizio tipico di una richiesta al computer (usato quando il modello che decide non risponde)
COMMAND_LIKE = re.compile(r"(?i)^\s*(?:nova\b|apri|aprimi|chiudi|cerca|cercami|trova|trovami|mostra|mostrami|fammi\s+vedere|"
                          r"metti|mettimi|alza|abbassa|accendi|spegni|ricordami|segna|segnati|scrivi|leggi|leggimi|"
                          r"riproduci|ferma|pausa|installa|aggiorna|blocca|che\s+ore|che\s+tempo|quanto\s+manca|"
                          r"dimmi|riprendi|riapri|ingrandisci|riduci|rimpicciolisci|collega|chiama|manda|invia)\b")


def addressed_to_pc(text: str, ask: Callable[[str], tuple[str, float] | None] | None = None) -> str:
    """La frase sentita è davvero per il computer? → "si", "no" o "forse" (allora Nova chiede conferma).

    Il modello decisionale (Tev1) distingue «Nova, metti la musica» da un pezzo di dialogo di un film o
    di una chiacchierata. Senza il modello si risponde: meglio che ignorare l'utente."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < 2 and not re.search(r"(?i)\b(?:stop|basta|grazie|pausa|avanti|indietro|annulla|sì|si|no)\b", text):
        return "no"  # una parola sola a caso
    ask = ask or _ask_addressed
    try:
        verdict = ask(text)
    except Exception:
        return "si"
    if verdict is None:  # senza il modello che decide: solo ciò che sembra chiaramente un comando
        return "si" if COMMAND_LIKE.search(text) else "no"
    choice, confidence = verdict
    if confidence >= 0.7:
        return "si" if choice == "richiesta" else "no"
    return "forse"


def _ask_addressed(text: str) -> tuple[str, float] | None:
    from .smistatore import Smistatore

    judge = Smistatore([])
    return judge._ask(text, "destinatario", ADDRESSED_QUESTION, ADDRESSED_CRITERIA)


# la domanda «è per Nova?» per il modello decisionale (uguale in uso e in addestramento, addestramento/dati_laya.py)
ADDRESSED_QUESTION = "Was this sentence, heard by the microphone after the word Nova, meant for the computer assistant?"
ADDRESSED_CRITERIA = {"richiesta": "A request or question to the computer assistant: do something, open, play, "
                                   "search, remind, answer a question",
                      "altro": "Not for the assistant: people talking to each other, TV or film dialogue, "
                               "song lyrics, a sentence fragment or random words"}


def energy_allows() -> bool:
    try:
        from .energy import EnergyBrain

        return EnergyBrain().decide().mode != "riserva"
    except Exception:
        return True


def serve(ears: Ears | None = None, send: Callable[[str], bool] = deliver) -> int:
    """Sempre in ascolto, senza parola di attivazione: ogni frase detta nella stanza viene trascritta
    e Nova decide se è per lei (addressed_to_pc). Nel dubbio chiede «Dicevi a me?».
    «Nova, …» all'inizio della frase vale come richiesta sicura."""
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
            text = ears.next_utterance(chunks, recent)
            if text is None:
                time.sleep(2)  # microfono non disponibile: si riprova
                continue
            if not text:
                continue
            request = after_wake(text)
            if request == "":  # solo «Nova»: un suono e si ascolta la richiesta
                chime()
                request = ears.transcribe(chunks, None, max_seconds=8.0, require_wake=False) or ""
        finally:
            chunks.close()  # dopo ogni frase si riapre: niente audio vecchio rimasto in coda
        if request is not None:  # chiamata per nome: è per Nova
            text, verdict = request, "si" if request else "no"
        else:
            verdict = addressed_to_pc(text)
        if verdict == "forse":  # nel dubbio si chiede: «Dicevi a me?»
            speak(f"Dicevi a me? {text[:80]}?")
            verdict = "si" if listen_yes_no(6.0, ears) else "no"
        if verdict == "si":
            chime()
            send(text)


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
