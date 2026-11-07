"""Il registro di quello che Nova cambia, per poterlo annullare: «annulla l'ultima cosa che hai fatto»,
«rimetti tutto com'era stamattina», e l'elenco in Impostazioni › Azioni di Nova.

Ogni strumento che cambia qualcosa (agenda, widget, rubrica, categorie della posta, impostazioni, temi, volume,
riordino dei file, personalizzazioni…) passa da qui: prima di eseguirlo si fotografano le «parti» che può toccare,
dopo si confronta, e nel registro resta solo la differenza (com'era → com'è). Annullare rimette «com'era», ma solo
se nel frattempo nessuno ha cambiato di nuovo la stessa cosa: altrimenti lo dice e non tocca niente.
Le cose che non si possono riprendere (una mail inviata) non finiscono qui.

Il registro sta in ~/.local/share/aios/azioni.jsonl: niente esce dal computer. Restano 30 giorni e 300 azioni.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

KEEP_DAYS = 30
KEEP_ITEMS = 300
_lock = threading.Lock()


def path() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "azioni.jsonl"


def load() -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path().read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    cut = time.time() - KEEP_DAYS * 86400
    items = [i for i in items if i.get("quando", 0) >= cut][-KEEP_ITEMS:]
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


# --- le parti che un'azione può cambiare ------------------------------------------------------------------
@dataclass
class Part:
    """Una cosa che un'azione può cambiare: come si legge (capture), com'è la differenza, come si torna indietro."""

    name: str
    capture: Callable[[], Any]
    diff: Callable[[Any, Any], Any]  # (prima, dopo) → differenza da tenere, None se uguale
    undo: Callable[[Any], str]  # differenza → "" se fatto, altrimenti il motivo per cui no


def _read(p: Path) -> str | None:
    try:
        data = p.read_bytes()
    except OSError:
        return None
    try:
        return data.decode()
    except UnicodeDecodeError:
        return "base64:" + base64.b64encode(data).decode()


def _write(p: Path, text: str | None) -> None:
    if text is None:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(base64.b64decode(text[7:]) if text.startswith("base64:") else text.encode())


def files(name: str, paths: Callable[[], list[Path]], reapply: Callable[[], None] = lambda: None) -> Part:
    """File di configurazione (piccoli): si tiene il contenuto di prima e di dopo."""

    def capture() -> dict[str, str | None]:
        return {str(p): _read(p) for p in paths()}

    def diff(before: dict, after: dict) -> Any:
        d = {k: [before.get(k), after.get(k)] for k in after if before.get(k) != after.get(k)}
        return d or None

    def undo(d: dict) -> str:
        if any(_read(Path(k)) != after for k, (_, after) in d.items()):
            return "è stato cambiato di nuovo dopo"
        for k, (before, _) in d.items():
            _write(Path(k), before)
        try:
            reapply()
        except Exception:
            pass
        return ""

    return Part(name, capture, diff, undo)


def rows(name: str, db: Callable[[], Path], table: str, columns: list[str], key: str = "id") -> Part:
    """Righe di una tabella SQLite: si tiene solo quello che è cambiato (aggiunte, tolte, modificate)."""
    cols = [key] + [c for c in columns if c != key]

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(db(), timeout=10)

    def capture() -> dict[str, list]:
        if not db().exists():
            return {}
        with connect() as c:
            try:
                return {str(r[0]): list(r) for r in c.execute(f"SELECT {', '.join(cols)} FROM {table}")}
            except sqlite3.Error:
                return {}

    def diff(before: dict, after: dict) -> Any:
        d = {k: [before.get(k), after.get(k)] for k in set(before) | set(after) if before.get(k) != after.get(k)}
        return {"colonne": cols, "righe": d} if d else None

    def undo(d: dict) -> str:
        now = capture()
        if any(now.get(k) != after for k, (_, after) in d["righe"].items()):
            return "è stato cambiato di nuovo dopo"
        names = d["colonne"]
        with connect() as c:
            for k, (before, after) in d["righe"].items():
                if before is None:  # aggiunta: si toglie
                    c.execute(f"DELETE FROM {table} WHERE {key} = ?", (after[0],))
                elif after is None:  # tolta: si rimette com'era
                    c.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", before)
                else:  # cambiata: solo le colonne seguite (le altre restano come sono)
                    c.execute(f"UPDATE {table} SET {', '.join(f'{n} = ?' for n in names[1:])} WHERE {key} = ?",
                              [*before[1:], before[0]])
        return ""

    return Part(name, capture, diff, undo)


def value(name: str, read: Callable[[], Any], write: Callable[[Any], None]) -> Part:
    """Un valore che si legge e si imposta (volume, luminosità, tema)."""

    def diff(before: Any, after: Any) -> Any:
        return [before, after] if before != after and before is not None else None

    def undo(d: list) -> str:
        if read() != d[1]:
            return "è stato cambiato di nuovo dopo"
        write(d[0])
        return ""

    return Part(name, read, diff, undo)


# --- le parti di SoIA --------------------------------------------------------------------------------------
def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def _agenda_db() -> Path:
    from .agenda import data_dir

    return data_dir() / "agenda.db"


def _mail_db() -> Path:
    from .mail.store import data_dir

    return data_dir() / "mail.db"


def _hypr_reapply() -> None:
    from . import hyprconf

    hyprconf.write()
    hyprconf.hyprctl(["reload"])


def _hypr_files(*names: str) -> Callable[[], list[Path]]:
    def paths() -> list[Path]:
        from . import hyprconf

        return [hyprconf.config_dir() / f"{n}.json" for n in names]

    return paths


def _quiet_reapply() -> None:
    from . import notifiche

    notifiche.apply_mode(bool(notifiche.quiet_now()))


def _volume_read() -> Any:
    from .shell.apps import volume_state

    s = volume_state()
    return [s["livello"], s["muto"]] if s["livello"] is not None else None


def _volume_write(v: list) -> None:
    _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{v[0]}%"])
    _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if v[1] else "0"])


def _brightness_read() -> Any:
    from .shell.apps import brightness_state

    return brightness_state()["livello"]


def _color_scheme_read() -> Any:
    code, out = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    return out.strip("'") if code == 0 and out else None


def _theme_write(theme_id: str) -> None:
    from . import themeapply

    theme = themeapply.find(theme_id)
    if theme is not None:
        themeapply.apply(theme)


def _theme_read() -> Any:
    from . import themeapply

    return themeapply.current_id()


def parts() -> dict[str, Part]:
    from . import luce_notturna, notifiche, rubrica, widget

    agenda = [rows("agenda", _agenda_db, "events", ["title", "start", "end", "all_day", "location", "notes", "repeat",
                                                     "source", "created"]),
              rows("promemoria", _agenda_db, "reminders", ["title", "due", "repeat", "done", "source", "created"]),
              rows("scadenze", _agenda_db, "suggestions", ["title", "due", "source", "status"])]
    return {p.name: p for p in [
        *agenda,
        files("widget", lambda: [widget.store_path()]),
        files("rubrica", lambda: [rubrica.own_path()]),
        rows("posta-regole", _mail_db, "overrides", ["category"], key="pattern"),
        rows("posta-categorie", _mail_db, "messages", ["category", "reason"]),
        files("luce-notturna", lambda: [luce_notturna.config_path()]),
        files("non-disturbare", lambda: [notifiche._conf()], _quiet_reapply),
        files("schermi", _hypr_files("monitor"), _hypr_reapply),
        files("accessibilita", _hypr_files("accessibilita"), _hypr_reapply),
        files("mouse-tastiera", _hypr_files("dispositivi"), _hypr_reapply),
        value("volume", _volume_read, _volume_write),
        value("luminosita", _brightness_read, lambda v: _run(["brightnessctl", "set", f"{v}%"])),
        value("tema-chiaro-scuro", _color_scheme_read,
              lambda v: _run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", v])),
        value("tema", _theme_read, _theme_write),
    ]}


AGENDA = ["agenda", "promemoria", "scadenze"]
TOUCHES: dict[str, list[str]] = {
    "add_reminder": AGENDA, "add_event": AGENDA, "complete_reminder": AGENDA, "delete_agenda_item": AGENDA,
    "resolve_suggestion": AGENDA,
    "add_widget": ["widget"], "remove_widget": ["widget"],
    "add_contact": ["rubrica"],
    "categorize_mail": ["posta-regole", "posta-categorie"],
    "night_light": ["luce-notturna"], "do_not_disturb": ["non-disturbare"],
    "set_display": ["schermi"], "accessibility": ["accessibilita"],
    "mouse_settings": ["mouse-tastiera"], "add_shortcut": ["mouse-tastiera"],
    "set_volume": ["volume"], "set_brightness": ["luminosita"], "set_theme": ["tema-chiaro-scuro"],
    "apply_theme": ["tema"], "create_theme": ["tema"], "create_theme_from_image": ["tema"], "remix_theme": ["tema"],
    "previous_theme": ["tema"], "market_install": ["tema"],
}

# azioni con un loro modo di tornare indietro
SPECIAL = {"tidy_apply", "customize_system", "apply_pending_customization", "remove_background"}
DESCRIPTIONS = {
    "add_reminder": "Promemoria aggiunto", "add_event": "Appuntamento aggiunto", "complete_reminder": "Promemoria segnato come fatto",
    "delete_agenda_item": "Impegno eliminato", "resolve_suggestion": "Scadenza proposta accettata o ignorata",
    "add_widget": "Widget aggiunto", "remove_widget": "Widget tolto", "add_contact": "Contatto aggiunto o cambiato",
    "categorize_mail": "Mail spostate in una categoria", "night_light": "Luce notturna cambiata",
    "do_not_disturb": "«Non disturbare» cambiato", "set_display": "Schermo cambiato", "accessibility": "Accessibilità cambiata",
    "mouse_settings": "Mouse o touchpad cambiati", "add_shortcut": "Scorciatoia aggiunta", "set_volume": "Volume cambiato",
    "set_brightness": "Luminosità cambiata", "set_theme": "Tema chiaro/scuro cambiato", "apply_theme": "Tema cambiato",
    "create_theme": "Tema nuovo applicato", "create_theme_from_image": "Tema nuovo applicato", "remix_theme": "Tema ritoccato",
    "previous_theme": "Tema di prima rimesso", "market_install": "Tema installato", "tidy_apply": "File riordinati",
    "customize_system": "SoIA personalizzato", "apply_pending_customization": "Personalizzazione applicata",
    "remove_background": "Sfondo tolto da una foto",
}


def _describe(tool: str, args: dict[str, Any]) -> str:
    base = DESCRIPTIONS.get(tool, tool)
    detail = next((str(v) for k, v in args.items() if v not in ("", None) and k in (
        "title", "titolo", "what", "testo", "richiesta", "tipo", "luogo", "name", "nome", "who", "mode", "level",
        "livello", "category", "description", "descrizione", "quale", "percorso", "azione", "attivo")), "")
    return f"{base}: {detail[:80]}" if detail else base


# --- prima e dopo ------------------------------------------------------------------------------------------
def _special_before(tool: str) -> Any:
    if tool in ("customize_system", "apply_pending_customization"):
        from . import codice

        h = codice.history()
        return h[0]["id"] if h else ""
    return None


def _special_after(tool: str, before: Any, result: str) -> dict[str, Any] | None:
    if tool in ("customize_system", "apply_pending_customization"):
        from . import codice

        h = codice.history()
        if h and h[0]["id"] != before:
            return {"tipo": "personalizzazione", "id": h[0]["id"]}
    elif tool == "tidy_apply" and result.strip().lower().startswith(("fatto", "ho ", "spostat", "riordinat")):
        return {"tipo": "riordino"}
    elif tool == "remove_background":
        from .tools import foto

        made = foto.LAST_CUTOUT.get("percorso")
        if made and f"«{Path(made).name}»" in result and Path(made).exists():
            return {"tipo": "file-nuovo", "percorso": made}
    return None


def record(tool: str, args: dict[str, Any], run: Callable[[], str], registry: dict[str, Part] | None = None) -> str:
    """Esegue lo strumento e, se ha cambiato qualcosa, lo annota nel registro."""
    names = TOUCHES.get(tool, [])
    if not names and tool not in SPECIAL:
        return run()
    registry = registry if registry is not None else parts()
    before: dict[str, Any] = {}
    for n in names:
        try:
            before[n] = registry[n].capture()
        except Exception:
            pass
    special_before = _special_before(tool) if tool in SPECIAL else None
    result = run()
    changes: dict[str, Any] = {}
    for n, b in before.items():
        try:
            d = registry[n].diff(b, registry[n].capture())
        except Exception:
            d = None
        if d is not None:
            changes[n] = d
    special = None
    if tool in SPECIAL:
        try:
            special = _special_after(tool, special_before, str(result))
        except Exception:
            special = None
    if changes or special:
        entry = {"id": str(time.time_ns()), "quando": time.time(), "strumento": tool,
                 "descrizione": _describe(tool, args), "cambi": changes, "speciale": special, "annullata": False}
        with _lock:
            items = load()
            items.append(entry)
            _save(items)
    return result


def wrap(tool: Any) -> Any:
    """Lo stesso strumento, che annota nel registro quello che cambia."""
    if tool.name not in TOUCHES and tool.name not in SPECIAL:
        return tool
    func = tool.func

    def recorded(**args: Any) -> str:
        return record(tool.name, args, lambda: func(**args))

    return replace(tool, func=recorded)


# --- annullare ---------------------------------------------------------------------------------------------
def _undo_special(s: dict[str, Any]) -> str:
    if s["tipo"] == "personalizzazione":
        from . import codice

        ok, what = codice.undo(s["id"])
        if ok:
            _restart_shell()
        return "" if ok else what
    if s["tipo"] == "riordino":
        from .organize import undo_tidy

        return "" if undo_tidy() else "non c'era più niente da rimettere a posto"
    if s["tipo"] == "file-nuovo":
        p = Path(s["percorso"])
        if p.exists():
            _run(["gio", "trash", str(p)])
            if p.exists():
                p.unlink()
        return ""
    return "non so come tornare indietro"


def _restart_shell() -> None:
    try:
        from .tools.windows import _shell

        _shell("--riavvia")
    except Exception:
        pass


def undo(entry_id: str, registry: dict[str, Part] | None = None) -> tuple[bool, str]:
    """Annulla un'azione del registro → (fatto, messaggio)."""
    with _lock:
        items = load()
        entry = next((i for i in items if i["id"] == entry_id), None)
        if entry is None:
            return False, "Non trovo quell'azione."
        if entry.get("annullata"):
            return False, f"«{entry['descrizione']}» era già stata annullata."
        registry = registry if registry is not None else parts()
        problems = []
        for n, d in entry.get("cambi", {}).items():
            try:
                why = registry[n].undo(d)
            except Exception as exc:
                why = str(exc) or exc.__class__.__name__
            if why:
                problems.append(why)
        if entry.get("speciale"):
            try:
                why = _undo_special(entry["speciale"])
            except Exception as exc:
                why = str(exc) or exc.__class__.__name__
            if why:
                problems.append(why)
        if problems and len(problems) == len(entry.get("cambi", {})) + bool(entry.get("speciale")):
            return False, f"Non ho annullato «{entry['descrizione']}»: {problems[0]}."
        entry["annullata"] = True
        _save(items)
    msg = f"Annullato: «{entry['descrizione']}»."
    return True, msg + (f" (In parte no: {problems[0]}.)" if problems else "")


