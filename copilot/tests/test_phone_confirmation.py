"""Conferme remote: una risposta vecchia non autorizza l'azione successiva."""

import threading
import time
from types import SimpleNamespace

import pytest

from aios_copilot.localapp import Job
from aios_copilot.mesh.delegate import handle_api
from aios_copilot.tools.base import Tool, params


def test_confirmation_ids_prevent_replay_and_preserve_cancellation():
    job = Job()
    tool = Tool("send_email", "Invia una mail", params(to="Destinatario"), lambda to: "Fatto")
    answers = []

    def run():
        answers.append(job.ask_confirmation(tool, {"to": "Giulia"}, warning="Dati privati"))
        answers.append(job.ask_confirmation(tool, {"to": "Marco"}))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    def pending(after=0):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = job.snapshot(0)["pending"]
            if value and value["id"] > after:
                return value
            time.sleep(0.005)
        pytest.fail("La conferma non è arrivata")

    first = pending()
    assert first["warning"] == "Dati privati"
    assert not job.confirm(True, first["id"] + 1)
    assert job.confirm(False, first["id"])
    second = pending(first["id"])
    assert not job.confirm(True, first["id"])
    assert job.confirm(True, second["id"])
    worker.join(2)
    assert not worker.is_alive() and answers == [False, True]
    assert not job.confirm(True, second["id"])


@pytest.mark.parametrize("body", [{}, {"ok": "false"}, {"ok": 1}, {"ok": True, "id": False},
                                  {"ok": True, "id": 0}, {"ok": True, "id": "1"}, {"ok": True, "id": None}])
def test_remote_confirmation_rejects_malformed_decisions(body):
    server = SimpleNamespace(brain=None, assistant=SimpleNamespace(jobs={"7": Job()}))
    assert handle_api(server, "POST", "/api/job/7/conferma", body, "")[0] == 400
