"""Finestra del Copilota (GTK4).

È un'applicazione a istanza singola: richiamarla di nuovo (es. con Super+Spazio)
riporta in primo piano la finestra già aperta invece di aprirne un'altra.
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

CSS = b"""
.copilot-window { background: alpha(@window_bg_color, 0.96); }
.prompt { font-size: 1.3em; padding: 10px; }
.bubble { padding: 8px 12px; border-radius: 12px; }
.user { background: alpha(@accent_bg_color, 0.25); }
.assistant { background: alpha(@view_bg_color, 0.9); }
.status { opacity: 0.6; font-style: italic; }
"""


class CopilotWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, agent: Agent):
        super().__init__(application=app, title="Copilota")
        self.agent = agent
        self.set_default_size(640, 560)
        self.add_css_class("copilot-window")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{side}")(12)
        self.set_child(root)

        self.entry = Gtk.Entry(placeholder_text="Chiedimi qualsiasi cosa: «installa VLC», «cerca…»")
        self.entry.add_css_class("prompt")
        self.entry.connect("activate", self.on_submit)
        root.append(self.entry)

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
        entry.set_sensitive(False)
        self.add_message(text, "user")
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

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

    def do_activate(self) -> None:
        if self.window is None:
            # L'agente chiede conferma tramite la finestra, che viene creata subito dopo.
            holder: dict[str, CopilotWindow] = {}
            agent = self.make_agent(lambda tool, args, **kw: holder["w"].confirm(tool, args, **kw))
            self.window = holder["w"] = CopilotWindow(self, agent)
        self.window.present()
        self.window.entry.grab_focus()


def run(make_agent) -> int:
    return CopilotApp(make_agent).run(None)
