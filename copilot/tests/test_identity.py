import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aios_copilot import crypto, ed25519, identity as ident
from aios_copilot.agenda import Agenda
from aios_copilot.agent import Agent
from aios_copilot.sync import AgendaAdapter, SyncEngine, ThemesAdapter
from aios_copilot.tools import identity as identity_tools

COPILOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("AIOS_NO_KEYRING", "1")


# --- crittografia ---------------------------------------------------------------------------------


def test_chacha20_poly1305_rfc8439_and_tampering():
    key, nonce = bytes(range(0x80, 0xA0)), bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    text = b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it."
    for pure in (True, False):
        out = crypto.encrypt(key, nonce, text, aad, pure=pure)
        assert out[-16:].hex() == "1ae10b594f09e26a7e902ecbd0600691"
        assert crypto.decrypt(key, nonce, out, aad, pure=pure) == text
        bad = bytes([out[0] ^ 1]) + out[1:]
        with pytest.raises(crypto.DecryptError):
            crypto.decrypt(key, nonce, bad, aad, pure=pure)
        with pytest.raises(crypto.DecryptError):
            crypto.decrypt(key, nonce, out, b"altro", pure=pure)


def test_ed25519_signing_rfc8032():
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    assert ed25519.public_key(seed).hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = ed25519.sign(seed, b"")
    assert sig.hex().startswith("e5564300c360ac729086e2cc806e828a")
    assert ed25519.verify(ed25519.public_key(seed), b"", sig) and not ed25519.verify(ed25519.public_key(seed), b"x", sig)


# --- frase di recupero e certificati ------------------------------------------------------------------


def test_recovery_phrase():
    secret = bytes(range(16))
    words = ident.phrase_from_secret(secret)
    assert len(words) == 17
    assert ident.secret_from_phrase(" ".join(words)) == secret
    assert ident.secret_from_phrase(" ".join(w[:4].upper() for w in words)) == secret  # bastano le prime 4 lettere
    swapped = words[:]
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(ident.IdentityError, match="non torna"):
        ident.secret_from_phrase(" ".join(swapped))
    with pytest.raises(ident.IdentityError, match="17 parole"):
        ident.secret_from_phrase(" ".join(words[:12]))
    with pytest.raises(ident.IdentityError, match="non è una parola"):
        ident.secret_from_phrase(" ".join(["computer"] + words[1:]))


def test_identity_devices_and_revocation():
    me, words = ident.create("Davide", "Portatile")
    assert me.trusts(me.certificate) and me.master() is not None
    phone_seed = os.urandom(32)
    cert = me.add_device(ed25519.public_key(phone_seed), "Pixel 8", "telefono")
    assert me.trusts(cert)
    forged = ident.Certificate.from_dict({**cert.to_dict(), "name": "Pixel di qualcun altro"})
    assert not me.trusts(forged)
    other_master = ident.master_seed(os.urandom(16))  # un'altra persona
    assert not me.trusts(ident.issue(other_master, ed25519.public_key(os.urandom(32)), "PC estraneo", "pc"))

    me2 = ident.restore(" ".join(words), "PC nuovo")
    assert me2.user_public == me.user_public  # stessa identità dalla frase
    cert2 = me2.add_device(ed25519.public_key(phone_seed), "Pixel 8", "telefono")
    assert me2.trusts(cert2)
    assert [c.name for c in me2.revoke("pixel")] == ["Pixel 8"] and not me2.trusts(cert2)

    old = dict(me2.data["revoche"])
    assert not me2.accept_revocations(old)  # non più recente
    tampered = {**old, "seq": old["seq"] + 1, "revoked": []}
    assert not me2.accept_revocations(tampered)  # firma non valida


