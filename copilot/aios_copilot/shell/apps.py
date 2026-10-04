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
import html
import mimetypes
import os
import re
import shutil
import subprocess
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
    except Exception:
        pass
    try:
        from ..llm import OllamaClient

        info["modello"] = OllamaClient().model
    except Exception:
        pass
    return info


POWER = {"spegni": ["systemctl", "poweroff"], "riavvia": ["systemctl", "reboot"], "sospendi": ["systemctl", "suspend"],
         "blocca": ["loginctl", "lock-session"], "esci": ["labwc", "--exit"]}


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

    def settings(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        part = q.get("parte", "")
        data: dict[str, Any] = {}
        if part in ("", "wifi"):
            data["wifi"] = wifi_state(run)
        if part in ("", "bluetooth"):
            data["bluetooth"] = bluetooth_state(run)
        if part in ("", "suono"):
            data["volume"] = volume_state(run)
            data["luminosita"] = brightness_state(run)
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

    def power(m: Any, b: dict[str, Any], q: dict[str, str]) -> tuple[int, Any]:
        cmd = POWER.get(str(b.get("azione", "")))
        if cmd is None:
            return 400, {"error": "azione sconosciuta"}
        code, _ = run(cmd)
        return 200, {"ok": code == 0}

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

    for method, pattern, handler in (
        ("GET", r"/api/cartella", folder),
        ("GET", r"/api/raccolta/(foto|musica|video|note)", gallery),
        ("GET", r"/file/(.+)", raw_file),
        ("GET", r"/doc/vedi", doc_view),
        ("GET", r"/doc/pagina/([0-9a-f]{20})/(p-\d+\.png)", doc_page),
        ("POST", r"/api/file/cestino", trash),
        ("POST", r"/api/file/rinomina", rename),
        ("POST", r"/api/file/nuova-cartella", new_folder),
        ("POST", r"/api/file/apri-con", open_with),
        ("GET", r"/api/nota", note_read),
        ("POST", r"/api/nota", note_save),
        ("GET", r"/api/impostazioni", settings),
        ("POST", r"/api/impostazioni/wifi", wifi),
        ("POST", r"/api/impostazioni/bluetooth", bluetooth),
        ("POST", r"/api/impostazioni/(volume|luminosita)", level),
        ("POST", r"/api/impostazioni/password", password),
        ("POST", r"/api/impostazioni/github", github),
        ("POST", r"/api/impostazioni/energia", power),
        ("POST", r"/api/impostazioni/voce", choose_voice),
        ("POST", r"/api/impostazioni/tastiera", keyboard_layout),
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
    from .. import vault
    from ..imageupdate import TOKEN_KEY

    return bool(vault.load(TOKEN_KEY))


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
         "testo": "Ti avviso delle mail importanti e trovo bollette e scadenze.", "azione": {"chiedi": "collega la posta"}},
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
