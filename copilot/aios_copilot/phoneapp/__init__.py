"""Pagina dell'installatore di AIOS per telefono (aperta da Nova: «installa AIOS sul telefono»)."""

from __future__ import annotations

import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import phoneinstall as pi
from ..localapp import LocalApp, open_window, serve
from ..tools.base import Runner


class InstallApp(LocalApp):
    page = Path(__file__).with_name("index.html")

    def __init__(self, runner: Runner | None = None, download=None):
        super().__init__(None)
        self.runner = runner or Runner()
        self.download = download or (lambda build: pi.download_build(build, pi.log_dir() / "immagini"))
        self.steps: list[pi.Step] = []
        self.question: dict[str, Any] | None = None
        self.result: str = ""
        self.running = False
        self._answer = threading.Event()
        self._value = ""
        self.route("GET", r"/api/telefono", self._phone)
        self.route("POST", r"/api/avvia", self._start)
        self.route("GET", r"/api/stato", self._state)
        self.route("POST", r"/api/risposta", self._reply)

    def _phone(self, m, body, query):
        phone = pi.detect(self.runner)
        problems = pi.preflight(phone, pi.load_catalog())
        return 200, {"telefono": asdict(phone), "nome": phone.label, "problemi": problems}

    def _start(self, m, body, query):
        if self.running:
            return 409, {"error": "installazione già in corso"}
        dry = bool(body.get("prova"))
        phone = pi.detect(self.runner)
        problems = pi.preflight(phone, pi.load_catalog())
        if problems and not dry:
            return 400, {"error": " ".join(problems)}
        system, recovery = pi.choose_build(phone, pi.load_catalog())
        system = system or pi.Build("AIOS (prova)", "0", "gsi", files=[{"nome": "aios-system.img", "partizione": "system"}])
        recovery = recovery or pi.Build("recovery (prova)", "0", "recovery", files=[{"nome": "vbmeta.img", "partizione": "vbmeta"},
                                                                                   {"nome": "recovery.img", "partizione": "recovery"}])
        try:
            files = {} if dry else self.download(system)
            self.steps = pi.plan_for(phone, system, recovery, files)
        except pi.InstallError as exc:
            return 400, {"error": str(exc)}
        self.running, self.result = True, ""
        installer = pi.Installer(self.runner, phone, self.steps, self._ask, dry_run=dry)

        def work():
            ok = installer.run()
            self.result = ("Modalità prova completata: questi sono i passi che farei." if dry else
                           "Fatto: AIOS è installato. Benvenuto nel tuo nuovo telefono!") if ok else "Installazione interrotta."
            self.running, self.question = False, None

        threading.Thread(target=work, daemon=True).start()
        return 200, {"ok": True}

    def _ask(self, step: pi.Step) -> str:
        self._answer.clear()
        self.question = {"id": step.id, "titolo": step.title, "testo": step.text, "tipo": step.kind, "dettaglio": step.detail}
        self._answer.wait()
        self.question = None
        return self._value

    def _state(self, m, body, query):
        return 200, {"passi": [asdict(s) for s in self.steps], "domanda": self.question, "in_corso": self.running,
                     "esito": self.result}

    def _reply(self, m, body, query):
        if self.question is None:
            return 409, {"error": "nessuna domanda in sospeso"}
        self._value = str(body.get("valore", ""))[:100]
        self._answer.set()
        return 200, {"ok": True}


def main(argv: list[str] | None = None) -> int:
    app = InstallApp()
    _, url = serve(app)
    open_window(url, app.finished, "Installa AIOS sul telefono", "org.aios.PhoneInstall", (760, 820))
    return 0


if __name__ == "__main__":
    sys.exit(main())
