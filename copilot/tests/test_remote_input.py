import json
import ssl
import urllib.request

import pytest

from aios_copilot.mesh.files import Devices, FileShare, PhoneServer
from aios_copilot.mesh.remote_input import InputInjector
from aios_copilot.tools.base import Runner


class Recorder(Runner):
    def __init__(self, backend):
        super().__init__(which=lambda p: p if p == backend else None)
        self.ran = []

    def run(self, cmd):
        self.ran.append(cmd)
        return 0, ""


@pytest.mark.parametrize("backend, text, key, move, click", [
    ("ydotool", ["ydotool", "type", "--", "-rm ciao"], ["ydotool", "key", "28:1", "28:0"],
     ["ydotool", "mousemove", "-x", "10", "-y", "-5"], ["ydotool", "click", "0xC1"]),
    ("xdotool", ["xdotool", "type", "--clearmodifiers", "--", "-rm ciao"], ["xdotool", "key", "--clearmodifiers", "Return"],
     ["xdotool", "mousemove_relative", "--", "10", "-5"], ["xdotool", "click", "3"]),
    ("wtype", ["wtype", "--", "-rm ciao"], ["wtype", "-k", "Return"], None, None),
])
def test_backends(backend, text, key, move, click):
    r = Recorder(backend)
    inj = InputInjector(r)
    assert inj.handle({"tipo": "testo", "testo": "-rm ciao"}) and r.ran[-1] == text  # «--»: il testo non diventa un'opzione
    assert inj.handle({"tipo": "tasto", "tasto": "invio"}) and r.ran[-1] == key
    assert inj.handle({"tipo": "muovi", "dx": 10, "dy": -5}) == (move is not None)
    if move:
        assert r.ran[-1] == move
        assert inj.handle({"tipo": "click", "tasto": "destro"}) and r.ran[-1] == click
    assert not inj.handle({"tipo": "tasto", "tasto": "spegni"})  # solo tasti dell'elenco
    assert not inj.handle({"tipo": "muovi", "dx": "tanto"})


def test_input_requires_a_paired_phone(tmp_path):
    r = Recorder("ydotool")
    srv = PhoneServer(FileShare(tmp_path), Devices(tmp_path / "d.json"))
    srv.input = InputInjector(r)
    port = srv.start("127.0.0.1", 0, cert_dir=tmp_path)
    key = srv.devices.add("Pixel")
    ctx = ssl.create_default_context(cafile=str(tmp_path / "pc.crt"))
    ctx.check_hostname = False

    def post(body, auth=True):
        req = urllib.request.Request(f"https://127.0.0.1:{port}/api/input", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if auth else {})})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        assert post({"eventi": [{"tipo": "testo", "testo": "ciao"}, {"tipo": "tasto", "tasto": "invio"}]}) == 200
        assert r.ran == [["ydotool", "type", "--", "ciao"], ["ydotool", "key", "28:1", "28:0"]]
        assert post({"tipo": "testo", "testo": "intruso"}, auth=False) == 403 and len(r.ran) == 2
    finally:
        srv.stop()
