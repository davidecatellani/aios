"""Le app di AIOS, dentro la shell: tutte in HTML, niente programmi a finestra per le cose di base.

File, Foto, Musica, Video, Note, il visore dei documenti e le Impostazioni sono viste della stessa
pagina (home.html + static/apps.js); qui c'è quello che serve dal sistema:

- i file dell'utente (solo dentro la cartella personale), con Range per audio e video;
- i documenti trasformati in una pagina da mostrare in un riquadro: PDF (pagine come immagini,
  pdftoppm), Word (mammoth), Excel (openpyxl), testo; per gli altri formati si dice cosa serve;
- le impostazioni: Wi-Fi e Bluetooth (nmcli, bluetoothctl), volume (wpctl), luminosità
  (brightnessctl), password, aggiornamenti, voce di Nova, spegnimento.

I programmi installati dall'utente (Firefox, giochi, app Windows…) restano finestre: le gestisce la
shell (barra, Nova), vedi shell/__init__.py e tools/windows.py.
"""

from __future__ import annotations

import hashlib
import json
import html
import mimetypes
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from ..localapp import Raw

Run = Callable[[list[str]], tuple[int, str]]

IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif", ".heic"}
AUDIO = {".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4a", ".aac"}
VIDEO = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".ogv"}
TEXT = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".py", ".sh", ".ini", ".conf", ".yaml", ".yml", ".toml"}
DOCS = {".pdf", ".docx", ".xlsx", ".xlsm", ".odt", ".ods", ".odp", ".doc", ".xls", ".ppt", ".pptx", ".rtf"}
FOLDERS = {"foto": ("Immagini", "Pictures"), "musica": ("Musica", "Music"), "video": ("Video", "Videos"),
           "note": ("Documenti/Note", "Documents/Notes")}
MAX_LIST = 2000


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if path.is_dir():
        return "cartella"
    for name, exts in (("immagine", IMAGES), ("audio", AUDIO), ("video", VIDEO), ("testo", TEXT), ("documento", DOCS)):
        if ext in exts:
            return name
    return "altro"


# --- file dell'utente ------------------------------------------------------------------------------
def home() -> Path:
    return Path(os.environ.get("AIOS_CASA", Path.home())).resolve()


def safe_path(raw: str, base: Path | None = None) -> Path:
    """Un percorso dentro la cartella personale, o ValueError. Accetta «~/…» e percorsi relativi."""
    base = base or home()
    raw = (raw or "").strip()
    path = Path(raw).expanduser() if raw.startswith("~") else (Path(raw) if raw.startswith("/") else base / raw)
    path = path.resolve()
    path.relative_to(base)  # ValueError se esce dalla cartella personale
    return path


def photo_thumbnail(path: Path, size: int = 320) -> Path | None:
    """Una miniatura piccola di una foto (in ~/.cache/aios/miniature), per non caricare foto da 5 MB."""
    import hashlib

    try:
        st = path.stat()
    except OSError:
        return None
    key = hashlib.sha256(f"{path}:{st.st_mtime}:{size}".encode()).hexdigest()[:32]
    out = Path(os.environ.get("XDG_CACHE_HOME", home() / ".cache")) / "aios" / "miniature" / f"f-{key}.jpg"
    if out.exists():
        return out
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
        pix = pix.apply_embedded_orientation() or pix
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.savev(str(out), "jpeg", ["quality"], ["82"])
        return out
    except Exception:
        return None


def user_folder(which: str) -> Path:
    for name in FOLDERS[which]:
        p = home() / name
        if p.is_dir():
            return p
    return home() / FOLDERS[which][0]


def describe(path: Path, base: Path | None = None) -> dict[str, Any]:
    base = base or home()
    try:
        st = path.stat()
    except OSError:
        st = None
    return {"nome": path.name, "percorso": str(path.relative_to(base)), "tipo": kind_of(path),
            "dimensione": st.st_size if st and not path.is_dir() else None, "modificato": int(st.st_mtime) if st else 0}


def list_folder(path: Path) -> dict[str, Any]:
    base = home()
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:MAX_LIST]:
        if child.name.startswith("."):
            continue
        entries.append(describe(child, base))
    parent = str(path.parent.relative_to(base)) if path != base else None
    return {"cartella": str(path.relative_to(base)) if path != base else "", "nome": path.name if path != base else "Casa",
            "su": parent, "voci": entries}


def collect(folder: Path, exts: set[str], limit: int = 1000) -> list[dict[str, Any]]:
    """I file di un tipo in una cartella e sottocartelle, dal più recente."""
    found = []
    if folder.is_dir():
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if Path(f).suffix.lower() in exts and not f.startswith("."):
                    found.append(Path(root) / f)
            if len(found) > limit * 2:
                break
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return [describe(p) for p in found[:limit]]


def file_response(path: Path) -> Raw:
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if kind in ("text/html", "image/svg+xml", "application/xhtml+xml"):
        kind = "text/plain; charset=utf-8" if kind != "image/svg+xml" else kind  # mai eseguire pagine dell'utente
    # dentro un file dell'utente niente script: sandbox
    return Raw(path, kind, csp="default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox")


# --- documenti in un riquadro -------------------------------------------------------------------------------
DOC_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; media-src 'self'"
DOC_STYLE = """<style>
body{margin:0;background:#f4f7f8;color:#10232e;font:16px/1.55 "Inter","DejaVu Sans",sans-serif}
.pagina{max-width:900px;margin:24px auto;background:#fff;padding:48px 56px;border-radius:10px;box-shadow:0 6px 24px rgba(10,42,58,.10)}
.pdf{display:block;max-width:min(100%,1000px);margin:18px auto;box-shadow:0 6px 24px rgba(10,42,58,.15);background:#fff}
pre{white-space:pre-wrap;word-break:break-word;font:14px/1.5 "DejaVu Sans Mono",monospace}
table{border-collapse:collapse;font-size:14px;margin:8px 0 28px}td,th{border:1px solid #d6e2e6;padding:4px 8px;white-space:nowrap}
th{background:#eaf3f5;position:sticky;top:0}h2{margin:28px 0 8px}img{max-width:100%}
.avviso{max-width:640px;margin:80px auto;text-align:center;font-size:18px}
@media (prefers-color-scheme: dark){body{background:#0a2a3a;color:#eaf4f4}.pagina{background:#12394b}th{background:#0f4a63}td,th{border-color:#2a5566}}
</style>"""


def cache_dir() -> Path:
    d = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "aios" / "documenti"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _page(title: str, body: str) -> Raw:
    doc = f"<!doctype html><html lang=it><head><meta charset=utf-8><title>{html.escape(title)}</title>{DOC_STYLE}</head><body>{body}</body></html>"
    return Raw(doc.encode(), "text/html; charset=utf-8", csp=DOC_CSP)


