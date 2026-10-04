"""I modelli installati, collegati alle funzioni del sistema.

    testo        → il copilota (llm.OllamaClient legge il modello da models.json)
    vista        → describe_image / look_at_screen (Ollama, modelli multimodali; o il nucleo, che vede)
    lettura      → read_scanned_document (DeepSeek-OCR; o il nucleo con l'adattatore «documenti»)
    dettatura    → transcribe (whisper.cpp)
    voce         → speak (piper)
    significato  → livello 1 multilingue e ricerca nei file (calibrato a riposo)
    immagini     → generate_image (stable-diffusion.cpp)

Ogni funzione è disponibile solo se il modello è installato E il programma che lo
esegue è presente (nell'immagine di AIOS lo sono tutti).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable

from .models import find_model, load_config, models_dir

ENGINES = {
    "dettatura": ("whisper-cli", "whisper-cpp", "whisper"),
    "voce": ("piper",),
    "immagini": ("sd", "sd-cli"),
}
PLAYERS = (["pw-play"], ["paplay"], ["aplay", "-q"])
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_IMAGE_BYTES = 12 * 2**20
NUCLEO = "nucleo"  # il «modello» per vista e lettura quando le fa il nucleo (llama-server con mmproj)


def engine(capability: str, which: Callable[[str], str | None] = shutil.which) -> str | None:
    return next((p for p in ENGINES.get(capability, ()) if which(p)), None)


def model_files(name: str) -> list[Path]:
    model = find_model(name)
    if model is None:
        return []
    return [models_dir() / name / url.rsplit("/", 1)[-1] for url in model.urls]


def available(which: Callable[[str], str | None] = shutil.which) -> dict[str, str]:
    """Capacità pronte all'uso: {capacità: modello}."""
    config = load_config()
    ready = {}
    for cap, name in config.items():
        if cap.startswith("_") or not name:
            continue
        model = find_model(name)
        if model is None:
            continue
        if model.engine == "file":
            files = model_files(name)
            if not files or not all(f.exists() for f in files) or engine(cap, which) is None:
                continue
        ready[cap] = name
    # il nucleo (nucleo.py) vede le immagini: se nessun altro modello è scelto, guarda e legge lui
    if ("vista" not in ready or "lettura" not in ready) and os.environ.get("AIOS_NUCLEO", "") != "spento":
        try:
            from .nucleo import Nucleo

            n = Nucleo()
            if n.vision():
                ready.setdefault("vista", NUCLEO)
                if "documenti" in n.adapters():
                    ready.setdefault("lettura", NUCLEO)
        except Exception:
            pass
    return ready


