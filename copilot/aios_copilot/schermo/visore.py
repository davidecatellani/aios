"""La finestra che mostra lo schermo di un altro PC e gli passa mouse e tastiera.

Il video si decodifica con GStreamer, con la scheda video quando può (VA-API, NVDEC; altrimenti
libavcodec), e si disegna direttamente in GTK 4 (gtk4paintablesink) senza copie in Python. Mouse e
tastiera si mandano come eventi piccoli (protocollo.py): i movimenti al massimo ogni 8 ms, l'ultimo vince.

Il suono dell'altro PC arriva in Opus (10 ms a pacchetto) e si può spegnere col tasto dell'altoparlante.
Gli appunti sono condivisi finché la finestra è aperta (testo, immagini, file: appunti.py); i file trascinati
nella finestra, o scelti con «Manda file», finiscono negli Scaricati dell'altro PC.

Scorciatoie che restano a questa finestra (non vanno all'altro PC):
    Ctrl+Alt+F  schermo intero        Ctrl+Alt+Q  chiudi
"""

from __future__ import annotations

import threading
import time
from typing import Any

from . import protocollo as P

MOVE_EVERY = 0.008
GTK_TO_EVDEV = 8  # su Linux il codice tasto di GTK è quello del kernel + 8


def fit(widget_w: float, widget_h: float, video_w: float, video_h: float) -> tuple[float, float, float, float]:
    """Il rettangolo del video dentro la finestra (proporzioni mantenute, bande ai lati) → x, y, w, h."""
    if min(widget_w, widget_h, video_w, video_h) <= 0:
        return 0.0, 0.0, max(widget_w, 0.0), max(widget_h, 0.0)
    s = min(widget_w / video_w, widget_h / video_h)
    w, h = video_w * s, video_h * s
    return (widget_w - w) / 2, (widget_h - h) / 2, w, h


def to_norm(x: float, y: float, rect: tuple[float, float, float, float]) -> tuple[int, int]:
    rx, ry, rw, rh = rect
    nx = (x - rx) / rw if rw else 0.0
    ny = (y - ry) / rh if rh else 0.0
    clamp = lambda v: int(min(1.0, max(0.0, v)) * 65535)  # noqa: E731
    return clamp(nx), clamp(ny)


def decoders(find: Any = None) -> list[str]:
    """I codec che questo PC sa decodificare, dal più compresso."""
    if find is None:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            Gst.init(None)
            find = lambda name: Gst.ElementFactory.find(name) is not None  # noqa: E731
        except (ImportError, ValueError):
            return ["jpeg"]
    out = []
    if find("h265parse") and any(find(d) for d in ("vah265dec", "nvh265dec", "avdec_h265")):
        out.append("hevc")
    if find("h264parse") and any(find(d) for d in ("vah264dec", "nvh264dec", "avdec_h264", "openh264dec")):
        out.append("h264")
    return out + ["jpeg"]


PIPELINE = ("appsrc name=src is-live=true do-timestamp=true format=time caps={caps} ! {parse} ! "
            "decodebin ! videoconvert ! gtk4paintablesink name=sink sync=false")
CAPS = {"h264": ("video/x-h264,stream-format=byte-stream", "h264parse"),
        "hevc": ("video/x-h265,stream-format=byte-stream", "h265parse")}


AUDIO_PIPELINE = ("appsrc name=asrc is-live=true do-timestamp=true format=time caps=audio/ogg ! oggdemux ! opusdec ! "
                  "audioconvert ! audioresample ! autoaudiosink sync=false")


def pipeline_text(codec: str) -> str:
    caps, parse = CAPS[codec]
    return PIPELINE.format(caps=caps, parse=parse)


