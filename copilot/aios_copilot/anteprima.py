"""Il controllo visivo del programmatore: la foto di una pagina di SoIA fatta col codice modificato, e un modello
di visione del PC che la guarda («le lancette segnano le 10:10?», «si legge tutto?»).

La foto si fa in un processo a parte che carica il codice della copia personale (anche la modifica non ancora
salvata), avvia il server della schermata e la disegna con WebKitGTK, lo stesso motore della shell vera, in una
finestra che su Hyprland sta in uno spazio nascosto (l'utente non vede niente).
La pagina di prova ha i dati di una persona inventata (cartelle a parte, internet bloccato): niente agenda, file o
notifiche dell'utente. Per questo la foto può guardarla anche il modello in cloud, se l'utente l'ha acceso.
Prima e dopo la modifica si misura anche cosa c'è nella pagina (INVENTORY_JS): cambiamenti.py ne fa alcune righe
(«il riquadro «Meteo»: sfondo da bianco a rosso», «sparito: il pulsante «Fallo»») che il programmatore legge sempre.
Chi giudica: il modello in cloud che guarda la foto (se acceso), altrimenti il nucleo con l'adattatore «verifica»
(addestrato proprio per questo: dalla richiesta e da quelle righe dice se la modifica è riuscita), altrimenti un
modello di visione del PC. Senza nessuno restano i controlli senza modello e le righe misurate.
Questo file non si personalizza (è uno degli strumenti che controllano le modifiche).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import cambiamenti

SIZE = (1366, 768)
TIMEOUT = 90
WORKSPACE = "special:aios-prova"

LOOK_PROMPT = """Questa è una schermata di SoIA (un sistema operativo) appena modificata da un programmatore.
L'utente aveva chiesto: «{request}».
Adesso sono le {time} di {day}.
{question}
Guarda con attenzione e rispondi in italiano, in modo preciso e breve:
1. Descrivi quello che si vede nella parte che riguarda la richiesta (forma, posizione, testi, numeri, lancette…).
2. Elenca tutto quello che sembra sbagliato o strano: valori o lancette che non tornano con l'ora, elementi
   sovrapposti, tagliati o fuori posto, testi illeggibili o con poco contrasto, pezzi che mancano.
