"""Leggere i documenti: prima il testo vero, poi l'OCR solo dove serve.

- PDF col testo dentro (quasi tutti: bollette, avvisi, fatture): si prende il testo del file pagina per pagina
  (pdftotext). Istantaneo e senza errori.
- Pagine fatte di immagini (PDF scansionati), foto e schermate: le legge il modello di lettura (OCR).
  OvisOCR2 gira con llama.cpp (llama-mtmd-cli, in /usr/lib/aios/llama: versione Vulkan, usa la scheda video di
  qualsiasi marca, o il processore); gli altri (GLM-OCR…) con Ollama, come prima (engines.read_document).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

LLAMA_DIR = Path("/usr/lib/aios/llama")
BEST = "ovisocr2"  # il lettore di documenti di SoIA (models.py): si scarica da solo la prima volta che serve
MIN_PAGE_TEXT = 40  # sotto questi caratteri la pagina è un'immagine: si legge con l'OCR
MAX_PAGES = 20
OVIS_PROMPT = ("Extract all readable content from the image in natural human reading order and output the result as a single "
               "Markdown document. For charts or images, represent them using an HTML image tag: <img src=\"images/bbox_{left}_"
               "{top}_{right}_{bottom}.jpg\" />, where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
               "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. Transcribe all other text as standard "
               "Markdown. Preserve the original text without translation or paraphrasing.")

Run = Callable[..., tuple[int, str]]


def _run(cmd: list[str], timeout: int = 600, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def llama_tool(name: str) -> str | None:
    for p in (LLAMA_DIR / name, Path(shutil.which(name) or "")):
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def pdf_pages(path: Path, run: Run = _run) -> list[str]:
    """Il testo di ogni pagina del PDF ('' per le pagine fatte di immagini)."""
    code, out = run(["pdftotext", "-q", "-layout", "-l", str(MAX_PAGES), str(path), "-"], 120)
    if code != 0:
        return []
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def ovis(image: Path, files: list[Path], run: Run = _run) -> str:
    """OvisOCR2 su un'immagine: il testo in Markdown (tabelle in HTML)."""
    cli = llama_tool("llama-mtmd-cli")
    if cli is None or len(files) < 2:
        return ""
    model, mmproj = (next((f for f in files if "mmproj" not in f.name), files[0]), next((f for f in files if "mmproj" in f.name), files[-1]))
    env = {**os.environ, "LD_LIBRARY_PATH": f"{LLAMA_DIR}:{os.environ.get('LD_LIBRARY_PATH', '')}".rstrip(":")}
    code, out = run([cli, "-m", str(model), "--mmproj", str(mmproj), "--image", str(image), "-p", OVIS_PROMPT,
                     "--temp", "0", "-n", "4096", "-c", "8192", "-ngl", "99", "--no-warmup",
                     "-t", str(max(1, (os.cpu_count() or 2) - 1))], 900, env)
    text = out.replace("<think>", "").replace("</think>", "").strip()
    return text if code == 0 else ""


def read(path: Path, ocr: Callable[[Path], str] | None, run: Run = _run) -> str:
    """Il testo di un documento: PDF (testo vero, OCR per le pagine immagine) o immagine (OCR)."""
    path = Path(path).expanduser()
    if not path.is_file():
        return f"Non trovo {path}."
    if path.suffix.lower() != ".pdf":
        if ocr is None:
            return "Per leggere foto e scansioni serve il modello di lettura: di' «scarica il modello per leggere i documenti»."
        return ocr(path)
    pages = pdf_pages(path, run)
    if not pages:
        return "Non riesco ad aprire il PDF."
    out = []
    with tempfile.TemporaryDirectory(prefix="aios-pdf-") as tmp:
        for i, text in enumerate(pages, 1):
            if len("".join(text.split())) >= MIN_PAGE_TEXT:
                out.append(text.strip())
                continue
            if ocr is None:
                out.append(f"[pagina {i}: è un'immagine, serve il modello di lettura]")
                continue
            img = Path(tmp) / f"p{i}"
            run(["pdftoppm", "-r", "150", "-f", str(i), "-l", str(i), "-png", "-singlefile", str(path), str(img)], 120)
            png = img.with_suffix(".png")
            out.append(ocr(png).strip() if png.exists() else f"[pagina {i}: non riesco a leggerla]")
    return "\n\n---\n\n".join(f"Pagina {i}\n\n{t}" if len(out) > 1 else t for i, t in enumerate(out, 1))