def watch(identity: Any, host: str, port: int, name: str, quality: str = "alta") -> int:  # pragma: no cover - interfaccia
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gdk, GLib, Gst, Gtk

    from .servizio import open_channel

    Gst.init(None)
    state: dict[str, Any] = {"codec": "", "w": 0, "h": 0, "src": None, "pipe": None, "pending": [], "rtt": None,
                             "last_move": 0.0, "move": None, "bytes": 0, "t": time.monotonic(), "rate": 0.0}
    lock = threading.Lock()
    try:
        ch = open_channel(identity, host, port)
    except (OSError, P.HandshakeError) as exc:
        print(f"Non riesco a collegarmi a {name}: {exc}")
        return 1

    app = Gtk.Application(application_id="org.aios.Schermo")

    def build(app: Gtk.Application) -> None:
        win = Gtk.ApplicationWindow(application=app, title=f"{name} — Schermo AIOS")
        win.set_default_size(1280, 760)
        header = Gtk.HeaderBar()
        info = Gtk.Label(label="collegamento…")
        info.add_css_class("dim-label")
        header.pack_start(info)
        quality_box = Gtk.DropDown.new_from_strings(["alta", "media", "bassa"])
        quality_box.set_selected(["alta", "media", "bassa"].index(quality) if quality in ("alta", "media", "bassa") else 0)
        header.pack_end(quality_box)
        sound = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic", active=True)
        sound.set_tooltip_text("Suono dell'altro PC")
        header.pack_end(sound)
        send_btn = Gtk.Button.new_from_icon_name("document-send-symbolic")
        send_btn.set_tooltip_text("Manda file all'altro PC (o trascinali qui)")
        header.pack_end(send_btn)
        full = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        full.set_tooltip_text("Schermo intero (Ctrl+Alt+F)")
        header.pack_end(full)
        win.set_titlebar(header)

        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_can_shrink(True)
        picture.set_focusable(True)
        overlay = Gtk.Overlay()
        overlay.set_child(picture)
        message = Gtk.Label(label=f"Mi collego a {name}…")
        message.add_css_class("title-2")
        overlay.add_overlay(message)
        win.set_child(overlay)

        def send_events(events: list[tuple]) -> None:
            try:
                ch.send(P.INPUT, P.pack_events(events))
            except OSError:
                pass

        def rect() -> tuple[float, float, float, float]:
            return fit(picture.get_width(), picture.get_height(), state["w"], state["h"])

        def flush_move() -> bool:
            pos, state["move"] = state["move"], None
            if pos is not None:
                state["last_move"] = time.monotonic()
                send_events([(P.EV_MOVE, *pos)])
            return False

        def on_motion(_c: Any, x: float, y: float) -> None:
            state["move"] = to_norm(x, y, rect())
            wait = MOVE_EVERY - (time.monotonic() - state["last_move"])
            if wait <= 0:
                flush_move()
            else:
                GLib.timeout_add(max(1, int(wait * 1000)), flush_move)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", on_motion)
        picture.add_controller(motion)

        click = Gtk.GestureClick()
        click.set_button(0)

        def on_press(g: Any, _n: int, x: float, y: float) -> None:
            picture.grab_focus()
            state["move"] = to_norm(x, y, rect())
            flush_move()
            send_events([(P.EV_BUTTON, g.get_current_button(), 1)])

        def on_release(g: Any, _n: int, _x: float, _y: float) -> None:
            send_events([(P.EV_BUTTON, g.get_current_button(), 0)])

        click.connect("pressed", on_press)
        click.connect("released", on_release)
        picture.add_controller(click)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)

        def on_scroll(_c: Any, dx: float, dy: float) -> bool:
            send_events([(P.EV_WHEEL, max(-32000, min(32000, int(dx * 120))), max(-32000, min(32000, int(dy * 120))))])
            return True

        scroll.connect("scroll", on_scroll)
        picture.add_controller(scroll)

        keys = Gtk.EventControllerKey()

        def local_shortcut(keyval: int, mods: Any) -> bool:
            ctrl_alt = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
            if (mods & ctrl_alt) != ctrl_alt:
                return False
            k = Gdk.keyval_to_lower(keyval)
            if k == Gdk.KEY_f:
                toggle_full()
                return True
            if k == Gdk.KEY_q:
                win.close()
                return True
            return False

        def on_key(_c: Any, keyval: int, keycode: int, mods: Any) -> bool:
            if local_shortcut(keyval, mods):
                send_events([(P.EV_RELEASE_ALL,)])
                return True
            send_events([(P.EV_KEY, keycode - GTK_TO_EVDEV, 1)])
            return True

        def on_key_up(_c: Any, _keyval: int, keycode: int, _mods: Any) -> None:
            send_events([(P.EV_KEY, keycode - GTK_TO_EVDEV, 0)])

        keys.connect("key-pressed", on_key)
        keys.connect("key-released", on_key_up)
        win.add_controller(keys)
        win.connect("notify::is-active", lambda w, _p: None if w.is_active() else send_events([(P.EV_RELEASE_ALL,)]))

        def toggle_full(*_a: Any) -> None:
            win.unfullscreen() if win.is_fullscreen() else win.fullscreen()

        full.connect("clicked", toggle_full)

        def on_quality(box: Any, _p: Any) -> None:
            try:
                ch.send_json(P.CONTROL, {"tipo": "qualita", "qualita": ["alta", "media", "bassa"][box.get_selected()]})
            except OSError:
                pass

        quality_box.connect("notify::selected", on_quality)

        # --- suono ---
        def start_audio() -> None:
            if state.get("apipe") is not None:
                return
            try:
                apipe = Gst.parse_launch(AUDIO_PIPELINE)
            except GLib.Error:
                return
            apipe.set_state(Gst.State.PLAYING)
            state["apipe"], state["asrc"] = apipe, apipe.get_by_name("asrc")

        def on_sound(btn: Any) -> None:
            on = btn.get_active()
            btn.set_icon_name("audio-volume-high-symbolic" if on else "audio-volume-muted-symbolic")
            if state.get("apipe") is not None:
                state["apipe"].set_state(Gst.State.PLAYING if on else Gst.State.PAUSED)
            try:
                ch.send_json(P.CONTROL, {"tipo": "audio", "acceso": on})
            except OSError:
                pass

        sound.connect("toggled", on_sound)

        # --- appunti e file ---
        from . import appunti as A
        from .servizio import fetch_offer, notify, send_files_to

        def upload(paths: list[Any], scope: str) -> None:
            size = A.total_size(paths)
            if scope == "appunti" and size > A.CLIP_FILES_MAX:
                notify(f"{A.describe(paths)} pesa {A.human(size)}: per mandarlo a {name} trascinalo nella finestra.")
                return

            def go() -> None:
                GLib.idle_add(info.set_label, f"Mando {A.describe(paths)} a {name}…")
                try:
                    names = send_files_to(identity, host, port, paths, scope)
                    if scope == "file":
                        notify(f"Mandato {A.describe(paths)} a {name}: è nei suoi Scaricati." if names else f"{name} non l'ha accettato.")
                except (OSError, P.HandshakeError) as exc:
                    notify(f"Non riesco a mandare {A.describe(paths)} a {name}: {exc}")

            threading.Thread(target=go, name="schermo-invio", daemon=True).start()

        def fetch(offer: dict[str, Any]) -> list[Any]:
            size = int(offer.get("dimensione", 0))
            if size > A.CLIP_FILES_MAX:
                notify(f"Su {name} hai copiato {A.human(size)} di file: troppo per gli appunti, usa «Manda file» da lì.")
                return []
            try:
                return fetch_offer(identity, host, port, offer, A.clipboard_dir())
            except (OSError, P.HandshakeError):
                return []

        clip = A.Clipboard()
        state["sync"] = A.ClipboardSync(clip, lambda payload: ch.send(P.CLIPBOARD, payload),
                                        on_local_files=lambda paths: upload(paths, "appunti"), fetch_files=fetch) if clip.available() else None

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)

        def on_drop(_t: Any, value: Any, _x: float, _y: float) -> bool:
            from pathlib import Path

            paths = [Path(f.get_path()) for f in value.get_files() if f.get_path()]
            if paths:
                upload(paths, "file")
            return bool(paths)

        drop.connect("drop", on_drop)
        picture.add_controller(drop)

        def choose_files(*_a: Any) -> None:
            dialog = Gtk.FileDialog(title=f"Manda file a {name}")

            def done(d: Any, res: Any) -> None:
                from pathlib import Path

                try:
                    files = d.open_multiple_finish(res)
                except GLib.Error:
                    return
                paths = [Path(files.get_item(i).get_path()) for i in range(files.get_n_items()) if files.get_item(i).get_path()]
                if paths:
                    upload(paths, "file")

            dialog.open_multiple(win, None, done)

        send_btn.connect("clicked", choose_files)

        # --- dalla rete ---
        def configure(cfg: dict[str, Any]) -> bool:
            if cfg.get("errore"):
                message.set_label(cfg["errore"])
                message.set_visible(True)
                return False
            state["w"], state["h"] = cfg.get("larghezza", 0), cfg.get("altezza", 0)
            codec = cfg.get("codec", "")
            old = state["pipe"]
            with lock:
                state["src"], state["pipe"], state["codec"] = None, None, codec
            if old is not None:
                old.set_state(Gst.State.NULL)
            if codec in CAPS:
                pipe = Gst.parse_launch(pipeline_text(codec))
                picture.set_paintable(pipe.get_by_name("sink").get_property("paintable"))
                pipe.set_state(Gst.State.PLAYING)
                with lock:
                    state["pipe"], state["src"] = pipe, pipe.get_by_name("src")
                    pending, state["pending"] = state["pending"], []
                for data in pending:
                    state["src"].emit("push-buffer", Gst.Buffer.new_wrapped(data))
            message.set_visible(False)
            if cfg.get("audio"):
                start_audio()
            sync = state.get("sync")
            if sync is not None and cfg.get("appunti") and not state.get("sync_on"):
                state["sync_on"] = True
                sync.start()
            ctrl = "" if cfg.get("comandi") else " · solo visione"
            state["label"] = f"{codec.upper()} {cfg.get('codificatore', '')} · {state['w']}×{state['h']}{ctrl}"
            info.set_label(state["label"])
            picture.grab_focus()
            return False

        def show_jpeg(data: bytes) -> bool:
            try:
                picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
            except GLib.Error:
                pass
            return False

        def closed(reason: str) -> bool:
            message.set_label(reason or f"{name} si è scollegato.")
            message.set_visible(True)
            return False

        def network() -> None:
            reason = ""
            try:
                ch.send_json(P.CONTROL, {"tipo": "avvia", "codec": decoders(), "qualita": quality, "audio": True,
                                         "appunti": state.get("sync") is not None})
                while True:
                    kind, payload = ch.recv()
                    if kind == P.VIDEO:
                        state["bytes"] += len(payload)
                        if state["codec"] == "jpeg":
                            GLib.idle_add(show_jpeg, payload)
                            continue
                        with lock:
                            src = state["src"]
                            if src is None:
                                state["pending"].append(payload)
                        if src is not None:
                            src.emit("push-buffer", Gst.Buffer.new_wrapped(payload))
                    elif kind == P.AUDIO:
                        asrc = state.get("asrc")
                        if asrc is not None:
                            asrc.emit("push-buffer", Gst.Buffer.new_wrapped(payload))
                    elif kind == P.CLIPBOARD:
                        if state.get("sync") is not None:
                            state["sync"].remote(payload)
                    elif kind == P.CONFIG:
                        GLib.idle_add(configure, P.parse_json(payload))
                    elif kind == P.CONTROL:
                        msg = P.parse_json(payload)
                        if msg.get("tipo") == "pong" and isinstance(msg.get("t"), (int, float)):
                            state["rtt"] = (time.monotonic() - msg["t"]) * 1000
                    elif kind == P.BYE:
                        reason = P.parse_json(payload).get("motivo", "")
                        break
            except (OSError, P.DecryptError):
                pass
            GLib.idle_add(closed, reason)

        def tick() -> bool:
            now = time.monotonic()
            state["rate"] = state["bytes"] * 8 / max(0.001, now - state["t"]) / 1e6
            state["bytes"], state["t"] = 0, now
            try:
                ch.send_json(P.CONTROL, {"tipo": "ping", "t": now})
            except OSError:
                return False
            if state.get("label"):
                rtt = f" · {state['rtt']:.0f} ms" if state["rtt"] is not None else ""
                info.set_label(f"{state['label']} · {state['rate']:.1f} Mbit/s{rtt}")
            return True

        threading.Thread(target=network, name="schermo-rete", daemon=True).start()
        GLib.timeout_add(int(2000), tick)

        def on_close(_w: Any) -> bool:
            ch.close("chiuso")
            if state.get("sync") is not None:
                state["sync"].stop()
            for key in ("pipe", "apipe"):
                if state.get(key) is not None:
                    state[key].set_state(Gst.State.NULL)
            return False

        win.connect("close-request", on_close)
        win.present()
        win.maximize()

    app.connect("activate", build)
    return app.run([])