Se è tutto a posto scrivi «Sembra tutto a posto»."""

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def page_url(base: str, page: str) -> tuple[str, str]:
    """«casa», «impostazioni/aspetto», «attivita», «pannello/emoji» → (indirizzo, JavaScript da eseguire dopo)."""
    parts = [p for p in (page or "casa").strip().strip("/").lower().split("/") if p]
    if not parts or parts[0] in ("casa", "home", "schermata"):
        return base, ""
    if parts[0] == "pannello":
        which = parts[1] if len(parts) > 1 else "appunti"
        return base.replace("/#", "/static/pannello.html#", 1) + f"&apri={which}", ""
    args = ", ".join(repr(p) for p in parts)
    return base, f"typeof apriVista === 'function' && apriVista({args});"


# --- la persona inventata della pagina di prova -----------------------------------------------------------
BLOCKED = "http://127.0.0.1:9"  # internet chiuso: la pagina di prova non chiede niente fuori
DEMO_PLACE = {"nome": "Bologna", "lat": 44.494, "lon": 11.343, "zona": "Emilia-Romagna"}
DEMO_NOTE = "Comprare il pane e chiamare Marco per la cena di sabato."
DEMO_EVENTS = (("Dentista", 17, 30), ("Riunione con il condominio", 21, 0))
DEMO_FILES = ("Documenti/Bolletta luce ottobre.pdf", "Documenti/Contratto affitto.pdf", "Immagini/Vacanze/mare.jpg",
              "Musica/Preferite/canzone.mp3", "Scaricati/orario-treni.pdf")


def fake_env(folder: Path) -> dict[str, str]:
    """Le cartelle della persona inventata e internet chiuso (vale per il processo della pagina di prova)."""
    env = {"HOME": str(folder), "AIOS_CASA": str(folder), "XDG_CONFIG_HOME": str(folder / ".config"),
           "XDG_DATA_HOME": str(folder / ".local/share"), "XDG_STATE_HOME": str(folder / ".local/state"),
           "XDG_CACHE_HOME": str(folder / ".cache"), "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env[k] = BLOCKED
    return env


def keep_browsers() -> None:
    """Chromium di Playwright sta nella casa vera: la casa finta non deve nasconderlo."""
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache" / "ms-playwright"))


def local_zone() -> str:
    if "/" in os.environ.get("TZ", ""):
        return os.environ["TZ"].lstrip(":")
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else "Europe/Rome"
    except OSError:
        return "Europe/Rome"


def _gray_png(size: int = 256) -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes((228, 232, 226)) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _fake_weather(url: str) -> bytes:
    today = datetime.now().date()
    from datetime import timedelta

    days = [(today + timedelta(days=i)).isoformat() for i in range(5)]
    return json.dumps({"current": {"temperature_2m": 18.4, "apparent_temperature": 17.0, "weather_code": 2,
                                   "wind_speed_10m": 9.0, "relative_humidity_2m": 64},
                       "daily": {"time": days, "weather_code": [2, 61, 3, 0, 1], "temperature_2m_max": [21, 17, 18, 23, 22],
                                 "temperature_2m_min": [11, 10, 9, 12, 13],
                                 "precipitation_probability_max": [10, 80, 40, 0, 5]}}).encode()


def seed_demo(folder: Path, clock_zone: str | None = None) -> None:
    """Riempie le cartelle della persona inventata: nome, widget, agenda, qualche file."""
    from . import widget
    from .agenda import Agenda
    from .welcome import save_profile

    save_profile(name="Giulia", welcome_done=True)
    widget.save([{"id": "orologio-1", "tipo": "orologio", "luogo": {**DEMO_PLACE, "fuso": clock_zone or local_zone()}},
                 {"id": "meteo-1", "tipo": "meteo", "luogo": DEMO_PLACE},
                 {"id": "nota-1", "tipo": "nota", "testo": DEMO_NOTE},
                 {"id": "mappa-1", "tipo": "mappa", "luogo": DEMO_PLACE}])
    agenda = Agenda()
    today = datetime.now().replace(second=0, microsecond=0)
    for title, h, m in DEMO_EVENTS:
        agenda.add_event(title, today.replace(hour=h, minute=m))
    for rel in DEMO_FILES:
        f = folder / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")


def fake_services() -> None:
    """Il meteo e le mappe con dati inventati (internet è chiuso)."""
    from . import widget

    real = widget.TIPI["meteo"]["dati"]
    widget.TIPI["meteo"]["dati"] = lambda w, fetch=None, now=time.time: real(w, _fake_weather, now)
    tile = _gray_png()
    widget.tile = lambda z, x, y, fetch=None: tile


# --- dentro il processo a parte ---------------------------------------------------------------------------
class _QuietAgent:
    def ask(self, text: str, on_event: Any = None, context: Any = None) -> str:
        return "Sono una prova del controllo visivo."


def _serve() -> str:
    from .localapp import serve
    from .shell import ShellApp

    app = ShellApp(lambda *a, **k: _QuietAgent())
    app.restart_shell = lambda: None
    _, url = serve(app)
    return url


# Prima che la pagina parta: gli errori di JavaScript si annotano (sono il guasto più comune di una modifica).
CATCH_ERRORS_JS = """(() => { const s = document.createElement('style');  // ferma animazioni e cursore: prima e dopo uguali
  s.textContent = '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';
  document.documentElement.appendChild(s); })();
window.__errori = [];
addEventListener('error', e => __errori.push((e.message || 'errore') + (e.lineno ? ' (riga ' + e.lineno + ' di ' +
  (String(e.filename || '').split('#')[0].split('/').pop() || 'home.html') + ')' : '')));
