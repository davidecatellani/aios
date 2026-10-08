import json
import os

import pytest

from aios_copilot import ed25519, identity as ident, relay
from aios_copilot.agent import Agent
from aios_copilot.mesh.delegate import sync_peers
from aios_copilot.mesh.files import certificate
from aios_copilot.sync import SyncEngine
from aios_copilot.tools import identity as identity_tools


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("AIOS_NO_KEYRING", "1")


class Box:
    prefix = "note"

    def __init__(self):
        self.data = {}

    def records(self):
        return dict(self.data)

    def apply(self, key, value):
        if value is None:
            self.data.pop(key, None)
        else:
            self.data[key] = value


class User:
    """Un utente con i suoi dispositivi, tutto in memoria (niente cassaforte condivisa nel test)."""

    def __init__(self, name):
        self.secret = os.urandom(16)
        self.master = ident.master_seed(self.secret)
        self.sync_key = ident.sync_key_from(self.secret)
        self.public = ed25519.public_key(self.master)
        self.revocations = ident.revocation_list(self.master, [], 0)
        self.name = name

    def device(self, name, kind="pc", holds_master=False):
        seed = os.urandom(32)
        cert = ident.issue(self.master, ed25519.public_key(seed), name, kind)
        me = ident.Identity({"utente": ident.b64(self.public), "nome": self.name, "certificato": cert.to_dict(),
                             "revoche": self.revocations}, device_seed=seed, sync_key=self.sync_key)
        me.save = lambda: None
        if holds_master:
            me.master = lambda: self.master
        else:
            me.master = lambda: None
        return me


@pytest.fixture
def relay_server(tmp_path):
    cert, key, fingerprint = certificate(tmp_path)
    store = relay.RelayStore(tmp_path / "relay.db")
    httpd = relay.serve(store, "127.0.0.1", 0, cert, key)
    cfg = {"url": f"https://127.0.0.1:{httpd.server_address[1]}", "fingerprint": fingerprint}
    yield store, cfg
    httpd.shutdown()


def engine(me, tmp_path, name):
    box = Box()
    return SyncEngine(me.sync_key(), me.certificate.id, [box], tmp_path / f"{name}.db"), box


def test_devices_far_apart_sync_through_the_relay(relay_server, tmp_path):
    store, cfg = relay_server
    davide = User("Davide")
    laptop, phone = davide.device("Portatile", holds_master=True), davide.device("Pixel 8", "telefono")
    a, a_box = engine(laptop, tmp_path, "a")
    b, b_box = engine(phone, tmp_path, "b")

    a_box.data["spesa"] = "latte"
    a_box.data["allarme"] = "il codice dell'allarme è 4242"
    assert a.sync_with("relay", relay.relay_requester(laptop, cfg)) == (0, 2)
    assert b.sync_with("relay", relay.relay_requester(phone, cfg))[0] == 2
    assert b_box.data == a_box.data
    b_box.data["spesa"] = "latte e pane"
    b.sync_with("relay", relay.relay_requester(phone, cfg))
    a.sync_with("relay", relay.relay_requester(laptop, cfg))
    assert a_box.data["spesa"] == "latte e pane"

    # il relay non sa leggere: niente testo, niente chiavi delle voci, niente nome dell'utente
    dump = json.dumps(store.db.execute("SELECT * FROM ops").fetchall() + store.db.execute("SELECT * FROM mailboxes").fetchall())
    for secret in ("allarme", "4242", "spesa", "latte", "Davide", "Pixel"):
        assert secret not in dump
    assert store.db.execute("SELECT id FROM mailboxes").fetchone()[0] == laptop.mailbox


def test_strangers_cannot_use_someone_elses_mailbox(relay_server, tmp_path):
    store, cfg = relay_server
    davide = User("Davide")
    laptop = davide.device("Portatile", holds_master=True)
    a, a_box = engine(laptop, tmp_path, "a")
    a_box.data["x"] = "1"
    a.sync_with("relay", relay.relay_requester(laptop, cfg))

    mallory_user = User("Mallory")
    mallory = mallory_user.device("PC di Mallory", holds_master=True)

    class Pretending(ident.Identity):
        mailbox = laptop.mailbox  # punta alla cassetta di Davide

    intruder = Pretending(mallory.data, device_seed=mallory.device_seed(), sync_key=mallory.sync_key())
    intruder.save = lambda: None
    with pytest.raises(ConnectionError, match="riconosciuto"):
        relay.relay_requester(intruder, cfg)("GET", "/api/sync?dopo=0", None)
    assert not store.register(laptop.mailbox, mallory_user.public)  # la cassetta è legata alla chiave di Davide
    assert store.since(laptop.mailbox, 0)[0]  # i dati di Davide sono ancora lì, intatti