def test_signed_requests():
    me, _ = ident.create("Davide", "Portatile")
    body = b'{"ops": []}'
    header = me.sign_request("POST", "/api/sync", body, now=1000)
    assert me.check_request(header, "POST", "/api/sync", body, now=1010).id == me.certificate.id
    assert me.check_request(header, "POST", "/api/altro", body, now=1010) is None
    assert me.check_request(header, "POST", "/api/sync", b"{}", now=1010) is None
    assert me.check_request(header, "POST", "/api/sync", body, now=1000 + 600) is None  # troppo vecchia: ripetizione
    assert me.check_request("Bearer x", "POST", "/api/sync", body) is None


# --- sincronizzazione --------------------------------------------------------------------------------


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


def test_sync_preserves_phone_data_without_a_local_adapter(tmp_path):
    phone_box = Box()
    phone_box.data["mobile"] = {"text": "nota sul telefono"}
    phone = SyncEngine(b"k" * 32, "phone", [phone_box], tmp_path / "phone.db")
    pc = SyncEngine(b"k" * 32, "pc", [], tmp_path / "pc.db")
    phone.scan()
    assert pc.merge(phone.ops_since(0)[0]) == 1
    assert pc.scan() == 0
    assert pc.scan() == 0
    assert json.loads(pc.db.execute("SELECT value FROM snapshot WHERE key='note/mobile'").fetchone()[0]) == phone_box.data["mobile"]
    assert phone.merge(pc.ops_since(0)[0]) == 0
    assert phone_box.data["mobile"]["text"] == "nota sul telefono"


def test_sync_preserves_profile_fields_unknown_to_the_pc(tmp_path):
    from aios_copilot.sync import ProfileAdapter

    class LocalProfile(ProfileAdapter):
        def records(self):
            return {}

    phone_box = Box()
    phone_box.prefix = "profilo"
    phone_box.data["lingua"] = "italiano"
    phone = SyncEngine(b"k" * 32, "phone", [phone_box], tmp_path / "phone.db")
    pc = SyncEngine(b"k" * 32, "pc", [LocalProfile()], tmp_path / "pc.db")
    phone.scan()
    assert pc.merge(phone.ops_since(0)[0]) == 1
    assert pc.scan() == 0
    assert phone.merge(pc.ops_since(0)[0]) == 0
    assert phone_box.data["lingua"] == "italiano"


def pair(tmp_path, key=b"k" * 32):
    t = [1000.0]
    a_box, b_box = Box(), Box()
    a = SyncEngine(key, "A", [a_box], tmp_path / "a.db", wall=lambda: t[0])
    b = SyncEngine(key, "B", [b_box], tmp_path / "b.db", wall=lambda: t[0])

    def link(src, dst, peer):
        def request(method, path, payload):
            if method == "GET":
                ops, seq = dst.ops_since(int(path.split("=")[1]))
                return {"ops": ops, "seq": seq}
            return {"applicate": dst.merge(payload["ops"])}
        return lambda: src.sync_with(peer, request)

    return t, a, b, a_box, b_box, link(a, b, "B"), link(b, a, "A")


def test_lww_merge_offline_edits_and_deletes(tmp_path):
    t, a, b, a_box, b_box, a_sync, b_sync = pair(tmp_path)
    a_box.data["spesa"] = "latte"
    a_sync()
    assert b_box.data == {"spesa": "latte"}
    # tutti e due offline: modificano la stessa voce, vince la più recente; altre voci si uniscono
    a_box.data["spesa"] = "latte e pane"
    t[0] += 5
    b_box.data["spesa"] = "latte, pane e uova"
    b_box.data["film"] = "Dune"
    a_sync()
    b_sync()
    assert a_box.data == b_box.data == {"spesa": "latte, pane e uova", "film": "Dune"}
    del a_box.data["film"]  # cancellazione: non deve «risorgere»
    t[0] += 5
    a_sync()
    b_sync()
    assert a_box.data == b_box.data == {"spesa": "latte, pane e uova"}
    assert a.scan() == 0 and b.scan() == 0  # nessun rimbalzo
    # orologio del telefono indietro di un'ora: la sua modifica successiva vince comunque (orologio ibrido)
    t[0] -= 3600
    b_box.data["spesa"] = "solo uova"
    b_sync()
    assert a_box.data["spesa"] == "solo uova"