addEventListener('unhandledrejection', e => __errori.push('promessa rifiutata: ' + (e.reason && e.reason.message || e.reason)));"""

# Dopo: cosa si vede di sbagliato senza bisogno di un modello (testi tagliati o che escono dallo schermo,
# testi uno sopra l'altro, poco contrasto). Torna un JSON.
CHECK_JS = r"""(() => {
  const out = { errori: (window.__errori || []).slice(0, 8), problemi: [] };
  const W = innerWidth, H = innerHeight;
  const rgb = s => (s.match(/[\d.]+/g) || []).map(Number);
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const bg = el => { for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e), c = rgb(s.backgroundColor);
      if (s.backgroundImage !== 'none') return null;  // sfumature e immagini: il contrasto non si calcola
      if (c.length >= 3 && (c.length < 4 || c[3] > 0.6)) return c; } return null; };
  const name = el => { const t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return '«' + t + '»' + (el.id ? ' #' + el.id : el.classList.length ? ' .' + el.classList[0] : ''); };
  const leaves = [];
  for (const el of document.querySelectorAll('body *')) {
    if (['SCRIPT', 'STYLE', 'svg', 'path'].includes(el.tagName)) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > H) continue;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    leaves.push([el, r]);
    if (r.right > W + 2 || r.left < -2) out.problemi.push('esce dallo schermo: ' + name(el));
    if (st.overflow !== 'visible' && st.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 3)
      out.problemi.push('testo tagliato: ' + name(el));
    const c = rgb(st.color), b = bg(el);
    if (c.length >= 3 && b) { const [a, z] = [lum(c), lum(b)].sort((x, y) => y - x);
      const k = (a + 0.05) / (z + 0.05); if (k < 2.2) out.problemi.push('poco contrasto (' + k.toFixed(1) + '): ' + name(el)); }
  }
  for (let i = 0; i < leaves.length && out.problemi.length < 30; i++) for (let j = i + 1; j < leaves.length; j++) {
    const [e1, a] = leaves[i], [e2, b] = leaves[j];
    if (e1.contains(e2) || e2.contains(e1)) continue;
    const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    if (w > 4 && h > 4 && w * h > 0.3 * Math.min(a.width * a.height, b.width * b.height))
      out.problemi.push('testi uno sopra l\'altro: ' + name(e1) + ' e ' + name(e2));
  }
  out.problemi = [...new Set(out.problemi)].slice(0, 12);
  return JSON.stringify(out);
})()"""

# Cosa c'è nella pagina e dove, per confrontare prima e dopo (cambiamenti.py): gli elementi visibili che contano
# (scritte, riquadri, pulsanti, immagini, campi, cose colorate o bordate) con posizione, colori, misure e testo.
INVENTORY_JS = r"""(() => {
  const W = innerWidth, H = innerHeight;
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true }), cache = {};
  const hex = s => {  // qualunque colore CSS (rgb, oklch, color-mix…) → #rrggbb; '' se trasparente
    if (!s || s === 'transparent') return ''; if (s in cache) return cache[s];
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = s; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    return cache[s] = d[3] < 40 ? '' : '#' + [d[0], d[1], d[2]].map(x => x.toString(16).padStart(2, '0')).join(''); };
  const words = el => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').split(' ').slice(0, 4).join(' ').slice(0, 30);
  const own = el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim().replace(/\s+/g, ' ').slice(0, 60);
  const isCard = (el, st, r) => { const bg = hex(st.backgroundColor);
    return bg && parseFloat(st.borderTopLeftRadius) >= 6 && r.width >= 100 && r.height >= 36 && r.width <= W * 0.7 && r.height <= H * 0.8; };
  const line = (w, s, c) => parseFloat(w) >= 1 && s !== 'none' && s !== 'hidden' && hex(c) ? Math.round(parseFloat(w)) + ' ' + hex(c) : '';
  const index = new Map(), out = [];
  const pathOf = el => { const p = []; for (let e = el; e && e !== document.body; e = e.parentElement) {
      let i = 0; for (let s = e.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === e.tagName) i++;
      p.push(e.tagName.toLowerCase() + i); } return p.reverse().join('/'); };
  for (const el of document.querySelectorAll('body *')) {
    if (out.length >= 2500) break;
    const tag = el.tagName;
    if (['SCRIPT', 'STYLE', 'TEMPLATE', 'BR', 'OPTION'].includes(tag) || (el.closest('svg') && tag !== 'svg')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3 || r.bottom < 0 || r.top > H) continue;
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || +st.opacity === 0) continue;
    const text = own(el), bg = hex(st.backgroundColor), card = isCard(el, st, r);
    const ol = line(st.outlineWidth, st.outlineStyle, st.outlineColor), bd = line(st.borderTopWidth, st.borderTopStyle, st.borderTopColor);
    const special = ['BUTTON', 'IMG', 'svg', 'CANVAS', 'INPUT', 'TEXTAREA', 'SELECT', 'VIDEO'].includes(tag);
    if (!text && !special && !bg && !ol && !bd) continue;
    let kind = 'il blocco', name = text ? words(el) : '';
    if (tag === 'BUTTON' || el.getAttribute('role') === 'button') { kind = 'il pulsante'; name = words(el); }
    else if (/^H[1-6]$/.test(tag)) kind = 'il titolo';
    else if (tag === 'IMG') { kind = "l'immagine"; name = (el.alt || '').slice(0, 30); }
    else if (tag === 'svg' || tag === 'CANVAS') kind = 'il disegno';
    else if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) { kind = 'il campo'; name = (el.placeholder || el.getAttribute('aria-label') || '').slice(0, 30); }
    else if (card) { kind = 'il riquadro'; const first = [...el.querySelectorAll('*')].find(x => own(x)); name = first ? words(first) : words(el); }
    else if (text) kind = 'la scritta';
    else { const first = [...el.querySelectorAll('*')].find(x => own(x)); name = first ? words(first) : ''; }
    let par = -1; for (let e = el.parentElement; e; e = e.parentElement) if (index.has(e)) { par = index.get(e); break; }
    index.set(el, out.length);
    const cls = [...el.classList].slice(0, 2).join('.');
    out.push({ k: tag + (el.id ? '#' + el.id : '') + (cls ? '.' + cls : '') + '|' + kind + '|' + name + '|' + text,
      p: pathOf(el), l: kind + (name ? ' «' + name + '»' : ''), t: text,
      b: [r.left, r.top, r.width, r.height].map(Math.round), bg, c: text ? hex(st.color) : '',
      fs: text ? Math.round(parseFloat(st.fontSize)) : 0, r: Math.round(parseFloat(st.borderTopLeftRadius) || 0),
      ol, bd, op: Math.round(+st.opacity * 100) / 100, par });
  }
  const page = hex(getComputedStyle(document.body).backgroundColor) || hex(getComputedStyle(document.documentElement).backgroundColor) || '#ffffff';
  return { fondo: page, scuro: matchMedia('(prefers-color-scheme: dark)').matches, elementi: out, larghezza: W, altezza: H };
})()"""

# Quello che si salva accanto alla foto: errori, problemi e l'inventario (un JSON in una stringa: WebKit lo passa così).
REPORT_JS = "(() => { const o = JSON.parse(" + CHECK_JS + "); o.inventario = " + INVENTORY_JS + "; return JSON.stringify(o); })()"


def _snapshot_webkit(url: str, js: str, out: Path, size: tuple[int, int], title: str) -> bool:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import GLib, Gtk, WebKit

    done = {"ok": False}
    app = Gtk.Application(application_id=None)

    def finish(ok: bool) -> None:
        done["ok"] = ok
        app.quit()

    def activate(application: Any) -> None:
        win = Gtk.ApplicationWindow(application=application, title=title)
        win.set_default_size(*size)
        manager = WebKit.UserContentManager()
        manager.add_script(WebKit.UserScript.new(CATCH_ERRORS_JS, WebKit.UserContentInjectedFrames.TOP_FRAME,
                                                 WebKit.UserScriptInjectionTime.START, None, None))
        view = WebKit.WebView(user_content_manager=manager)
        win.set_child(view)
        win.present()

        def shoot() -> bool:
            def saved(v: Any, res: Any) -> None:
                try:
                    texture = v.get_snapshot_finish(res)
                    finish(bool(texture.save_to_png(str(out))))
                except Exception:
                    finish(False)

            def checked(v: Any, res: Any) -> None:
                try:
                    out.with_suffix(".json").write_text(v.evaluate_javascript_finish(res).to_string())
                except Exception:
                    pass
                v.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None, saved)

            view.evaluate_javascript(REPORT_JS, -1, None, None, None, checked)
            return False

        def loaded(v: Any, event: Any) -> None:
            if event != WebKit.LoadEvent.FINISHED:
                return
            if js:
                GLib.timeout_add(1200, lambda: (v.evaluate_javascript(js, -1, None, None, None, None, None), False)[1])
            GLib.timeout_add(3500 if js else 2500, shoot)

        view.connect("load-changed", loaded)
        view.load_uri(url)
        GLib.timeout_add_seconds(TIMEOUT - 10, lambda: (finish(False), False)[1])

    app.connect("activate", activate)
    app.run([])
    return done["ok"]


def _snapshot_chromium(url: str, js: str, out: Path, size: tuple[int, int]) -> bool:
    """Senza schermo (sviluppo, prove): Chromium con Playwright, se c'è."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    exe = os.environ.get("AIOS_CHROMIUM") or None
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe)
        try:
            page = browser.new_page(viewport={"width": size[0], "height": size[1]})
            page.add_init_script(CATCH_ERRORS_JS)
            page.goto(url)
            time.sleep(1.2)
            if js:
                page.evaluate(js)
            time.sleep(2.0)
            out.with_suffix(".json").write_text(page.evaluate(REPORT_JS))
            page.screenshot(path=str(out))
        finally:
            browser.close()
    return out.is_file()