def _ollama(path: str, payload: dict, timeout: int = 600) -> dict:
    base = os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434").rstrip("/")
    req = urllib.request.Request(f"{base}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# --- vista --------------------------------------------------------------------------------
READ_DOCUMENT = "<|grounding|>Convert the document to markdown."


def describe_image(path: Path, question: str, model: str, chat: Callable[[str, dict], dict] = _ollama) -> str:
    path = Path(path).expanduser()
    if path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
        return f"{path} non è un'immagine che posso aprire."
    if path.stat().st_size > MAX_IMAGE_BYTES:
        return "L'immagine è troppo grande (oltre 12 MB)."
    if model == NUCLEO:
        from .galleria import small_jpeg
        from .nucleo import Nucleo

        small = small_jpeg(path, 800)
        if small is None:
            return "Non riesco ad aprire l'immagine."
        try:
            n = Nucleo()
            if question == READ_DOCUMENT:
                return n.read_document(small, fields=False) or "Non sono riuscito a leggere il documento."
            return n.see(small, question or "Descrivi l'immagine in italiano.") or "Non sono riuscito a interpretare l'immagine."
        except Exception as exc:
            return f"Non riesco a guardare l'immagine adesso ({exc.__class__.__name__})."
    image = base64.b64encode(path.read_bytes()).decode()
    reply = chat("/api/chat", {"model": model, "stream": False, "keep_alive": "10m",
                               "messages": [{"role": "user", "content": question or "Descrivi l'immagine in italiano.",
                                             "images": [image]}]})
    return reply.get("message", {}).get("content", "").strip() or "Non sono riuscito a interpretare l'immagine."


def capture_screen(run: Callable[[list[str]], int] = lambda c: subprocess.run(c, capture_output=True).returncode,
                   which: Callable[[str], str | None] = shutil.which) -> Path | None:
    target = Path(tempfile.mkstemp(prefix="aios-schermo-", suffix=".png")[1])
    for cmd in (["grim", str(target)], ["gnome-screenshot", "-f", str(target)], ["spectacle", "-b", "-n", "-f", "-o", str(target)]):
        if which(cmd[0]) and run(cmd) == 0 and target.exists() and target.stat().st_size:
            return target
    target.unlink(missing_ok=True)
    return None


# --- dettatura ------------------------------------------------------------------------------


def transcribe(audio: Path, model: str, run: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
               which: Callable[[str], str | None] = shutil.which) -> str:
    run = run or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=600))
    binary = engine("dettatura", which)
    files = model_files(model)
    if binary is None or not files:
        return "La dettatura non è ancora installata."
    wav = Path(tempfile.mkstemp(suffix=".wav")[1])
    try:
        if which("ffmpeg"):  # whisper.cpp vuole WAV 16 kHz mono
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
        else:
            shutil.copyfile(audio, wav)
        out = run([binary, "-m", str(files[0]), "-f", str(wav), "-l", "auto", "-nt", "-np"])
        return " ".join(line.strip() for line in (out.stdout or "").splitlines() if line.strip()) or "Non ho sentito niente."
    finally:
        wav.unlink(missing_ok=True)


def record(seconds: int, run: Callable[[list[str]], int] = lambda c: subprocess.run(c).returncode,
           which: Callable[[str], str | None] = shutil.which) -> Path | None:
    target = Path(tempfile.mkstemp(prefix="aios-voce-", suffix=".wav")[1])
    for cmd in (["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), str(target)],
                ["timeout", str(seconds), "pw-record", "--rate", "16000", "--channels", "1", str(target)]):
        if which(cmd[0]) and (run(cmd) in (0, 124)) and target.stat().st_size > 44:
            return target
    target.unlink(missing_ok=True)
    return None


# --- voce -----------------------------------------------------------------------------------


def speak(text: str, model: str, run: Callable[..., subprocess.CompletedProcess] | None = None,
          which: Callable[[str], str | None] = shutil.which) -> str:
    run = run or (lambda c, **kw: subprocess.run(c, capture_output=True, **kw))
    binary = engine("voce", which)
    files = [f for f in model_files(model) if f.suffix == ".onnx"]
    if binary is None or not files:
        return "La voce non è ancora installata."
    wav = Path(tempfile.mkstemp(suffix=".wav")[1])
    try:
        run([binary, "--model", str(files[0]), "--output_file", str(wav)], input=text[:4000].encode())
        player = next((p for p in PLAYERS if which(p[0])), None)
        if player is None:
            return "Non trovo un modo per riprodurre l'audio."
        run([*player, str(wav)])
        return "🔊"
    finally:
        wav.unlink(missing_ok=True)


# --- immagini ---------------------------------------------------------------------------------


def generate_image(prompt: str, model: str, out_dir: Path, run: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
                   which: Callable[[str], str | None] = shutil.which) -> Path | None:
    run = run or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=1800))
    binary = engine("immagini", which)
    files = model_files(model)
    if binary is None or not files:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"AIOS {time.strftime('%Y-%m-%d %H-%M-%S')}.png"
    steps = "1" if "turbo" in model else "20"  # i modelli "turbo" bastano pochi passaggi
    run([binary, "-m", str(files[0]), "-p", prompt, "-o", str(target), "--steps", steps, "--cfg-scale", "1.0" if steps == "1" else "7"])
    return target if target.exists() else None