def test_other_identities_cannot_read_or_inject(tmp_path):
    t, a, b, a_box, b_box, a_sync, _ = pair(tmp_path)
    a_box.data["segreto"] = "il codice dell'allarme è 4242"
    a.scan()
    ops, _ = a.ops_since(0)
    raw = json.dumps(ops)
    assert "allarme" not in raw and "segreto" not in raw  # cifrato e chiave opaca
    intruder_box = Box()
    intruder = SyncEngine(b"x" * 32, "X", [intruder_box], tmp_path / "x.db")
    assert intruder.merge(ops) == 0 and intruder_box.data == {}
    intruder_box.data["segreto"] = "manomesso"
    intruder.scan()
    assert a.merge(intruder.ops_since(0)[0]) == 0 and a_box.data["segreto"].startswith("il codice")
    tampered = [{**ops[0], "ms": ops[0]["ms"] + 99999}]  # orologio alterato: l'etichetta non torna
    assert b.merge(tampered) == 0


def test_agenda_syncs_between_devices(tmp_path):
    now = datetime(2026, 10, 3, 9, 0)
    a_ag, b_ag = Agenda(tmp_path / "a-ag.db", clock=lambda: now), Agenda(tmp_path / "b-ag.db", clock=lambda: now)
    a = SyncEngine(b"k" * 32, "A", [AgendaAdapter(a_ag)], tmp_path / "a.db")
    b = SyncEngine(b"k" * 32, "B", [AgendaAdapter(b_ag)], tmp_path / "b.db")
    rid = a_ag.add_reminder("Pagare la bolletta", now + timedelta(days=2))
    a_ag.add_event("Dentista", now + timedelta(days=1, hours=6))
    a.scan()
    b.merge(a.ops_since(0)[0])
    assert sorted(i.title for i in b_ag.between(now, now + timedelta(days=3))) == ["Dentista", "Pagare la bolletta"]
    a_ag.complete(rid)
    a.scan()
    b.merge(a.ops_since(0)[0])
    assert [i.title for i in b_ag.todos()] == []
    assert b.scan() == 0


def test_themes_follow_the_user(tmp_path, monkeypatch):
    from aios_copilot import themes

    applied = []
    adapter = ThemesAdapter(apply_theme=applied.append)
    sea = themes.from_description("stile marino")
    rec = json.loads(sea.to_json())
    rec.pop("origin")
    adapter.apply(sea.id, rec)
    adapter.apply("_attuale", sea.id)
    assert themes.load(sea.id).name == "Marino" and applied[0].id == sea.id
    with pytest.raises(Exception):
        adapter.apply("cattivo", {**rec, "id": "cattivo", "light": {**rec["light"], "accent": "red;}"}})


# --- copilota -----------------------------------------------------------------------------------------


def test_copilot_identity_flow():
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    asked = []
    tools = identity_tools.make_tools(command=lambda c: {"righe": ["Portatile: ricevute 2, inviate 1"]},
                                      user_name=lambda: "Davide")
    agent = Agent(NoModel(), tools, confirm=lambda tool, args, **k: asked.append(tool.name) or True,
                  routers=[identity_tools.IdentityRouter()])
    assert "Non hai ancora" in agent.ask("la mia identità")
    out = agent.ask("crea la mia identità")
    assert "frase di recupero" in out and " 1. " in out and "17. " in out and asked == ["create_identity"]
    words = ident.recovery_phrase()
    assert all(w in out for w in words)
    assert "Identità di Davide" in agent.ask("la mia identità") and "← questo" in agent.ask("la mia identità")
    assert all(w in agent.ask("mostra la frase di recupero") for w in words) and asked[-1] == "show_recovery_phrase"
    assert "ricevute 2" in agent.ask("sincronizza")
    assert "Non trovo" in agent.ask("revoca il tablet della cucina")