def document_page(path: Path, token: str, run: Run | None = None) -> Raw:
    """Il documento come pagina HTML da mostrare nel riquadro del visore."""
    ext = path.suffix.lower()
    rel = str(path.relative_to(home()))
    try:
        if ext == ".pdf":
            return _pdf(path, token, run)
        if ext == ".docx":
            return _docx(path)
        if ext in (".xlsx", ".xlsm"):
            return _xlsx(path)
        if ext in TEXT or ext == "":
            text = path.read_text(errors="replace")[:2_000_000]
            return _page(path.name, f"<div class=pagina><pre>{html.escape(text)}</pre></div>")
        if ext in IMAGES:
            return _page(path.name, f"<img class=pdf src='/file/{_q(rel)}?t={token}' alt=''>")
    except Exception as exc:  # file rovinato o formato non riconosciuto
        return _page(path.name, f"<div class=avviso>Non riesco a mostrare «{html.escape(path.name)}»: {html.escape(str(exc)[:200])}.</div>")
    return _page(path.name, "<div class=avviso>Questo formato (" + html.escape(ext or "senza estensione") + ") non lo so ancora "
                 "mostrare qui. Chiedi a Nova: «apri " + html.escape(path.name) + " con un programma».</div>")


def _q(rel: str) -> str:
    from urllib.parse import quote

    return quote(rel)