def child_main(argv: list[str]) -> int:
    out, page = Path(argv[0]), argv[1]
    w, h = int(argv[2]), int(argv[3])
    title = argv[4] if len(argv) > 4 else "aios-prova"
    if "--finti" in argv:
        folder = out.parent / "casa"
        keep_browsers()
        os.environ.update(fake_env(folder))
        seed_demo(folder)
        fake_services()
    url, js = page_url(_serve(), page)
    ok = False
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        try:
            ok = _snapshot_webkit(url, js, out, (w, h), title)
        except Exception as exc:
            print(f"webkit: {exc}", file=sys.stderr)
    if not ok:
        try:
            ok = _snapshot_chromium(url, js, out, (w, h))
        except Exception as exc:
            print(f"chromium: {exc}", file=sys.stderr)
    return 0 if ok else 1


# --- dal programmatore ------------------------------------------------------------------------------------
def take(page: str, root: Path, size: tuple[int, int] = SIZE, fake: bool = True) -> tuple[Path | None, str]:
    """La foto della pagina col codice in «root» (la copia personale) → (file png, problema).
    Con «fake» (sempre, per il programmatore) la pagina mostra i dati della persona inventata."""
    out = Path(tempfile.mkdtemp(prefix="aios-prova-")) / "pagina.png"
    title = f"aios-prova-{os.getpid()}"
    code = ("import sys; sys.path.insert(0, %r); from aios_copilot import anteprima; "
            "sys.exit(anteprima.child_main(sys.argv[1:]))") % str(root)
    cmd = [sys.executable, "-c", code, str(out), page, str(size[0]), str(size[1]), title] + (["--finti"] if fake else [])
    env = {**os.environ, "AIOS_CODICE": "base", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("LD_PRELOAD", None)
    log = out.parent / "errori.txt"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        # Hyprland apre la finestra direttamente nello spazio nascosto: sullo schermo non compare niente
        keep = " ".join(f"{k}={shlex.quote(env[k])}" for k in ("AIOS_CODICE", "PYTHONDONTWRITEBYTECODE", "XDG_DATA_HOME",
                                                                 "XDG_STATE_HOME", "XDG_CONFIG_HOME", "HOME") if k in env)
        line = f"env {keep} {shlex.join(cmd)} > {shlex.quote(str(log))} 2>&1"
        subprocess.run(["hyprctl", "dispatch", "exec", f"[workspace {WORKSPACE} silent; noinitialfocus; float; size {size[0]} {size[1]}] sh -c {shlex.quote(line)}"],
                       capture_output=True, timeout=10)
        deadline = time.time() + TIMEOUT
        while time.time() < deadline and not out.exists():
            time.sleep(0.5)
        time.sleep(0.3)  # che finisca di scrivere
    else:
        try:
            with open(log, "w") as err:
                subprocess.run(cmd, env=env, stdout=err, stderr=err, timeout=TIMEOUT, cwd=str(root))
        except (OSError, subprocess.SubprocessError) as exc:
            return None, str(exc)
    if out.is_file() and out.stat().st_size > 0:
        return out, ""
    problem = log.read_text(errors="replace").strip()[-300:] if log.exists() else ""
    return None, problem or "la pagina non si è disegnata"


def vision_model() -> str | None:
    """Solo un modello di visione del PC (la foto ha i dati dell'utente)."""
    try:
        from .galleria import vision_model as pick

        return pick()
    except Exception:
        return None


def _cloud_look(image: Path, prompt: str) -> str:
    try:
        from .cloud import Escalation, see

        if not Escalation().available():
            return ""
        return see(image.read_bytes(), prompt)
    except Exception:
        return ""


def _nucleo_look(changes: list[str] | None, request: str) -> str:
    """Il controllo di qualità del nucleo (adattatore «verifica»): la richiesta e cosa è cambiato, misurato."""
    if not changes:
        return ""
    try:
        from .nucleo import Nucleo

        data = Nucleo(timeout=300).check_change(changes, request)
    except Exception:
        return ""
    if data is None:
        return ""
    problems = [str(p) for p in data.get("problemi") or []]
    if data.get("fatto") and not problems:
        return "La modifica sembra riuscita come chiesto."
    return "La modifica non sembra riuscita: " + ("; ".join(problems) if problems else "non fa quello che è stato chiesto") + "."


def look(image: Path, request: str, question: str = "", model: str | None = None,
         see: Callable[[Path, str], str] | None = None, now: datetime | None = None, cloud: bool = True,
         changes: list[str] | None = None) -> str:
    """Chi giudica: il modello in cloud che guarda la foto (solo pagine coi dati finti, se l'utente l'ha acceso), il
    nucleo con l'adattatore «verifica» (legge cosa è cambiato, misurato), un modello di visione del PC. "" se nessuno."""
    now = now or datetime.now()
    prompt = LOOK_PROMPT.format(request=request.strip(), time=now.strftime("%H:%M"), day=GIORNI[now.weekday()],
                                question=f"Controlla in particolare: {question.strip()}" if question.strip() else "")
    if see is not None:
        return see(image, prompt)
    answer = (_cloud_look(image, prompt) if cloud else "") or _nucleo_look(changes, request)
    if answer:
        return answer
    model = model or vision_model()
    if model is None:
        return ""
    from . import engines

    return engines.describe_image(image, prompt, model)


MAX_CROP = 448  # lato più lungo dei ritagli dati al modello


def change_crops(before: bytes, after: bytes, pad: int = 28, min_side: int = 160) -> tuple[bytes, bytes, tuple[int, int, int, int]] | None:
    """La parte della pagina che è cambiata, prima e dopo (PNG), e il riquadro (x, y, w, h); None se non è cambiato niente.
    Il modello di verifica guarda questi ritagli: la pagina intera rimpicciolita renderebbe i dettagli invisibili."""
    import cv2
    import numpy as np

    a = cv2.imdecode(np.frombuffer(before, np.uint8), cv2.IMREAD_COLOR)
    b = cv2.imdecode(np.frombuffer(after, np.uint8), cv2.IMREAD_COLOR)
    if a is None or b is None:
        return None
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    diff = cv2.cvtColor(cv2.absdiff(a, b), cv2.COLOR_BGR2GRAY)
    mask = cv2.dilate((diff > 24).astype(np.uint8), np.ones((5, 5), np.uint8))
    pts = cv2.findNonZero(mask)
    if pts is None or len(pts) < 12:  # qualche pixel sparso (bordi sfumati): non è una modifica
        return None
    x, y, bw, bh = cv2.boundingRect(pts)
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    if x1 - x0 < min_side:
        c = (x0 + x1) // 2
        x0, x1 = max(0, c - min_side // 2), min(w, c + min_side // 2)
    if y1 - y0 < min_side:
        c = (y0 + y1) // 2
        y0, y1 = max(0, c - min_side // 2), min(h, c + min_side // 2)

    def png(img: Any) -> bytes:
        crop = img[y0:y1, x0:x1]
        scale = MAX_CROP / max(crop.shape[:2])
        if scale < 1:
            crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        return cv2.imencode(".png", crop)[1].tobytes()

    return png(a), png(b), (x0, y0, x1 - x0, y1 - y0)


def page_report(image: Path) -> dict[str, Any]:
    """Gli errori di JavaScript, i problemi trovati nella pagina senza modello e l'inventario (accanto alla foto)."""
    try:
        data = json.loads(image.with_suffix(".json").read_text())
        return {"errori": [str(x) for x in data.get("errori", [])], "problemi": [str(x) for x in data.get("problemi", [])],
                "inventario": data.get("inventario")}
    except (OSError, ValueError, AttributeError):
        return {"errori": [], "problemi": [], "inventario": None}


def base_root(copy: Path | None = None) -> Path:
    """Il codice com'era prima della modifica in corso: l'ultima versione salvata della copia personale (git HEAD,
    estratta in una cartella a parte), oppure il codice dell'immagine."""
    from . import BASE_DIR

    if copy is not None and (copy / ".git").is_dir():
        out = Path(tempfile.mkdtemp(prefix="aios-prima-"))
        try:
            arch = subprocess.run(["git", "-C", str(copy), "archive", "HEAD", "aios_copilot"], capture_output=True, timeout=60)
            if arch.returncode == 0:
                subprocess.run(["tar", "-x", "-C", str(out)], input=arch.stdout, capture_output=True, timeout=60, check=True)
                if (out / "aios_copilot" / "__init__.py").exists():
                    return out
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(out, ignore_errors=True)
    return BASE_DIR.parent


def can_render() -> bool:
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        return True
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


class Eyes:
    """Il controllo visivo per il programmatore: la foto della pagina, i controlli senza modello (errori di
    JavaScript, testi tagliati, sovrapposti o poco leggibili) e, se c'è, il modello di visione del PC."""

    def __init__(self, root: Path, request: str, see: Callable[[Path, str], str] | None = None,
                 shoot: Callable[[str], tuple[Path | None, str]] | None = None, model: str | None = None,
                 shoot_base: Callable[[str], tuple[Path | None, str]] | None = None):
        self.root, self.request, self.see, self.model = root, request, see, model
        self.shoot = shoot or (lambda page: take(page, self.root))
        self._base_root: Path | None = None
        self.shoot_base = shoot_base or (lambda page: take(page, self._base()))
        self.last: Path | None = None
        self._before_img: dict[str, Path | None] = {}
        self.failed = False  # la foto non si fa su questo PC: niente controllo visivo
        self.broken = False  # l'ultima occhiata ha trovato errori o problemi nuovi
        self._before: dict[str, dict[str, Any]] = {}

    def _base(self) -> Path:
        if self._base_root is None:
            self._base_root = base_root(self.root)
        return self._base_root

    def before(self, page: str) -> dict[str, Any]:
        """Com'era la pagina prima della modifica: i problemi che aveva già (non sono colpa sua) e l'inventario."""
        if page not in self._before:
            image, _ = self.shoot_base(page)
            self._before_img[page] = image
            self._before[page] = page_report(image) if image else {"errori": [], "problemi": [], "inventario": None}
        return self._before[page]

    def available(self) -> bool:
        return not self.failed and (self.see is not None or can_render())

    def __call__(self, page: str, question: str = "") -> str:
        image, problem = self.shoot(page)
        if image is None:  # non dipende dalla modifica (i moduli si caricano, lo dice controlla): si va avanti senza
            self.failed = True
            return f"La foto della pagina «{page}» qui non si riesce a fare ({problem[:120]}): rileggi bene la logica."
        self.last = image
        full, old = page_report(image), self.before(page)
        changes = cambiamenti.from_reports(old, full) if old.get("inventario") and full.get("inventario") else None
        report = {k: [x for x in full[k] if x not in old[k]] for k in ("errori", "problemi")}
        self.broken = bool(report["errori"] or report["problemi"])
        lines = []
        if report["errori"]:
            lines.append("Errori di JavaScript nella pagina (da correggere):\n" + "\n".join(f"- {e}" for e in report["errori"]))
        if report["problemi"]:
            lines.append("Problemi nuovi rispetto a SoIA originale (da correggere):\n"
                         + "\n".join(f"- {e}" for e in report["problemi"]))
        if not lines:
            lines.append("Nessun errore di JavaScript, niente testi tagliati, sovrapposti o poco leggibili.")
        if changes:
            lines.append("Cosa è cambiato rispetto a prima (misurato nella pagina; controlla che sia quello chiesto):\n"
                         + "\n".join(f"- {c}" for c in changes))
        else:
            old_img = self._before_img.get(page)
            same = changes is not None
            if changes is None and old_img is not None and old_img.exists():
                try:
                    same = change_crops(old_img.read_bytes(), image.read_bytes()) is None
                except Exception:
                    same = False
            if same:
                lines.append("La pagina è uguale a prima della modifica: quello che hai cambiato qui non si vede.")
        answer = look(image, self.request, question, model=self.model, see=self.see, changes=changes)
        if answer:
            lines.append("Chi guarda la foto dice:\n" + answer)
        else:
            lines.append("(Nessun modello che vede: rileggi bene la logica di quello che si vede.)")
        return f"Pagina «{page}»:\n" + "\n\n".join(lines)


if __name__ == "__main__":
    sys.exit(child_main(sys.argv[1:]))