def test_revoked_device_is_locked_out_of_the_relay(relay_server, tmp_path):
    store, cfg = relay_server
    davide = User("Davide")
    laptop, phone = davide.device("Portatile", holds_master=True), davide.device("Pixel 8", "telefono")
    a, a_box = engine(laptop, tmp_path, "a")
    b, b_box = engine(phone, tmp_path, "b")
    b.sync_with("relay", relay.relay_requester(phone, cfg))

    laptop.data["dispositivi"] = [phone.certificate.to_dict()]
    assert [c.name for c in laptop.revoke("pixel")] == ["Pixel 8"]
    a_box.data["dopo"] = "il telefono è stato rubato"
    a.sync_with("relay", relay.relay_requester(laptop, cfg))  # il relay impara la revoca
    with pytest.raises(ConnectionError, match="riconosciuto"):
        b.sync_with("relay", relay.relay_requester(phone, cfg))
    assert "dopo" not in b_box.data

    # nessuno può annullare la revoca con una lista vecchia o falsa
    old = ident.revocation_list(davide.master, [], 0)
    assert not store.set_revocations(laptop.mailbox, old)
    forged = {**laptop.data["revoche"], "seq": 99, "revoked": []}
    assert not store.set_revocations(laptop.mailbox, forged)


def test_mailbox_space_is_limited(relay_server, tmp_path, monkeypatch):
    store, cfg = relay_server
    monkeypatch.setattr(relay, "MAX_MAILBOX_BYTES", 400)
    laptop = User("Davide").device("Portatile", holds_master=True)
    a, a_box = engine(laptop, tmp_path, "a")
    for i in range(20):
        a_box.data[f"voce{i}"] = "x" * 50
    with pytest.raises(ConnectionError, match="spazio esaurito"):
        a.sync_with("relay", relay.relay_requester(laptop, cfg))


def test_sync_peers_uses_the_relay(relay_server, tmp_path):
    _, cfg = relay_server
    laptop = User("Davide").device("Portatile", holds_master=True)
    laptop.data["relay"] = cfg
    a, a_box = engine(laptop, tmp_path, "a")
    a_box.data["film"] = "Dune"
    assert sync_peers(laptop, a) == ["relay: ricevute 0, inviate 1"]
    laptop.data["relay"] = {"url": "https://127.0.0.1:9", "fingerprint": cfg["fingerprint"]}
    assert sync_peers(laptop, a)[0].startswith("relay: non")


def test_copilot_turns_the_relay_on_and_off():
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    ident.create("Davide", "Portatile")
    asked = []
    agent = Agent(NoModel(), identity_tools.make_tools(), confirm=lambda tool, args, **k: asked.append(tool.name) or True,
                  routers=[identity_tools.IdentityRouter()])
    assert agent.ask("usa il relay https://Relay.Example.org").startswith("Relay attivo: https://Relay.Example.org")
    assert asked == ["set_relay"] and ident.Identity.load().data["relay"]["url"] == "https://Relay.Example.org"
    assert "relay https://Relay.Example.org" in agent.ask("la mia identità")
    assert agent.ask("disattiva il relay").startswith("Relay disattivato")
    assert "relay" not in ident.Identity.load().data


def test_key_rotation_after_revocation(relay_server, tmp_path):
    from aios_copilot.mesh.delegate import _sync_rotating

    store, cfg = relay_server
    davide = User("Davide")
    laptop = davide.device("Portatile", holds_master=True)
    phone, tablet = davide.device("Pixel 8", "telefono"), davide.device("Tablet", "tablet")
    laptop.data["dispositivi"] = [d.certificate.to_dict() for d in (laptop, phone, tablet)]
    engines = {name: engine(dev, tmp_path, name) for name, dev in (("a", laptop), ("p", phone), ("t", tablet))}
    sync = {name: (lambda dev, eng: lambda: _sync_rotating(dev, eng, "relay", relay.relay_requester(dev, cfg)))(dev, engines[name][0])
            for name, dev in (("a", laptop), ("p", phone), ("t", tablet))}
    engines["a"][1].data["spesa"] = "latte"
    for name in "apt":
        sync[name]()
    assert engines["t"][1].data == engines["p"][1].data == {"spesa": "latte"}
    old_key = phone.sync_key()

    laptop.revoke("pixel")  # revoca → nuova chiave cifrata solo per portatile e tablet
    assert laptop.epoch == 1 and laptop.sync_key() != old_key
    assert set(laptop.data["chiavi"]["wrapped"]) == {laptop.certificate.id, tablet.certificate.id}
    engines["a"][1].data["dopo"] = "nuovo indirizzo di casa"
    sync["a"]()
    sync["t"]()  # il tablet riceve la nuova chiave, ricifra i suoi dati e continua
    assert tablet.epoch == 1 and tablet.sync_key() == laptop.sync_key()
    assert engines["t"][1].data == {"spesa": "latte", "dopo": "nuovo indirizzo di casa"}
    engines["t"][1].data["film"] = "Dune"
    sync["t"]()
    sync["a"]()
    assert engines["a"][1].data["film"] == "Dune"

    # il telefono revocato: respinto dal relay, e anche con i dati in mano non saprebbe leggerli
    with pytest.raises(ConnectionError, match="riconosciuto"):
        sync["p"]()
    assert not phone.accept_keys(laptop.data["chiavi"])  # la chiave nuova non è cifrata per lui
    stolen, _ = store.since(laptop.mailbox, 0)
    assert stolen and engines["p"][0].merge(stolen) == 0 and "dopo" not in engines["p"][1].data
    forged = {**laptop.data["chiavi"], "epoch": 5}
    assert not tablet.accept_keys(forged)  # epoca alterata: firma non valida
