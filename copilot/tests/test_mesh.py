import json
import os
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from aios_copilot.agent import Agent
from aios_copilot.mesh import calls
from aios_copilot.mesh.calls import Ofono
from aios_copilot.mesh.files import Devices, FileShare, Pairing, PhoneServer
from aios_copilot.mesh.phone import KdeConnect
from aios_copilot.mesh.service import MeshService, send_command
from aios_copilot.tools import mail as mail_tools
from aios_copilot.tools import phone as phone_tools
from aios_copilot.tools.base import Runner


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


class FakeRunner(Runner):
    """kdeconnect-cli e busctl finti."""

    def __init__(self, devices="", calls_json=None):
        super().__init__(which=lambda p: p if p in ("kdeconnect-cli", "busctl") else None)
        self.devices, self.calls_json, self.ran = devices, calls_json, []

    def run(self, cmd):
        self.ran.append(cmd)
        if cmd[:2] == ["kdeconnect-cli", "-l"]:
            return 0, self.devices
        if cmd[0] == "busctl":
            method = cmd[-1]
            if method == "GetModems":
                return 0, json.dumps({"type": "a(oa{sv})", "data": [[["/hfp/org/bluez/hci0/dev_AA", {
                    "Online": {"type": "b", "data": True}}]]]})
            if method == "GetCalls":
                return 0, json.dumps({"type": "a(oa{sv})", "data": [self.calls_json or []]})
            return 0, json.dumps({"type": "", "data": []})
        return 0, ""


DEVICES = ("- Pixel 8: 1a2b3c4d (paired and reachable)\n- Tablet: 99ff (paired)\n"
           "- iPhone di Anna: 7e7e (reachable)\n2 devices found\n")


def test_kdeconnect_devices_and_actions(tmp_path):
    r = FakeRunner(DEVICES)
    kc = KdeConnect(r)
    phones = {p.name: p for p in kc.devices()}
    assert phones["Pixel 8"].paired and phones["Pixel 8"].reachable
    assert phones["Tablet"].paired and not phones["Tablet"].reachable
    assert not phones["iPhone di Anna"].paired and phones["iPhone di Anna"].reachable
    assert [p.name for p in kc.nearby()] == ["Pixel 8"]
    kc.ring(kc.find())
    assert r.ran[-1] == ["kdeconnect-cli", "-d", "1a2b3c4d", "--ring"]


RINGING = [["/hfp/org/bluez/hci0/dev_AA/voicecall01", {"LineIdentification": {"type": "s", "data": "+39 333 1234567"},
                                                        "State": {"type": "s", "data": "incoming"},
                                                        "Name": {"type": "s", "data": ""}}]]


def test_calls_through_ofono():
    r = FakeRunner(calls_json=RINGING)
    o = Ofono(r)
    assert o.incoming().who == "+39 333 1234567"
    assert o.answer().startswith("Risposto a +39 333 1234567")
    assert r.ran[-1][-3:] == ["/hfp/org/bluez/hci0/dev_AA/voicecall01", "org.ofono.VoiceCall", "Answer"]
    assert o.hang_up() == "Chiamata rifiutata." and r.ran[-1][-1] == "Hangup"
    assert Ofono(FakeRunner()).answer() == "Non c'è nessuna chiamata in arrivo."


def test_hands_free_setup(tmp_path):
    r = FakeRunner()
    assert calls.setup_hands_free(r, tmp_path) == []
    conf = (tmp_path / calls.WIREPLUMBER_CONF).read_text()
    assert "hfp_hf" in conf and 'hfphsp-backend = "ofono"' in conf
    assert ["pkexec", "systemctl", "enable", "--now", "ofono.service"] in r.ran