def _pdf(path: Path, token: str, run: Run | None) -> Raw:
    st = path.stat()
    key = hashlib.sha256(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:20]
    out = cache_dir() / key
    if not any(out.glob("p-*.png")):
        out.mkdir(parents=True, exist_ok=True)
        if not shutil.which("pdftoppm"):
            raise RuntimeError("manca pdftoppm (poppler-utils)")
        cmd = ["pdftoppm", "-r", "110", "-png", "-l", "200", str(path), str(out / "p")]
        code, msg = (run or _run_long)(cmd)
        if code != 0:
            raise RuntimeError("PDF non leggibile")
    pages = sorted(out.glob("p-*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    imgs = "".join(f"<img class=pdf loading=lazy src='/doc/pagina/{key}/{p.name}?t={token}' alt='pagina {i + 1}'>"
                   for i, p in enumerate(pages))
    return _page(path.name, imgs or "<div class=avviso>Il PDF è vuoto.</div>")


def pdf_page_file(key: str, name: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{20}", key) or not re.fullmatch(r"p-\d+\.png", name):
        return None
    p = cache_dir() / key / name
    return p if p.is_file() else None


def _docx(path: Path) -> Raw:
    try:
        import mammoth  # type: ignore[import-not-found]
    except ImportError:
        return _page(path.name, "<div class=pagina><pre>" + html.escape(_docx_text(path)) + "</pre></div>")
    with path.open("rb") as f:
        result = mammoth.convert_to_html(f)
    return _page(path.name, f"<div class=pagina>{_clean_html(result.value)}</div>")


def _docx_text(path: Path) -> str:
    """Senza mammoth: il testo dei paragrafi, letto direttamente dal file .docx (è uno zip di XML)."""
    import zipfile
    from xml.etree import ElementTree

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    return "\n\n".join("".join(t.text or "" for t in p.iter(f"{ns}t")) for p in root.iter(f"{ns}p"))


def _clean_html(fragment: str) -> str:
    # mammoth produce HTML semplice; per sicurezza via script, eventi e link javascript
    fragment = re.sub(r"(?is)<\s*(script|style|iframe|object|embed)[^>]*>.*?<\s*/\s*\1\s*>", "", fragment)
    fragment = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", fragment)
    return re.sub(r"(?i)(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2", r"\1=\2#\2", fragment)


def _xlsx(path: Path) -> Raw:
    import openpyxl  # type: ignore[import-not-found]

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets[:20]:
        rows = []
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= 2000:
                rows.append("<tr><td colspan=50>… altre righe non mostrate</td></tr>")
                break
            cells = "".join(f"<{'th' if r == 0 else 'td'}>{html.escape('' if v is None else str(v))}</{'th' if r == 0 else 'td'}>"
                            for v in row[:50])
            rows.append(f"<tr>{cells}</tr>")
        parts.append(f"<h2>{html.escape(ws.title)}</h2><table>{''.join(rows)}</table>")
    wb.close()
    return _page(path.name, "<div class=pagina style='max-width:none;overflow:auto'>" + "".join(parts) + "</div>")


def _run_long(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout + proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


# --- impostazioni ----------------------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 20, input_text: str | None = None) -> tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, f"{cmd[0]} non è installato"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input_text)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def split_nmcli(line: str) -> list[str]:
    """Una riga di «nmcli -t»: campi separati da «:», con «\\:» dentro i valori."""
    parts, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def wifi_state(run: Run = _run) -> dict[str, Any]:
    code, radio = run(["nmcli", "radio", "wifi"])
    on = code == 0 and radio.strip().startswith("enabled")
    networks: dict[str, dict[str, Any]] = {}
    if on:
        code, out = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "auto"])
        for line in out.splitlines() if code == 0 else []:
            f = split_nmcli(line)
            if len(f) < 4 or not f[1]:
                continue
            net = {"nome": f[1], "segnale": int(f[2] or 0), "protetta": bool(f[3] and f[3] != "--"), "attiva": f[0] == "*"}
            old = networks.get(f[1])
            if not old or net["attiva"] or (net["segnale"] > old["segnale"] and not old["attiva"]):
                networks[f[1]] = net
    code, dev = run(["nmcli", "-t", "-f", "TYPE", "device"])
    has_card = code == 0 and any(l.strip() == "wifi" for l in dev.splitlines())
    return {"acceso": on, "scheda": has_card,
            "reti": sorted(networks.values(), key=lambda n: (not n["attiva"], -n["segnale"]))}


def wifi_connect(name: str, password: str, run: Run = _run) -> tuple[bool, str]:
    if not name or len(name) > 64 or "\n" in name:
        return False, "Nome della rete non valido."
    cmd = ["nmcli", "device", "wifi", "connect", name] + (["password", password] if password else [])
    code, out = run(cmd)
    if code == 0:
        return True, f"Collegato a «{name}»."
    if "Secrets were required" in out or "password" in out.lower():
        return False, "Password sbagliata, riprova."
    return False, "Non riesco a collegarmi: " + out.strip().splitlines()[-1][:160] if out.strip() else "Non riesco a collegarmi."


def bluetooth_state(run: Run = _run) -> dict[str, Any]:
    code, out = run(["bluetoothctl", "show"])
    if code != 0:
        return {"presente": False, "acceso": False, "dispositivi": []}
    on = "Powered: yes" in out
    devices = []
    code, paired = run(["bluetoothctl", "devices", "Paired"])
    if code != 0 or not paired.strip():
        code, paired = run(["bluetoothctl", "paired-devices"])
    for line in paired.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
        if m:
            code, info = run(["bluetoothctl", "info", m.group(1)])
            devices.append({"indirizzo": m.group(1), "nome": m.group(2), "collegato": "Connected: yes" in info})
    return {"presente": True, "acceso": on, "dispositivi": devices}


def volume_state(run: Run = _run) -> dict[str, Any]:
    code, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    m = re.search(r"Volume:\s*([\d.]+)", out) if code == 0 else None
    return {"livello": round(float(m.group(1)) * 100) if m else None, "muto": "MUTED" in out}


def brightness_state(run: Run = _run) -> dict[str, Any]:
    code, out = run(["brightnessctl", "-m", "info"])
    if code != 0 or not out.strip():
        return {"livello": None}
    f = out.strip().splitlines()[0].split(",")
    try:
        return {"livello": int(f[3].rstrip("%"))}
    except (IndexError, ValueError):
        return {"livello": None}


def change_password(old: str, new: str) -> tuple[bool, str]:
    """Cambia la password dell'utente con «passwd», come da terminale (pty: passwd vuole un terminale)."""
    if len(new) < 6:
        return False, "La nuova password deve avere almeno 6 caratteri."
    import pty
    import select
    import time

    pid, fd = pty.fork()
    if pid == 0:  # figlio
        os.execvp("passwd", ["passwd"])
    out = b""
    answers = [old, new, new]
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if re.search(rb"(?i)password[^\n]*:\s*$", out.rstrip(b" ") + b" ") and answers:
                os.write(fd, (answers.pop(0) + "\n").encode())
                out += b"\n"
    finally:
        _, status = os.waitpid(pid, 0)
        os.close(fd)
    if os.waitstatus_to_exitcode(status) == 0:
        return True, "Password cambiata."
    text = out.decode(errors="replace").lower()
    if "authentication" in text or "autenticazione" in text:
        return False, "La password attuale non è giusta."
    return False, "La nuova password non va bene (troppo semplice o simile a quella vecchia)."


def system_info() -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        info["versione"] = Path("/usr/share/aios/versione").read_text().strip()
    except OSError:
        info["versione"] = "sviluppo"
    try:
        from ..hardware import detect

        d = detect()
        info.update({"memoria_gb": round(d.ram_gb), "processore": d.cpu, "disco_libero_gb": round(d.disk_free_gb)})
        gpu = next((g for g in d.gpus if g.vendor == "nvidia"), d.gpus[0] if d.gpus else None)
        if gpu is not None:
            info["scheda_video"] = gpu.name + (f", {gpu.vram_gb:.0f} GB" if gpu.vram_gb else "")
            if gpu.vendor == "nvidia" and not Path("/proc/driver/nvidia/version").exists():
                info["scheda_video_nota"] = "Driver NVIDIA non caricato: la grafica e l'AI usano il processore"
    except Exception:
        pass
    try:
        from ..llm import OllamaClient

        info["modello"] = OllamaClient().model
    except Exception:
        pass
    return info


POWER = {"spegni": ["systemctl", "poweroff"], "riavvia": ["systemctl", "reboot"], "sospendi": ["systemctl", "suspend"],
         "blocca": ["loginctl", "lock-session"], "esci": ["sh", "-c", "hyprctl dispatch exit || labwc --exit"]}


# --- rotte della shell -----------------------------------------------------------------------------------
def register_apps(app: Any, run: Run = _run) -> None:
    """Aggiunge al server della shell le rotte delle app di AIOS."""

    def path_from(value: Any) -> Path:
        return safe_path(str(value or ""))

    def guarded(handler: Callable[..., tuple[int, Any]]) -> Callable[..., tuple[int, Any]]:
        def wrapper(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
            try:
                return handler(m, b, q)
            except ValueError:
                return 403, {"error": "percorso non consentito"}
            except FileNotFoundError:
                return 404, {"error": "file non trovato"}
            except PermissionError:
                return 403, {"error": "non hai il permesso"}
        return wrapper

    def folder(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        p = path_from(q.get("p", ""))
        if not p.is_dir():
            raise FileNotFoundError
        return 200, list_folder(p)

    def gallery(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        which = m.group(1)
        exts = {"foto": IMAGES, "musica": AUDIO, "video": VIDEO, "note": {".txt", ".md"}}[which]
        target = user_folder(which)
        if which == "note":
            target.mkdir(parents=True, exist_ok=True)
        return 200, {"cartella": str(target.relative_to(home())), "voci": collect(target, exts)}

    def raw_file(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from urllib.parse import unquote

        p = path_from(unquote(m.group(1)))
        if not p.is_file():
            raise FileNotFoundError
        return 200, file_response(p)

    def doc_view(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        p = path_from(q.get("p", ""))
        if not p.is_file():
            raise FileNotFoundError
        from .. import diario

        diario.record_file(p)
        return 200, document_page(p, app.token)

    def doc_page(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        f = pdf_page_file(m.group(1), m.group(2))
        return (200, Raw(f, "image/png")) if f else (404, {"error": "pagina non trovata"})

    def trash(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        p = path_from(b.get("p"))
        if p == home():
            return 403, {"error": "la cartella personale non si cestina"}
        code, out = run(["gio", "trash", str(p)])
        return (200, {"ok": True}) if code == 0 else (500, {"error": "non riesco a spostarlo nel cestino"})

    def rename(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        p = path_from(b.get("p"))
        name = str(b.get("nome", "")).strip()
        if not name or "/" in name or name in (".", ".."):
            return 400, {"error": "nome non valido"}
        target = p.with_name(name)
        if target.exists():
            return 409, {"error": "c'è già un file con questo nome"}
        p.rename(target)
        return 200, {"ok": True, "percorso": str(target.relative_to(home()))}

    def new_folder(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        parent = path_from(b.get("p"))
        name = str(b.get("nome", "")).strip()
        if not name or "/" in name or name.startswith("."):
            return 400, {"error": "nome non valido"}
        (parent / name).mkdir(exist_ok=False)
        return 200, {"ok": True}

    def save_image(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        """L'immagine modificata nell'editor: una copia accanto all'originale («… (modificato).png») o al suo posto."""
        import base64

        original = path_from(b.get("p"))
        raw = str(b.get("dati", ""))
        data = base64.b64decode(raw.split(",", 1)[-1], validate=False)
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 60_000_000:
            return 400, {"error": "immagine non valida"}
        if b.get("copia", True) or original.suffix.lower() != ".png":
            target = original.with_name(f"{original.stem} (modificato).png")
            n = 2
            while target.exists():
                target = original.with_name(f"{original.stem} (modificato {n}).png")
                n += 1
        else:
            target = original
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        return 200, {"percorso": str(target), "nome": target.name}

    def note_read(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        p = path_from(q.get("p"))
        return 200, {"testo": p.read_text(errors="replace")[:1_000_000], "nome": p.name}

    def note_save(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        raw = str(b.get("p") or "")
        if not raw:  # nota nuova
            folder_ = user_folder("note")
            folder_.mkdir(parents=True, exist_ok=True)
            title = re.sub(r"[^\w\s-]", "", str(b.get("testo", "")).strip().splitlines()[0] if str(b.get("testo", "")).strip() else "")[:40].strip()
            base = title or "Nota"
            p, i = folder_ / f"{base}.txt", 2
            while p.exists():
                p, i = folder_ / f"{base} {i}.txt", i + 1
        else:
            p = path_from(raw)
        if p.suffix.lower() not in TEXT:
            return 400, {"error": "qui si scrivono solo file di testo"}
        p.write_text(str(b.get("testo", "")))
        return 200, {"ok": True, "percorso": str(p.relative_to(home()))}

    def open_with(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        p = path_from(b.get("p"))
        subprocess.Popen(["gio", "open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return 200, {"ok": True}

    def audio_action(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        """Impostazioni › Suono: scegliere uscita e microfono, volume e muto di ciascuno e dei programmi, prove."""
        from ..audio import Audio

        a, what = Audio(), str(b.get("azione", ""))
        try:
            target = int(b.get("id", -1))
        except (TypeError, ValueError):
            target = -1
        if what == "scegli":
            prof = b.get("profilo") if isinstance(b.get("profilo"), dict) and b.get("profilo") else None
            ok, msg = a.choose(None if prof else target, prof)
            return 200, {"ok": ok, "messaggio": msg}
        if what == "volume" and target >= 0:
            return 200, {"ok": a.volume(target, int(b.get("livello", 50)))}
        if what == "muto" and target >= 0:
            return 200, {"ok": a.mute(target, bool(b.get("muto")))}
        if what == "prova":
            return 200, {"ok": a.test_output()}
        if what == "prova-microfono":
            ok, msg = a.test_input()
            return 200, {"ok": ok, "messaggio": msg}
        return 400, {"error": "azione sconosciuta"}

    def settings(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        part = q.get("parte", "")
        data: dict[str, Any] = {}
        if part in ("", "wifi"):
            data["wifi"] = wifi_state(run)
        if part in ("", "bluetooth"):
            data["bluetooth"] = bluetooth_state(run)
        if part in ("", "suono"):
            from ..audio import Audio

            data["volume"] = volume_state(run)
            data["luminosita"] = brightness_state(run)
            data["audio"] = Audio().as_json()
        if part in ("", "info"):
            data["info"] = system_info()
            try:
                from ..updates import Updates

                data["aggiornamenti"] = Updates().describe()
            except Exception as exc:
                data["aggiornamenti"] = f"Non riesco a leggere lo stato: {exc}"
        if part in ("", "tastiera"):
            from .. import keyboard

            data["tastiera"] = {"scelta": keyboard.current(), "lingue": keyboard.LAYOUTS}
        if part in ("", "privacy"):
            from .. import diario, galleria

            data["galleria"] = {"attivo": galleria.enabled(), **(galleria.Gallery().stats() if galleria.enabled() else {})}

            data["diario"] = {"attivo": diario.enabled(), "giorni": len(diario.days_with_events()),
                              "conserva": diario.KEEP_DAYS}
        if part in ("", "voce"):
            from .. import voice

            data["voce"] = {"scelta": voice.chosen_voice(), "voci": voice.available_voices()}
        return 200, data

    def wifi(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        action = b.get("azione")
        if action in ("accendi", "spegni"):
            code, out = run(["nmcli", "radio", "wifi", "on" if action == "accendi" else "off"])
            return 200, {"ok": code == 0}
        if action == "collega":
            ok, msg = wifi_connect(str(b.get("nome", "")), str(b.get("password", "")), run)
            return 200, {"ok": ok, "messaggio": msg}
        if action == "dimentica":
            code, out = run(["nmcli", "connection", "delete", "id", str(b.get("nome", ""))])
            return 200, {"ok": code == 0}
        return 400, {"error": "azione sconosciuta"}

    def bluetooth(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        action = b.get("azione")
        if action in ("accendi", "spegni"):
            code, _ = run(["bluetoothctl", "power", "on" if action == "accendi" else "off"])
            return 200, {"ok": code == 0}
        addr = str(b.get("indirizzo", ""))
        if action in ("collega", "scollega") and re.fullmatch(r"[0-9A-F:]{17}", addr):
            code, _ = run(["bluetoothctl", "connect" if action == "collega" else "disconnect", addr], 30)
            return 200, {"ok": code == 0}
        return 400, {"error": "azione sconosciuta"}

    def level(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        try:
            value = max(0, min(100, int(b.get("livello", 0))))
        except (TypeError, ValueError):
            return 400, {"error": "livello non valido"}
        if m.group(1) == "volume":
            code, _ = run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value / 100:.2f}"])
            if b.get("muto") is not None:
                run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if b.get("muto") else "0"])
        else:
            code, _ = run(["brightnessctl", "set", f"{max(value, 5)}%"])
        return 200, {"ok": code == 0}

    def password(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        ok, msg = change_password(str(b.get("vecchia", "")), str(b.get("nuova", "")))
        return 200, {"ok": ok, "messaggio": msg}

    def github(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from ..updates import connect_github

        msg = connect_github(str(b.get("token", "")))
        return 200, {"ok": msg.startswith("Collegato"), "messaggio": msg}

    def mail_accounts(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        """Impostazioni › Posta: gli account collegati, il servizio di un indirizzo, collegarne uno nuovo."""
        from ..mail import client, oauth
        from ..mail.providers import provider_for

        if m.re.pattern.endswith("servizio"):
            address = q.get("indirizzo", "").strip()
            found = provider_for(address) if "@" in address else None
            if found is None:
                return 200, {"noto": False, "nome": "", "oauth": False, "aiuto": "Usa la password della tua casella di posta."}
            p = found[1]
            ready = False
            if p.oauth is not None:
                try:
                    ready = bool(oauth.client_credentials(p.oauth)[0])
                except Exception:
                    ready = False
            return 200, {"noto": True, "nome": p.name, "oauth": ready, "aiuto": p.password_help}
        if not b:
            return 200, {"account": [a.address for a in client.load_accounts()]}
        address, secret, use_oauth = str(b.get("indirizzo", "")).strip(), str(b.get("password", "")), bool(b.get("oauth"))
        if "@" not in address:
            return 200, {"ok": False, "messaggio": "Scrivi l'indirizzo completo, per esempio nome@gmail.com."}
        if not use_oauth and not secret:
            return 200, {"ok": False, "messaggio": "Scrivi la password."}
        from ..mail.service import add_account

        try:
            msg = add_account(address, use_oauth, ask=lambda _prompt: secret)
            account = next(a for a in client.load_accounts() if a.address == address)
            client.imap_connect(account).logout()  # proviamo subito: meglio saperlo adesso
        except Exception as exc:
            accounts = [a for a in client.load_accounts() if a.address != address]
            client.save_accounts(accounts)
            return 200, {"ok": False, "messaggio": f"Non riesco ad accedere: {exc}. Controlla indirizzo e password."}
        return 200, {"ok": True, "messaggio": msg + " Scarico le mail in sottofondo."}

    def power(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        cmd = POWER.get(str(b.get("azione", "")))
        if cmd is None:
            return 400, {"error": "azione sconosciuta"}
        code, _ = run(cmd)
        return 200, {"ok": code == 0}

    def appearance(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import carattere

        if m.re.pattern.endswith("caratteri"):
            return 200, {"caratteri": carattere.offered(), "minimo": carattere.MIN_SCALE, "massimo": carattere.MAX_SCALE}
        if b:
            msg = carattere.set_appearance(str(b.get("carattere", "")),
                                           f"{round(float(b['scala']) * 100)}%" if b.get("scala") else "")
            return 200, {**carattere.load(), "messaggio": msg}
        from .. import fuso

        zone = fuso.current()
        return 200, {**carattere.load(), "fuso": zone if zone not in fuso.UNSET else (fuso.guess() or "UTC")}

    def card(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import schede

        return 200, schede.lookup(q.get("titolo", "")[:120], q.get("tipo", "film"))

    def remote_thumb(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import schede

        got = schede.thumbnail(q.get("u", ""))
        return (200, Raw(got[0], got[1])) if got else (404, {"error": "miniatura non disponibile"})

    def file_thumb(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        p = path_from(q.get("p", ""))
        if not p.is_file():
            raise FileNotFoundError
        small = photo_thumbnail(p)
        return 200, Raw(small, "image/jpeg") if small else file_response(p)

    def people(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import galleria
        from ..tools.foto import galleria_has_data

        if not galleria_has_data():
            return 200, {"persone": [], "attivo": galleria.enabled()}
        return 200, {"persone": galleria.Gallery().people()[:40], "attivo": galleria.enabled()}

    def name_person(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import galleria

        name = str(b.get("nome", "")).strip()
        if not name or not str(b.get("id", "")).isdigit():
            return 400, {"error": "nome mancante"}
        return 200, {"ok": True, "nome": galleria.Gallery().name_person(int(b["id"]), name)}

    def face_thumb(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import galleria

        found = galleria.Gallery().face(int(m.group(1)))
        data = galleria.small_jpeg(found[0], 200, found[1]) if found else None
        return (200, Raw(data, "image/jpeg")) if data else (404, {"error": "volto non trovato"})

    def gallery_setting(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import galleria
        from ..tools.foto import make_tools

        if b.get("cancella"):
            galleria.Gallery().forget()
            return 200, {"ok": True, "messaggio": "Fatto: Nova ha dimenticato quello che aveva visto nelle foto."}
        tool = next(t for t in make_tools() if t.name == "photo_recognition")
        return 200, {"ok": True, "messaggio": tool.func("si" if b.get("attivo") else "no")}

    def diary(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import diario

        if b.get("cancella"):
            return 200, {"ok": True, "messaggio": f"Diario cancellato ({diario.forget_all()} giorni)."}
        diario.set_enabled(bool(b.get("attivo")))
        return 200, {"ok": True}

    def keyboard_layout(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import keyboard

        msg = keyboard.set_layout(str(b.get("lingua", "")))
        return 200, {"ok": msg.endswith("attiva."), "messaggio": msg}

    def choose_voice(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import voice

        name = str(b.get("voce", ""))
        if not voice.set_voice(name):
            return 400, {"error": "voce non disponibile"}
        if b.get("prova", True):
            import threading

            threading.Thread(target=voice.speak, args=("Ciao, sono Nova. Ti piace questa voce?",), daemon=True).start()
        return 200, {"ok": True}

    samples: list[list[float]] = []  # frasi registrate per l'impronta in corso

    def voiceprint_state(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import voiceprint

        data = voiceprint.load()
        return 200, {"persone": [p["nome"] for p in data["persone"]], "solo_conosciute": data["solo_conosciute"],
                     "frasi": voiceprint.PHRASES, "disponibile": voiceprint.spk_model_dir() is not None,
                     "registrate": len(samples)}

    def voiceprint_sample(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import voice, voiceprint

        if b.get("da_capo"):
            samples.clear()
        try:
            vector = voiceprint.embed(voice.record(float(b.get("secondi", 5))))
        except Exception as exc:
            return 200, {"ok": False, "messaggio": f"Non riesco a sentirti: {exc}"}
        if not vector:
            return 200, {"ok": False, "messaggio": "Non ho sentito abbastanza: riprova parlando un po' più vicino."}
        samples.append(vector)
        return 200, {"ok": True, "registrate": len(samples)}

    def voiceprint_finish(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import voiceprint
        from ..welcome import clean_name, load_profile

        name = clean_name(b.get("nome") or load_profile().get("name") or "Io") or "Io"
        try:
            voiceprint.enroll(name, list(samples))
        except ValueError as exc:
            return 200, {"ok": False, "messaggio": f"Ancora qualche frase: {exc}."}
        samples.clear()
        return 200, {"ok": True, "messaggio": f"Fatto: ora riconosco la voce di {name}."}

    def voiceprint_mode(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from .. import voiceprint

        if b.get("togli"):
            voiceprint.forget(str(b["togli"]))
        if "solo_conosciute" in b:
            voiceprint.set_only_known(bool(b["solo_conosciute"]))
        return 200, {"ok": True}

    for method, pattern, handler in (
        ("GET", r"/api/impronta", voiceprint_state),
        ("POST", r"/api/impronta/campione", voiceprint_sample),
        ("POST", r"/api/impronta/fine", voiceprint_finish),
        ("POST", r"/api/impronta/modo", voiceprint_mode),
        ("GET", r"/api/cartella", folder),
        ("GET", r"/api/raccolta/(foto|musica|video|note)", gallery),
        ("GET", r"/file/(.+)", raw_file),
        ("GET", r"/doc/vedi", doc_view),
        ("GET", r"/doc/pagina/([0-9a-f]{20})/(p-\d+\.png)", doc_page),
        ("POST", r"/api/file/cestino", trash),
        ("POST", r"/api/file/rinomina", rename),
        ("POST", r"/api/file/nuova-cartella", new_folder),
        ("POST", r"/api/file/salva-immagine", save_image),
        ("POST", r"/api/file/apri-con", open_with),
        ("GET", r"/api/nota", note_read),
        ("POST", r"/api/nota", note_save),
        ("GET", r"/api/impostazioni", settings),
        ("POST", r"/api/impostazioni/wifi", wifi),
        ("POST", r"/api/impostazioni/bluetooth", bluetooth),
        ("POST", r"/api/impostazioni/(volume|luminosita)", level),
        ("POST", r"/api/impostazioni/audio", audio_action),
        ("POST", r"/api/impostazioni/password", password),
        ("POST", r"/api/impostazioni/github", github),
        ("GET", r"/api/impostazioni/posta", mail_accounts),
        ("POST", r"/api/impostazioni/posta", mail_accounts),
        ("GET", r"/api/impostazioni/posta/servizio", mail_accounts),
        ("POST", r"/api/impostazioni/energia", power),
        ("POST", r"/api/impostazioni/voce", choose_voice),
        ("POST", r"/api/impostazioni/tastiera", keyboard_layout),
        ("POST", r"/api/impostazioni/diario", diary),
        ("GET", r"/api/scheda", card),
        ("GET", r"/api/persone", people),
        ("POST", r"/api/persona", name_person),
        ("GET", r"/api/miniatura-volto/(\d+)", face_thumb),
        ("POST", r"/api/impostazioni/galleria", gallery_setting),
        ("GET", r"/api/miniatura", remote_thumb),
        ("GET", r"/api/miniatura-file", file_thumb),
        ("GET", r"/api/aspetto", appearance),
        ("POST", r"/api/aspetto", appearance),
        ("GET", r"/api/aspetto/caratteri", appearance),
    ):
        app.route(method, pattern, guarded(handler))


# --- primi passi: cosa Nova propone di collegare ------------------------------------------------------
def _safe(check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    except Exception:
        return False


def _has_mail() -> bool:
    from ..mail.client import load_accounts

    return bool(load_accounts())


def _has_phone() -> bool:
    from ..mesh.bluetooth import load_known

    return bool(load_known())


def _has_github() -> bool:
    """Gli aggiornamenti arrivano: repository pubblico (nessun token) o token salvato."""
    from .. import vault
    from ..imageupdate import TOKEN_KEY
    from ..updates import public_repo

    return bool(vault.load(TOKEN_KEY)) or bool(public_repo())


def _online(run: Run = _run) -> bool:
    code, out = run(["nmcli", "-t", "-f", "STATE", "general"])
    return code == 0 and out.strip().startswith("connected")


def first_steps(run: Run = _run, checks: dict[str, Callable[[], bool]] | None = None) -> list[dict[str, Any]]:
    """Le proposte di Nova per iniziare, con quelle già fatte segnate."""
    checks = checks or {"internet": lambda: _online(run), "posta": _has_mail, "telefono": _has_phone,
                        "aggiornamenti": _has_github}
    steps = [
        {"id": "internet", "simbolo": "📶", "titolo": "Collegati a internet", "testo": "Scegli la tua rete Wi-Fi.",
         "azione": {"vista": "impostazioni", "parte": "wifi"}},
        {"id": "posta", "simbolo": "✉️", "titolo": "Collega la posta",
         "testo": "Ti avviso delle mail importanti e trovo bollette e scadenze.", "azione": {"vista": "impostazioni", "parte": "posta"}},
        {"id": "telefono", "simbolo": "📱", "titolo": "Collega il telefono",
         "testo": "Foto, notifiche e chiamate anche qui; funziona anche senza Wi-Fi.", "azione": {"chiedi": "collega il telefono"}},
        {"id": "aggiornamenti", "simbolo": "⬇️", "titolo": "Ricevi gli aggiornamenti",
         "testo": "Le nuove versioni di AIOS arrivano da sole, senza reinstallare.",
         "azione": {"vista": "impostazioni", "parte": "aggiornamenti"}},
        {"id": "modelli", "simbolo": "🧠", "titolo": "Rendimi più brava",
         "testo": "Guardo il computer e ti propongo i modelli AI più adatti.", "azione": {"chiedi": "quali modelli AI mi consigli?"}},
    ]
    for s in steps:
        check = checks.get(s["id"])
        s["fatto"] = _safe(check) if check else False
    return steps


def register_widgets(app: Any) -> None:
    """I widget della home (widget.py): i dati da mostrare, toglierne uno, i riquadri delle mappe."""
    from .. import widget
    from ..localapp import Raw

    def items(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"widget": widget.render()}

    def remove(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"messaggio": widget.remove(str(b.get("id", "")))}

    def tile(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        try:
            return 200, Raw(widget.tile(int(m.group(1)), int(m.group(2)), int(m.group(3))), "image/png")
        except ValueError:
            return 404, {"error": "fuori mappa"}
        except OSError:
            return 503, {"error": "mappa non raggiungibile"}

    app.route("GET", r"/api/widget", items)
    app.route("POST", r"/api/widget/via", remove)
    app.route("GET", r"/api/mappa/(\d+)/(\d+)/(\d+)\.png", tile)


def register_screens(app: Any) -> None:
    """Schermo AIOS nella home: gli altri tuoi PC accesi, con l'anteprima; aprirli e mandargli file."""
    import threading

    from ..schermo import azioni

    def items(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"dispositivi": [{"id": p.id, "nome": p.nome, "tipo": p.tipo} for p in azioni.peers()],
                     "identita": azioni.identity() is not None}

    def thumb(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        img = azioni.thumbnail(m.group(1))
        return (200, Raw(img, "image/jpeg")) if img else (404, {"error": "anteprima non disponibile"})

    def watch(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        ok, msg = azioni.open_viewer(m.group(1))
        return 200, {"ok": ok, "messaggio": msg}

    def send(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        peer_id = m.group(1)

        def go() -> None:
            paths = azioni.choose_files()
            if paths:
                azioni.send(peer_id, paths)

        threading.Thread(target=go, name="schermo-scegli", daemon=True).start()
        return 200, {"ok": True}

    app.route("GET", r"/api/schermi", items)
    app.route("GET", r"/api/schermi/([0-9a-f]{16})/anteprima\.jpg", thumb)
    app.route("POST", r"/api/schermi/([0-9a-f]{16})/guarda", watch)
    app.route("POST", r"/api/schermi/([0-9a-f]{16})/manda", send)


def register_cloud(app: Any) -> None:
    """Impostazioni › AI in cloud: acceso/spento, chiave, modello, limiti di spesa, attesa del locale, privacy."""
    import time as _time

    from .. import cloud

    cache: dict[str, Any] = {}

    def state(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        day, month, n = cloud.Usage().spent()
        return 200, {**cloud.settings(), "chiave": bool(cloud.key()), "oggi": round(day, 4), "mese": round(month, 4),
                     "richieste": n}

    def change(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        if isinstance(b.get("chiave"), str) and b["chiave"].strip():
            cloud.set_key(b["chiave"])
        try:
            cloud.save_settings({k: v for k, v in b.items() if k != "chiave"})
        except (TypeError, ValueError):
            return 400, {"error": "valore non valido"}
        return state(m, b, q)

    def models(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        if not cache.get("t") or _time.time() - cache["t"] > 3600:
            try:
                cache["modelli"], cache["t"] = cloud.models(), _time.time()
            except cloud.CloudError as exc:
                return 503, {"error": str(exc)}
        return 200, {"modelli": cache["modelli"]}

    app.route("GET", r"/api/cloud", state)
    app.route("POST", r"/api/cloud", change)
    app.route("GET", r"/api/cloud/modelli", models)


def register_activity(app: Any) -> None:
    """Gestione attività: i programmi che consumano, lo stato della macchina e dell'AI, chiudere a forza."""
    from .. import attivita

    def state(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, attivita.shared().snapshot()

    def end(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        pids = [int(p) for p in b.get("pid") or [] if str(p).isdigit()]
        ok, msg = attivita.shared().end(str(b.get("nome", "")), pids)
        return 200, {"ok": ok, "messaggio": msg}

    def unload(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        ok = attivita.unload_model(str(b.get("nome", "")))
        return 200, {"ok": ok, "messaggio": "Modello tolto dalla memoria: si ricarica alla prossima domanda." if ok
                     else "Non riesco a parlare con Ollama."}

    app.route("GET", r"/api/attivita", state)
    app.route("POST", r"/api/attivita/chiudi", end)
    app.route("POST", r"/api/attivita/modello", unload)


def register_calendar(app: Any, agenda: Callable[[], Any] | None = None) -> None:
    """Calendario e Rubrica: da guardare (e ritoccare); il resto si chiede a Nova."""
    from datetime import date, datetime, time as dtime, timedelta

    from ..rubrica import Rubrica

    def make() -> Any:
        if agenda is not None:
            return agenda()
        from ..agenda import Agenda

        return Agenda()

    def item(i: Any) -> dict[str, Any]:
        return {"tipo": i.kind, "id": i.id, "titolo": i.title, "quando": i.at.isoformat() if i.at else None,
                "tutto_il_giorno": i.all_day, "luogo": i.location, "ripeti": i.repeat, "fatto": i.done}

    def month(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        a = make()
        start = date.fromisoformat(q.get("da") or date.today().replace(day=1).isoformat())
        end = date.fromisoformat(q.get("a") or (start + timedelta(days=42)).isoformat())
        if (end - start).days > 120:
            return 400, {"error": "intervallo troppo lungo"}
        items = a.between(datetime.combine(start, dtime()), datetime.combine(end, dtime()))
        return 200, {"voci": [item(i) for i in items], "da_fare": [item(i) for i in a.todos()],
                     "scaduti": [item(i) for i in a.overdue()]}

    def new(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        title = str(b.get("titolo", "")).strip()[:200]
        if not title:
            return 400, {"error": "manca il titolo"}
        day = date.fromisoformat(str(b.get("giorno")))
        repeat = str(b.get("ripeti", ""))
        repeat = repeat if repeat in ("", "daily", "weekly", "monthly", "yearly") else ""
        hour = str(b.get("ora", "") or "")
        a = make()
        if b.get("promemoria"):
            at = datetime.combine(day, dtime.fromisoformat(hour)) if hour else datetime.combine(day, dtime(9))
            a.add_reminder(title, at, repeat)
        elif hour:
            start = datetime.combine(day, dtime.fromisoformat(hour))
            a.add_event(title, start, start + timedelta(minutes=int(b.get("durata", 60) or 60)),
                        location=str(b.get("luogo", ""))[:200], repeat=repeat)
        else:
            a.add_event(title, datetime.combine(day, dtime()), all_day=True, location=str(b.get("luogo", ""))[:200], repeat=repeat)
        return 200, {"ok": True, "messaggio": f"«{title}» in agenda."}

    def remove(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        kind = "reminder" if b.get("tipo") == "reminder" else "event"
        return 200, {"ok": make().delete(kind, int(b.get("id", 0)))}

    def done(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"ok": make().complete(int(b.get("id", 0)))}

    def contacts(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"contatti": Rubrica().as_json()}

    def contact_add(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        msg = Rubrica().add(str(b.get("nome", "")), str(b.get("telefono", "")), str(b.get("email", "")),
                            str(b.get("compleanno", "")), str(b.get("note", "")))
        return 200, {"ok": msg.endswith("in rubrica."), "messaggio": msg}

    def contact_remove(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"ok": Rubrica().remove(str(b.get("nome", "")))}

    app.route("GET", r"/api/calendario", month)
    app.route("POST", r"/api/calendario/nuovo", new)
    app.route("POST", r"/api/calendario/togli", remove)
    app.route("POST", r"/api/calendario/fatto", done)
    app.route("GET", r"/api/rubrica", contacts)
    app.route("POST", r"/api/rubrica", contact_add)
    app.route("POST", r"/api/rubrica/togli", contact_remove)


def register_display(app: Any) -> None:
    """Impostazioni › Schermo: luce notturna (e i monitor, monitor.py)."""
    from .. import luce_notturna as LN

    def night(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        if b:
            try:
                if b.get("adesso") is True:
                    LN.now_on()
                elif b.get("adesso") is False:
                    LN.save({"fino_a": ""})
                LN.save({k: v for k, v in b.items() if k != "adesso"})
            except (TypeError, ValueError):
                return 400, {"error": "valore non valido"}
        return 200, LN.status()

    app.route("GET", r"/api/luce-notturna", night)
    app.route("POST", r"/api/luce-notturna", night)

    from ..monitor import Monitors, best_rate_advice

    screens = Monitors()

    def monitors(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        state = screens.state()
        return 200, {"schermi": state, "consigli": best_rate_advice(state), "da_confermare": screens.pending is not None}

    def change(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        try:
            ok, msg = screens.change(str(b.get("nome", "")), {k: v for k, v in b.items() if k != "nome"})
        except (TypeError, ValueError):
            return 400, {"error": "valore non valido"}
        return 200, {"ok": ok, "messaggio": msg, "secondi": 15 if ok else 0}

    def confirm(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"ok": screens.confirm()}

    def undo(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"ok": screens.revert(), "messaggio": "Tornato come prima."}

    def automatic(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        screens.reset()
        return 200, {"ok": True, "messaggio": "Schermi sulle scelte automatiche."}

    app.route("GET", r"/api/monitor", monitors)
    app.route("POST", r"/api/monitor", change)
    app.route("POST", r"/api/monitor/conferma", confirm)
    app.route("POST", r"/api/monitor/annulla", undo)
    app.route("POST", r"/api/monitor/automatico", automatic)


def register_clipboard(app: Any) -> None:
    """Il pannello sopra i programmi: cronologia degli appunti (Super+V) ed emoji (Super+.)."""
    from .. import cronologia_appunti as CA

    hist = CA.History()
    emoji: list[Any] = []

    def items(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"voci": hist.load(), "attivo": CA.enabled()}

    def image(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        path = hist.dir / f"{m.group(1)}.png"
        if not path.exists():
            return 404, {"error": "non c'è più"}
        return 200, Raw(path, "image/png", csp="default-src 'none'")

    def use(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        ok = hist.put_text(str(b["testo"])) if isinstance(b.get("testo"), str) else hist.put(str(b.get("id", "")))
        getattr(app, "on_pick", lambda paste: None)(ok)
        return 200, {"ok": ok}

    def pin(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"ok": hist.pin(str(b.get("id", "")), bool(b.get("fissato", True)))}

    def remove(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"ok": hist.remove(str(b.get("id", "")))}

    def clear(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        n = hist.clear(keep_pinned=not b.get("tutto"))
        return 200, {"ok": True, "messaggio": f"Cronologia degli appunti svuotata ({n} voci)."}

    def toggle(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        CA.set_enabled(bool(b.get("attivo")))
        return 200, {"ok": True, "attivo": CA.enabled()}

    def emoji_list(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        if not emoji:
            emoji.extend(CA.emoji_list())
        return 200, {"emoji": emoji}

    def close(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        getattr(app, "on_pick", lambda paste: None)(False)
        return 200, {"ok": True}

    app.route("GET", r"/api/appunti", items)
    app.route("GET", r"/api/appunti/img/([0-9a-f]{16})\.png", image)
    app.route("POST", r"/api/appunti/usa", use)
    app.route("POST", r"/api/appunti/fissa", pin)
    app.route("POST", r"/api/appunti/togli", remove)
    app.route("POST", r"/api/appunti/svuota", clear)
    app.route("POST", r"/api/appunti/attivo", toggle)
    app.route("GET", r"/api/emoji", emoji_list)
    app.route("POST", r"/api/scelta/chiudi", close)


def register_notifications(app: Any) -> None:
    """Il centro notifiche: cronologia e «Non disturbare»."""
    from .. import notifiche as N

    store = N.Store()

    def items(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        return 200, {"notifiche": store.load()[:150], "non_lette": store.unread(), "non_disturbare": N.settings(),
                     "silenzio": N.quiet_now()}

    def read(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        store.mark_read()
        return 200, {"ok": True}

    def remove(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        if b.get("tutte"):
            return 200, {"ok": True, "tolte": store.clear(str(b.get("app", "")))}
        return 200, {"ok": store.remove(str(b.get("id", "")))}

    def quiet(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        try:
            if "minuti" in b:
                N.quiet_for(int(b["minuti"]) or None)
            changes = {k: v for k, v in b.items() if k != "minuti"}
            if changes.get("attivo") is False:
                changes["fino_a"] = ""
            N.save(changes)
        except (TypeError, ValueError):
            return 400, {"error": "valore non valido"}
        quiet_now = N.quiet_now()
        threading.Thread(target=N.apply_mode, args=(bool(quiet_now),), daemon=True).start()
        return 200, {"ok": True, "non_disturbare": N.settings(), "silenzio": quiet_now}

    def open_panel(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        getattr(app, "open_panel", lambda which: None)(str(b.get("quale", "notifiche")))
        return 200, {"ok": True}

    def open_app(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        """Dalla notifica al programma che l'ha mandata: davanti se è aperto, altrimenti si apre."""
        from . import focus_window, installed_apps, launch, open_windows

        app_id = str(b.get("app_id", ""))
        getattr(app, "on_pick", lambda paste: None)(False)
        if any(w["app_id"].lower() == app_id.lower() for w in open_windows()):
            return 200, {"ok": focus_window(app_id)}
        found = installed_apps().get(app_id)
        return 200, {"ok": bool(found and launch(found))}

    app.route("GET", r"/api/notifiche", items)
    app.route("GET", r"/api/notifiche/conta", lambda m, b, q: (200, {"non_lette": store.unread(), "silenzio": N.quiet_now()}))
    app.route("POST", r"/api/notifiche/lette", read)
    app.route("POST", r"/api/notifiche/togli", remove)
    app.route("POST", r"/api/notifiche/non-disturbare", quiet)
    app.route("POST", r"/api/pannello", open_panel)
    app.route("POST", r"/api/notifiche/apri", open_app)

    def ask(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        getattr(app, "on_pick", lambda paste: None)(False)
        getattr(app, "ask_nova", lambda text: None)(str(b.get("testo", ""))[:500])
        return 200, {"ok": True}

    app.route("POST", r"/api/nova/chiedi", ask)


def history_path() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aios" / "risultati.json"


def register_session(app: Any) -> None:
    """La carta «Riprendi da dove eri»: riaprire un programma alla volta, o dire di no; e lo storico dei risultati
    (colonna di sinistra), salvato su disco così resta dopo il riavvio."""
    from .. import sessione
    from ..tools.sessione import _launch

    def reopen(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from . import boot_time, installed_apps, open_windows

        store = sessione.Sessions()
        snap = store.last_session()
        if snap is None:
            return 404, {"error": "niente da riaprire"}
        try:
            i = int(b.get("indice"))
            program = snap.get("programmi", [])[i]
        except (TypeError, ValueError, IndexError):
            return 400, {"error": "programma non valido"}
        done = sessione.restore({"programmi": [program], "siti": []}, installed_apps(), _launch, open_windows())
        return 200, {"ok": bool(done), "messaggio": f"Riaperto {done[0]}." if done else f"{program.get('nome', 'Il programma')} è già aperto."}

    def dismiss(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        from . import boot_time

        sessione.Sessions().mark_offered(boot_time())
        return 200, {"ok": True}

    def history(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        path = history_path()
        if m.group(0).endswith("salva"):
            items = b.get("storico")
            if not isinstance(items, list):
                return 400, {"error": "storico non valido"}
            data = json.dumps(items[:10], ensure_ascii=False)
            if len(data) > 2_000_000:
                return 413, {"error": "storico troppo grande"}
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(data)
            tmp.replace(path)
            return 200, {"ok": True}
        try:
            items = json.loads(path.read_text())
        except (OSError, ValueError):
            items = []
        return 200, {"storico": items if isinstance(items, list) else []}

    app.route("POST", r"/api/sessione/riapri", reopen)
    app.route("POST", r"/api/sessione/no", dismiss)
    app.route("GET", r"/api/storico", history)
    app.route("POST", r"/api/storico/salva", history)


def register_first_steps(app: Any, run: Run = _run) -> None:
    from ..welcome import clean_name, load_profile, save_profile

    def state(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        profile = load_profile()
        hidden = set(profile.get("passi_nascosti", []))
        steps = [s for s in first_steps(run) if s["id"] not in hidden]
        return 200, {"benvenuto": not profile.get("welcome_done"), "nome": profile.get("name", ""), "passi": steps}

    def profile(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        changes: dict[str, Any] = {}
        if "nome" in b:
            changes["name"] = clean_name(b["nome"])
        if b.get("fatto"):
            changes["welcome_done"] = True
        if b.get("nascondi"):
            changes["passi_nascosti"] = sorted(set(load_profile().get("passi_nascosti", [])) | {str(b["nascondi"])})
        return 200, {"profilo": save_profile(**changes)}

    app.route("GET", r"/api/primi-passi", state)
    app.route("POST", r"/api/profilo", profile)
