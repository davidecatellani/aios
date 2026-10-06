"""Il controllo visivo del programmatore: la foto di una pagina di AIOS fatta col codice modificato, e un modello
di visione del PC che la guarda («le lancette segnano le 10:10?», «si legge tutto?»).

La foto si fa in un processo a parte che carica il codice della copia personale (anche la modifica non ancora
salvata), avvia il server della schermata e la disegna con WebKitGTK, lo stesso motore della shell vera, in una
finestra che su Hyprland sta in uno spazio nascosto (l'utente non vede niente). La foto resta sul PC: la guarda
solo il modello di visione locale, mai uno in cloud (sulla schermata ci sono agenda, file, notifiche).
Se manca lo schermo o il modello di visione il controllo visivo si salta: restano gli altri controlli.
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

SIZE = (1366, 768)
TIMEOUT = 90
WORKSPACE = "special:aios-prova"

LOOK_PROMPT = """Questa è una schermata di AIOS (un sistema operativo) appena modificata da un programmatore.
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
CATCH_ERRORS_JS = """window.__errori = [];
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

            view.evaluate_javascript(CHECK_JS, -1, None, None, None, checked)
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
            out.with_suffix(".json").write_text(page.evaluate(CHECK_JS))
            page.screenshot(path=str(out))
        finally:
            browser.close()
    return out.is_file()


def child_main(argv: list[str]) -> int:
    out, page = Path(argv[0]), argv[1]
    w, h = int(argv[2]), int(argv[3])
    title = argv[4] if len(argv) > 4 else "aios-prova"
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
def take(page: str, root: Path, size: tuple[int, int] = SIZE) -> tuple[Path | None, str]:
    """La foto della pagina col codice in «root» (la copia personale) → (file png, problema)."""
    out = Path(tempfile.mkdtemp(prefix="aios-prova-")) / "pagina.png"
    title = f"aios-prova-{os.getpid()}"
    code = ("import sys; sys.path.insert(0, %r); from aios_copilot import anteprima; "
            "sys.exit(anteprima.child_main(sys.argv[1:]))") % str(root)
    cmd = [sys.executable, "-c", code, str(out), page, str(size[0]), str(size[1]), title]
    env = {**os.environ, "AIOS_CODICE": "base", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("LD_PRELOAD", None)
    log = out.parent / "errori.txt"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        # Hyprland apre la finestra direttamente nello spazio nascosto: sullo schermo non compare niente
        keep = " ".join(f"{k}={shlex.quote(env[k])}" for k in ("AIOS_CODICE", "PYTHONDONTWRITEBYTECODE", "XDG_DATA_HOME",
                                                                 "XDG_STATE_HOME", "XDG_CONFIG_HOME", "HOME") if k in env)
        line = f"env {keep} {shlex.join(cmd)} > {shlex.quote(str(log))} 2>&1"
        subprocess.run(["hyprctl", "dispatch", "exec", f"[workspace {WORKSPACE} silent; noinitialfocus] sh -c {shlex.quote(line)}"],
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


def look(image: Path, request: str, question: str = "", model: str | None = None,
         see: Callable[[Path, str], str] | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now()
    prompt = LOOK_PROMPT.format(request=request.strip(), time=now.strftime("%H:%M"), day=GIORNI[now.weekday()],
                                question=f"Controlla in particolare: {question.strip()}" if question.strip() else "")
    if see is None:
        from . import engines

        model = model or vision_model()
        if model is None:
            return ""
        return engines.describe_image(image, prompt, model)
    return see(image, prompt)


def page_report(image: Path) -> dict[str, list[str]]:
    """Gli errori di JavaScript e i problemi trovati nella pagina senza modello (accanto alla foto)."""
    try:
        data = json.loads(image.with_suffix(".json").read_text())
        return {"errori": [str(x) for x in data.get("errori", [])], "problemi": [str(x) for x in data.get("problemi", [])]}
    except (OSError, ValueError, AttributeError):
        return {"errori": [], "problemi": []}


def base_root() -> Path:
    """Il codice originale dell'immagine (quello senza personalizzazioni)."""
    from . import BASE_DIR

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
        self.shoot_base = shoot_base or (lambda page: take(page, base_root()))
        self.last: Path | None = None
        self.failed = False  # la foto non si fa su questo PC: niente controllo visivo
        self.broken = False  # l'ultima occhiata ha trovato errori o problemi nuovi
        self._before: dict[str, dict[str, list[str]]] = {}

    def before(self, page: str) -> dict[str, list[str]]:
        """I problemi che la pagina aveva già col codice originale: non sono colpa della modifica."""
        if page not in self._before:
            image, _ = self.shoot_base(page)
            self._before[page] = page_report(image) if image else {"errori": [], "problemi": []}
        return self._before[page]

    def available(self) -> bool:
        return not self.failed and (self.see is not None or can_render())

    def __call__(self, page: str, question: str = "") -> str:
        image, problem = self.shoot(page)
        if image is None:  # non dipende dalla modifica (i moduli si caricano, lo dice controlla): si va avanti senza
            self.failed = True
            return f"La foto della pagina «{page}» qui non si riesce a fare ({problem[:120]}): rileggi bene la logica."
        self.last = image
        report, old = page_report(image), self.before(page)
        report = {k: [x for x in v if x not in old[k]] for k, v in report.items()}
        self.broken = bool(report["errori"] or report["problemi"])
        lines = []
        if report["errori"]:
            lines.append("Errori di JavaScript nella pagina (da correggere):\n" + "\n".join(f"- {e}" for e in report["errori"]))
        if report["problemi"]:
            lines.append("Problemi nuovi rispetto ad AIOS originale (da correggere):\n"
                         + "\n".join(f"- {e}" for e in report["problemi"]))
        if not lines:
            lines.append("Nessun errore di JavaScript, niente testi tagliati, sovrapposti o poco leggibili.")
        if self.see is not None or (self.model or vision_model()):
            answer = look(image, self.request, question, model=self.model, see=self.see)
            if answer:
                lines.append("Il modello di visione guarda la foto e dice:\n" + answer)
        else:
            lines.append("(Nessun modello di visione sul PC: rileggi bene la logica di quello che si vede.)")
        return f"Pagina «{page}»:\n" + "\n\n".join(lines)


if __name__ == "__main__":
    sys.exit(child_main(sys.argv[1:]))