# --- file del PC dal telefono -------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "Documenti").mkdir(parents=True)
    (h / "Documenti/contratto.pdf").write_bytes(b"%PDF-1.4 contratto")
    (h / "Documenti/pagina.html").write_text("<script>alert(1)</script>")
    (h / "Documenti/server.key").write_text("PRIVATA")
    (h / ".ssh").mkdir()
    (h / ".ssh/id_ed25519").write_text("CHIAVE")
    (h / "Foto").mkdir()
    (h / "Foto/mare.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    (tmp_path / "fuori.txt").write_text("fuori dalla home")
    (h / "Documenti/scorciatoia").symlink_to(tmp_path / "fuori.txt")
    return h


def test_share_never_leaves_home_or_shows_private_files(home):
    share = FileShare(home)
    names = [e["name"] for e in share.listing("")["entries"]]
    assert names == ["Documenti", "Foto"]  # niente .ssh
    docs = [e["name"] for e in share.listing("Documenti")["entries"]]
    assert "contratto.pdf" in docs and "server.key" not in docs and "scorciatoia" not in docs
    for bad in ("../fuori.txt", ".ssh/id_ed25519", "Documenti/server.key", "Documenti/scorciatoia", "/etc/passwd",
                "Documenti/../../fuori.txt"):
        assert share.resolve(bad) is None, bad
    assert share.resolve("Documenti/contratto.pdf") == home / "Documenti/contratto.pdf"


def test_pairing_code_is_single_use_and_expires():
    now = [1000.0]
    p = Pairing(clock=lambda: now[0])
    code = p.start()
    assert not p.use("sbagliato") and p.use(code) and not p.use(code)
    code = p.start()
    now[0] += 301
    assert not p.use(code)
    code = p.start()
    for _ in range(10):
        p.use("tentativo")
    assert not p.use(code)  # troppi tentativi: serve un nuovo QR


@pytest.fixture
def server(home, tmp_path):
    def search(q):
        return [{"path": str(home / "Documenti/contratto.pdf"), "snippet": "il «contratto» di affitto"},
                {"path": str(home / ".ssh/id_ed25519"), "snippet": "chiave"}]

    srv = PhoneServer(FileShare(home, search), Devices(tmp_path / "devices.json"))
    port = srv.start("127.0.0.1", 0, cert_dir=tmp_path)
    ctx = ssl.create_default_context(cafile=str(tmp_path / "pc.crt"))
    ctx.check_hostname = False  # certificato del PC: verificato, ma il nome è l'indirizzo in rete locale
    yield srv, f"https://127.0.0.1:{port}", ctx
    srv.stop()


def request(base, ctx, path, key=None, data=None):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def test_phone_page_over_https(server):
    srv, base, ctx = server
    status, headers, body = request(base, ctx, "/")
    assert status == 200 and b"Il mio PC" in body and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert request(base, ctx, "/api/cartella")[0] == 403  # senza abbinamento niente
    assert request(base, ctx, "/api/abbina", data={"code": "inventato", "name": "x"})[0] == 403

    srv.pairing.start()
    status, _, body = request(base, ctx, "/api/abbina", data={"code": srv.pairing.code, "name": "Pixel 8"})
    key = json.loads(body)["key"]
    assert status == 200 and srv.devices.items[0].name == "Pixel 8" and key not in Path(srv.devices.path).read_text()
    assert oct(os.stat(srv.devices.path).st_mode)[-3:] == "600"

    listing = json.loads(request(base, ctx, "/api/cartella?p=Documenti", key)[2])
    assert [e["name"] for e in listing["entries"]] == ["contratto.pdf", "pagina.html"]
    assert request(base, ctx, "/api/cartella?p=..", key)[0] == 404
    results = json.loads(request(base, ctx, "/api/cerca?q=contratto", key)[2])["results"]
    assert [r["path"] for r in results] == ["Documenti/contratto.pdf"]  # la chiave SSH dall'indice non passa

    link = json.loads(request(base, ctx, "/api/link?p=Documenti/contratto.pdf", key)[2])["url"]
    status, headers, body = request(base, ctx, link + "?vedi=1")
    assert body == b"%PDF-1.4 contratto" and headers["Content-Disposition"].startswith("inline")
    link = json.loads(request(base, ctx, "/api/link?p=Documenti/pagina.html", key)[2])["url"]
    assert request(base, ctx, link + "?vedi=1")[1]["Content-Disposition"].startswith("attachment")  # mai HTML eseguito
    assert request(base, ctx, "/api/link?p=.ssh/id_ed25519", key)[0] == 404
    assert request(base, ctx, "/scarica/inventato")[0] == 403
    srv.tickets = {k: (p, time.time() - 1) for k, (p, _) in srv.tickets.items()}
    assert request(base, ctx, link)[0] == 403  # link scaduto

    assert srv.devices.remove("pixel") == 1
    assert request(base, ctx, "/api/cartella", key)[0] == 403  # scollegato: chiave revocata


# --- servizio --------------------------------------------------------------------------------------------


class FakeServer:
    def __init__(self):
        self.running, self.pairing, self.devices, self.httpd = False, Pairing(), Devices(Path(tempfile.mktemp())), None

    def start(self, **kw):
        self.running = True

    def stop(self):
        self.running = False


def test_service_connects_and_disconnects_by_itself():
    r = FakeRunner(DEVICES)
    notes, asked = [], []
    srv = FakeServer()
    svc = MeshService(KdeConnect(r), Ofono(r), srv, notify=lambda t, b: notes.append(t),
                      ask=lambda t, b, a: asked.append(t) or "rispondi")
    svc.tick()
    assert notes == ["📱 Pixel 8 collegato"] and srv.running
    svc.tick()
    assert len(notes) == 1  # niente avvisi ripetuti
    r.devices = "- Pixel 8: 1a2b3c4d (paired)\n"
    svc.tick()
    assert notes[-1] == "📱 Pixel 8 si è allontanato" and not srv.running
    srv.pairing.start()  # abbinamento in corso: la pagina resta accesa anche senza telefoni vicini
    svc.tick()
    assert srv.running

    r.calls_json = RINGING
    svc.tick()
    deadline = time.time() + 2
    while not any(c[-1] == "Answer" for c in r.ran) and time.time() < deadline:
        time.sleep(0.02)
    assert asked == ["📞 Chiamata da +39 333 1234567"] and any(c[-1] == "Answer" for c in r.ran)
    svc.tick()
    assert len(asked) == 1  # la stessa chiamata non si notifica due volte


def test_control_socket(tmp_path):
    svc = MeshService(KdeConnect(FakeRunner()), Ofono(FakeRunner()), FakeServer())
    sock_path = Path(tempfile.mkdtemp()) / "c.sock"
    sock = svc.serve_control(sock_path)
    reply = send_command({"azione": "abbina"}, sock_path)
    assert "#abbina=" in reply["url"] and svc.server.running
    assert send_command({"azione": "stato"}, sock_path)["pagina"] is True
    assert oct(os.stat(sock_path).st_mode)[-3:] == "600"
    sock.close()
    assert send_command({"azione": "stato"}, tmp_path / "nessuno.sock") is None


# --- copilota ---------------------------------------------------------------------------------------------


def test_copilot_phone_requests(tmp_path):
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM per il telefono")

    r = FakeRunner(DEVICES, RINGING)
    tools = phone_tools.make_tools(r, command=lambda c: {"url": "https://192.168.1.5:8743/#abbina=abc"})
    agent = Agent(NoModel(), tools, confirm=lambda *a, **k: True, routers=[phone_tools.PhoneRouter()])
    out = agent.ask("collega il telefono")
    assert "iPhone di Anna" in out and "https://192.168.1.5:8743/#abbina=abc" in out
    assert ["kdeconnect-cli", "-d", "7e7e", "--pair"] in r.ran
    assert agent.ask("fai squillare il telefono") == "Faccio squillare Pixel 8."
    assert agent.ask("rispondi").startswith("Risposto a +39 333 1234567")
    assert agent.ask("riaggancia") == "Chiamata rifiutata."
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home/nota.txt").write_text("ciao")
    assert agent.ask("manda ~/nota.txt al telefono") == "Mandato nota.txt a Pixel 8."
    assert r.ran[-1] == ["kdeconnect-cli", "-d", "1a2b3c4d", "--share", str(tmp_path / "home/nota.txt")]
    assert "Vicini e collegati: Pixel 8" in agent.ask("i miei dispositivi")
    assert mail_tools.MailRouter().match("rispondi") is None  # «rispondi» da solo è per il telefono
