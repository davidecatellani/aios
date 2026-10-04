"""Finestra di Nova (GTK4), con la grafica di AIOS.

È un'applicazione a istanza singola: richiamarla di nuovo (es. con Super+Spazio)
riporta in primo piano la finestra già aperta invece di aprirne un'altra.
Le richieste a voce (voice.py) arrivano con l'azione «voce»: la risposta si legge
anche ad alta voce e le conferme si danno dicendo «sì» o «no».
"""

from __future__ import annotations

import threading
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .agent import Agent  # noqa: E402
from .llm import LLMError  # noqa: E402
from .status import describe_call  # noqa: E402
from .tools import Tool  # noqa: E402

APP_ID = "org.aios.Copilot"

# Colori del marchio (docs/BRAND.md). Solo colori espliciti: i nomi di libadwaita
# (@window_bg_color…) senza libadwaita non esistono e la finestra diventava trasparente.
CSS = b"""
window.nova { background-color: #0A2A3A; background-image: linear-gradient(160deg, #0B3448 0%, #0A2A3A 55%, #071E2A 100%); color: #EAF4F4; }
.nova .titolo { font-size: 1.5em; font-weight: 700; color: #FFFFFF; }
.nova .stato { color: #9FC9CC; font-size: 0.95em; }
.nova entry.prompt { font-size: 1.25em; padding: 10px 16px; border-radius: 22px; background-color: #0F3B50; color: #FFFFFF; border: 1px solid #2EC4B6; caret-color: #E9C46A; }
.nova .bubble { padding: 10px 14px; border-radius: 16px; }
.nova .user { background-color: #0B6E99; color: #FFFFFF; }
.nova .assistant { background-color: #12394B; color: #EAF4F4; }
.nova .status { color: #7FB3B8; font-style: italic; }
.nova button.mic { font-size: 1.4em; border-radius: 22px; min-width: 44px; min-height: 44px; background-image: none; background-color: #0F3B50; color: #2EC4B6; border: 1px solid #2EC4B6; }
.nova button.mic.attivo { background-color: #2EC4B6; color: #0A2A3A; }
.nova button.suggested-action { background-image: none; background-color: #2EC4B6; color: #0A2A3A; border-radius: 12px; }
.nova button { border-radius: 12px; }
.nova scrollbar { background-color: transparent; }
"""


def orb_widget(size: int = 56) -> Gtk.Widget:
    from .branding import BRAND_DIR

    path = BRAND_DIR / "copilota.svg"
    if path.exists():
        image = Gtk.Image.new_from_file(str(path))  # un'icona di misura fissa (Gtk.Picture si allargava)
        image.set_pixel_size(size)
        image.set_valign(Gtk.Align.CENTER)
        return image
    return Gtk.Label(label="●")


class CopilotWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, agent: Agent):
        super().__init__(application=app, title="Nova")
        self.agent = agent
        self.by_voice = False  # l'ultima richiesta è arrivata a voce: rispondo anche a voce
        self.set_default_size(680, 600)
        self.add_css_class("nova")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{side}")(18)
        self.set_child(root)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.orb = orb_widget()
        head.append(self.orb)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label="Nova", xalign=0)
        title.add_css_class("titolo")
        self.state = Gtk.Label(label="Di' «Nova» e parla, oppure scrivi qui sotto.", xalign=0)
        self.state.add_css_class("stato")
        titles.append(title)
        titles.append(self.state)
        head.append(titles)
        root.append(head)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry = Gtk.Entry(placeholder_text="Chiedimi qualsiasi cosa: «installa VLC», «trova la bolletta»…",
                               hexpand=True)
        self.entry.add_css_class("prompt")
        self.entry.connect("activate", self.on_submit)
        row.append(self.entry)
        self.mic = Gtk.Button(label="🎙", tooltip_text="Parla con Nova")
        self.mic.add_css_class("mic")
        self.mic.connect("clicked", self.on_mic)
        row.append(self.mic)
        root.append(row)

        self.conversation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, child=self.conversation)
        root.append(self.scroller)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def on_key(self, _ctrl, keyval, _code, _state) -> bool:
        from gi.repository import Gdk

        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        return False

    def add_message(self, text: str, kind: str) -> None:
        label = Gtk.Label(label=text, wrap=True, xalign=0, selectable=True)
        label.add_css_class(kind)
        if kind in ("user", "assistant"):
            label.add_css_class("bubble")
        label.set_halign(Gtk.Align.END if kind == "user" else Gtk.Align.FILL)
        self.conversation.append(label)
        GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self) -> bool:
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def on_submit(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if not text:
            return
        entry.set_text("")
        self.ask(text, by_voice=False)

    def ask(self, text: str, by_voice: bool) -> None:
        self.by_voice = by_voice
        self.entry.set_sensitive(False)
        self.state.set_label("Ci penso…")
        self.add_message(text, "user")
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

    def on_mic(self, _btn) -> None:
        """Il pulsante del microfono: ascolta una frase (anche se l'ascolto continuo è spento)."""
        self.mic.add_css_class("attivo")
        self.state.set_label("Ti ascolto…")

        def listen() -> None:
            text = ""
            try:
                from .voice import Ears, audio_chunks, capture_command

                cmd = capture_command()
                if cmd:
                    chunks = audio_chunks(cmd)
                    try:
                        text = Ears().transcribe(chunks, require_wake=False) or ""
                    finally:
                        chunks.close()
            except Exception as exc:
                GLib.idle_add(self.state.set_label, f"Microfono non disponibile ({exc}).")
            GLib.idle_add(self._heard, text)

        threading.Thread(target=listen, daemon=True).start()

    def _heard(self, text: str) -> bool:
        self.mic.remove_css_class("attivo")
        if text:
            self.ask(text, by_voice=True)
        else:
            self.state.set_label("Non ho sentito: riprova, o scrivi.")
        return False

    def _work(self, text: str) -> None:
        def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "tool_call":
                GLib.idle_add(self.add_message, describe_call(data["tool"], data["args"]), "status")

        try:
            answer = self.agent.ask(text, on_event)
        except LLMError as exc:
            answer = str(exc)
        GLib.idle_add(self._done, answer)

    def _done(self, answer: str) -> bool:
        self.add_message(answer, "assistant")
        self.entry.set_sensitive(True)
        self.entry.grab_focus()
        self.state.set_label("Di' «Nova» e parla, oppure scrivi qui sotto.")
        if self.by_voice:
            from .voice import speak

            threading.Thread(target=speak, args=(answer,), daemon=True).start()
        return False

    def confirm(self, tool: Tool, args: dict[str, Any], warning: str | None = None) -> bool:
        """Chiamata dal thread dell'agente: mostra la richiesta e attende la risposta."""
        answered = threading.Event()
        result = {"ok": False}

        def show() -> bool:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            prefix = f"🔒 {warning}\n" if warning else ""
            label = Gtk.Label(label=f"{prefix}Confermi? {describe_call(tool, args)}", wrap=True, hexpand=True, xalign=0)
            yes, no = Gtk.Button(label="Procedi"), Gtk.Button(label="Annulla")
            yes.add_css_class("suggested-action")

            def answer(_btn, ok: bool) -> None:
                result["ok"] = ok
                yes.set_sensitive(False)
                no.set_sensitive(False)
                label.set_label(label.get_label() + ("  ✔" if ok else "  ✘"))
                answered.set()

            yes.connect("clicked", answer, True)
            no.connect("clicked", answer, False)
            for widget in (label, no, yes):
                box.append(widget)
            self.conversation.append(box)
            GLib.idle_add(self._scroll_to_end)
            yes.grab_focus()
            return False

        GLib.idle_add(show)
        if self.by_voice:  # anche a voce: «Procedo?» → «sì» / «no»
            def by_voice() -> None:
                from .voice import listen_yes_no, speak

                speak(f"{warning + '. ' if warning else ''}Confermi? {describe_call(tool, args)}")
                said = listen_yes_no()
                if said is not None and not answered.is_set():
                    result["ok"] = said
                    answered.set()

            threading.Thread(target=by_voice, daemon=True).start()
        answered.wait()
        return result["ok"]


class CopilotApp(Gtk.Application):
    def __init__(self, make_agent):
        super().__init__(application_id=APP_ID)
        self.make_agent = make_agent
        self.window: CopilotWindow | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        from gi.repository import Gdk

        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        from gi.repository import Gio

        action = Gio.SimpleAction.new("voce", GLib.VariantType.new("s"))
        action.connect("activate", lambda _a, param: self.voice_request(param.get_string()))
        self.add_action(action)

    def _ensure_window(self) -> CopilotWindow:
        if self.window is None:
            # L'agente chiede conferma tramite la finestra, che viene creata subito dopo.
            holder: dict[str, CopilotWindow] = {}
            agent = self.make_agent(lambda tool, args, **kw: holder["w"].confirm(tool, args, **kw))
            self.window = holder["w"] = CopilotWindow(self, agent)
        return self.window

    def do_activate(self) -> None:
        window = self._ensure_window()
        window.present()
        window.entry.grab_focus()

    def voice_request(self, text: str) -> None:
        """Una frase detta a Nova (voice.py): finestra in primo piano, risposta anche a voce."""
        window = self._ensure_window()
        window.present()
        if text.strip():
            window.ask(text.strip(), by_voice=True)


def run(make_agent, voice_text: str | None = None) -> int:
    app = CopilotApp(make_agent)
    if voice_text is not None:
        app.register(None)
        app.activate_action("voce", GLib.Variant("s", voice_text))
        if app.get_is_remote():
            return 0  # Nova era già aperta: ci pensa lei
    return app.run(None)