# --- da capo a fondo: un PC e un telefono in due processi diversi ----------------------------------------

PC_SCRIPT = textwrap.dedent("""
    import json, sys
    from datetime import datetime, timedelta
    sys.path.insert(0, %r)
    from aios_copilot import identity
    from aios_copilot.agenda import Agenda
    from aios_copilot.mesh.files import FileShare, PhoneServer
    from aios_copilot.sync import AgendaAdapter, ProfileAdapter, engine_for
    from aios_copilot.welcome import save_profile
    me, words = identity.create("Davide", "Portatile")
    save_profile(name="Davide")
    agenda = Agenda()
    agenda.add_event("Dentista", datetime.now() + timedelta(days=1))
    engine = engine_for(me, [AgendaAdapter(agenda), ProfileAdapter()])
    server = PhoneServer(FileShare(), sync=lambda: engine)
    port = server.start("127.0.0.1", 0)
    server.pairing.start()
    print(json.dumps({"url": f"https://127.0.0.1:{port}/#abbina={server.pairing.code}&fp={server.fingerprint}"}), flush=True)
    for line in sys.stdin:
        cmd = line.split()
        if cmd[0] == "agenda":
            out = sorted(r["title"] for r in agenda.sync_records().values())
        elif cmd[0] == "revoca":
            out = [c.name for c in identity.Identity.load().revoke(cmd[1])]
        print(json.dumps(out), flush=True)
""")


def test_pc_and_phone_end_to_end(tmp_path):
    from aios_copilot.mesh import delegate
    from aios_copilot.sync import engine_for
    from aios_copilot.welcome import load_profile

    pc_env = {**os.environ, "HOME": str(tmp_path / "pc"), "XDG_CONFIG_HOME": str(tmp_path / "pc/config"),
              "XDG_DATA_HOME": str(tmp_path / "pc/data"), "AIOS_NO_KEYRING": "1"}
    (tmp_path / "pc").mkdir()
    pc = subprocess.Popen([sys.executable, "-c", PC_SCRIPT % str(COPILOT)], env=pc_env, text=True,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        url = json.loads(pc.stdout.readline())["url"]

        def ask_pc(command):
            pc.stdin.write(command + "\n")
            pc.stdin.flush()
            return json.loads(pc.stdout.readline())

        # il telefono (questo processo) inquadra il QR: entra nell'identità di Davide
        config = delegate.pair_with_pc(url, "Pixel 8")
        assert config["identita"] == "Davide"
        phone = ident.Identity.load()
        assert phone.certificate.name == "Pixel 8" and phone.trusts(phone.certificate) and phone.master() is None

        agenda = Agenda()
        agenda.add_reminder("Comprare il pane", None)
        from aios_copilot.sync import AgendaAdapter, ProfileAdapter

        engine = engine_for(phone, [AgendaAdapter(agenda), ProfileAdapter()])
        report = delegate.sync_peers(phone, engine)
        assert "ricevute 2" in report[0]  # Dentista e il nome
        assert sorted(r["title"] for r in agenda.sync_records().values()) == ["Comprare il pane", "Dentista"]
        assert load_profile()["name"] == "Davide"
        assert ask_pc("agenda") == ["Comprare il pane", "Dentista"]

        # telefono perso: lo si revoca dal PC, e non può più sincronizzarsi
        assert ask_pc("revoca pixel") == ["Pixel 8"]
        agenda.add_reminder("Dopo il furto", None)
        assert "non riconosce più questo dispositivo" in delegate.sync_peers(phone, engine)[0]
        assert ask_pc("agenda") == ["Comprare il pane", "Dentista"]
    finally:
        pc.kill()