def pending(since: float = 0.0) -> list[dict[str, Any]]:
    """Le azioni ancora da annullare, dalla più recente."""
    return [i for i in reversed(load()) if not i.get("annullata") and i.get("quando", 0) >= since]


def undo_last(count: int = 1, registry: dict[str, Part] | None = None) -> str:
    todo = pending()[:max(1, count)]
    if not todo:
        return "Non ho fatto niente da annullare."
    return " ".join(undo(i["id"], registry)[1] for i in todo)


def since(phrase: str, now: datetime | None = None) -> float | None:
    """«stamattina», «oggi», «ieri sera», «nell'ultima ora», «ultimi 20 minuti» → istante da cui annullare."""
    now = now or datetime.now()
    p = phrase.lower()
    m = re.search(r"(\d+)\s*minut", p)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).timestamp()
    m = re.search(r"(\d+)\s*or[ae]", p)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).timestamp()
    if "ultima ora" in p or "un'ora" in p:
        return (now - timedelta(hours=1)).timestamp()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "ieri sera" in p:
        return (midnight - timedelta(hours=6)).timestamp()
    if "ieri" in p:
        return (midnight - timedelta(days=1)).timestamp()
    if any(w in p for w in ("stamattina", "stamane", "oggi", "stamani", "questa mattina")):
        return midnight.timestamp()
    if "pomeriggio" in p:
        return midnight.replace(hour=12).timestamp()
    if "stasera" in p or "questa sera" in p:
        return midnight.replace(hour=18).timestamp()
    return None


def undo_since(when: float, registry: dict[str, Part] | None = None) -> str:
    todo = pending(when)
    if not todo:
        return "Da allora non ho cambiato niente."
    done, failed = 0, []
    for i in todo:  # dalla più recente alla più vecchia: ogni passo ritrova lo stato giusto
        ok, msg = undo(i["id"], registry)
        if ok:
            done += 1
        else:
            failed.append(msg)
    text = f"Ho annullato {done} {'azione' if done == 1 else 'azioni'}."
    return text + (" " + " ".join(failed[:2]) if failed else "")


def describe(limit: int = 12) -> str:
    items = pending()[:limit]
    if not items:
        return "Non ho cambiato niente di recente (o l'ho già annullato)."
    lines = [f"• {datetime.fromtimestamp(i['quando']):%d/%m %H:%M} — {i['descrizione']}" for i in items]
    return "Le ultime cose che ho cambiato (le posso annullare):\n" + "\n".join(lines)
