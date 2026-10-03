import json
import time

import pytest

from aios_copilot.agent import Agent
from aios_copilot.llm import OllamaClient, make_client
from aios_copilot.mesh import delegate
from aios_copilot.mesh.delegate import PHONE_ALLOWED, Assistant, Brain, HybridModel, PinError, RemoteBrain
from aios_copilot.mesh.files import Devices, FileShare, PhoneServer
from aios_copilot.tools.base import Tool, params


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("AIOS_OLLAMA_URL", "http://127.0.0.1:9")
    (tmp_path / "home").mkdir()


class BigModel:
    """Il modello «grande» del PC: risponde e, se servono, chiede strumenti."""

    model = "qwen3:30b-a3b"

    def __init__(self):
        self.seen = []

    def chat(self, messages, tools):
        self.seen.append((messages, tools))
        last = messages[-1]
        if last["role"] == "tool":
            return {"role": "assistant", "content": f"Fatto: {last['content']}"}
        if "nota" in last["content"]:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "save_note", "arguments": {"text": "comprare il latte"}}}]}
        return {"role": "assistant", "content": f"Risposta del PC a: {last['content']}"}


@pytest.fixture
def pc(tmp_path):
    big = BigModel()
    saved = []

    def phone_agent(confirm):
        note = Tool("save_note", "Salva una nota", params(text="Testo"), lambda text: saved.append(text) or "nota salvata",
                    requires_confirmation=True)
        return Agent(big, [note], confirm)

    server = PhoneServer(FileShare(tmp_path / "home"), Devices(tmp_path / "devices.json"),
                         brain=Brain(lambda: big), assistant=Assistant(phone_agent))
    port = server.start("127.0.0.1", 0, cert_dir=tmp_path)
    server.pairing.start()
    url = f"https://127.0.0.1:{port}/#abbina={server.pairing.code}&fp={server.fingerprint}"
    yield server, url, big, saved
    server.stop()


def test_phone_pairs_with_pinned_certificate_and_borrows_the_brain(pc):
    server, url, big, _ = pc
    config = delegate.pair_with_pc(url, "Pixel 8")
    assert config["fingerprint"] == server.fingerprint and server.devices.items[0].name == "Pixel 8"
    assert oct(delegate.pc_config_path().stat().st_mode)[-3:] == "600"

    remote = RemoteBrain(delegate.load_pc())
    tools = [{"type": "function", "function": {"name": "leggi_sms", "parameters": {}}}]
    reply = remote.chat([{"role": "user", "content": "riassumi la giornata"}], tools)
    assert reply["content"] == "Risposta del PC a: riassumi la giornata"
    assert big.seen[-1][1] == tools  # gli strumenti restano quelli del telefono: il PC presta solo il ragionamento
    assert remote.reachable()

    fake = RemoteBrain({**config, "fingerprint": "0" * 64})  # qualcuno si finge il PC
    with pytest.raises(PinError):
        fake.chat([{"role": "user", "content": "dati privati"}], [])
    assert len(big.seen) == 1  # nulla è stato inviato


def test_requests_need_a_paired_phone(pc):
    server, url, _, _ = pc
    stranger = {"url": url.split("/#")[0], "key": "chiave-inventata", "fingerprint": server.fingerprint}
    with pytest.raises(ConnectionError, match="non abbinato"):
        RemoteBrain(stranger).chat([{"role": "user", "content": "ciao"}], [])


def test_hybrid_model_falls_back_to_the_phone(pc, tmp_path):
    server, url, big, _ = pc
    config = delegate.pair_with_pc(url, "Pixel 8")

    class Small:
        model = "qwen2.5:0.5b"

        def chat(self, messages, tools):
            return {"role": "assistant", "content": "risposta del telefono"}

    now = [0.0]
    hybrid = HybridModel(RemoteBrain(config), Small(), clock=lambda: now[0])
    assert hybrid.chat([{"role": "user", "content": "ciao"}], [])["content"].startswith("Risposta del PC")
    assert hybrid.model.startswith("PC")
    server.stop()  # il telefono si allontana
    assert hybrid.chat([{"role": "user", "content": "ciao"}], [])["content"] == "risposta del telefono"
    t = time.monotonic()
    hybrid.chat([{"role": "user", "content": "ciao"}], [])
    assert time.monotonic() - t < 0.5  # nel frattempo niente attese: si va diretti al modello locale
    now[0] += 31
    server.start("127.0.0.1", int(config["url"].rsplit(":", 1)[1]), cert_dir=tmp_path)  # stesso certificato del PC
    assert hybrid.chat([{"role": "user", "content": "ciao"}], [])["content"].startswith("Risposta del PC")


def test_ask_the_pc_with_confirmation_on_the_phone(pc):
    server, url, _, saved = pc
    config = delegate.pair_with_pc(url, "Pixel 8")
    req = lambda method, path, data=None: delegate.pinned_request(config["url"], method, path, data, config["key"],
                                                                   config["fingerprint"], 10)
    job = req("POST", "/api/chiedi", {"text": "che tempo fa?"})["job"]
    for _ in range(100):
        state = req("GET", f"/api/job/{job}")
        if state["done"]:
            break
        time.sleep(0.02)
    assert state["answer"].startswith("Risposta del PC a: che tempo fa?") and "dal telefono" in state["answer"]

    job = req("POST", "/api/chiedi", {"text": "salva una nota: comprare il latte"})["job"]
    for _ in range(100):
        state = req("GET", f"/api/job/{job}")
        if state["pending"]:
            break
        time.sleep(0.02)
    assert "save_note" in state["pending"]["label"] or "nota" in state["pending"]["label"].lower()
    assert not saved  # aspetta la conferma sul telefono
    assert req("POST", f"/api/job/{job}/conferma", {"ok": True})["ok"]
    for _ in range(100):
        state = req("GET", f"/api/job/{job}")
        if state["done"]:
            break
        time.sleep(0.02)
    assert saved == ["comprare il latte"] and state["answer"] == "Fatto: nota salvata"


def test_phone_cannot_change_the_pc():
    from aios_copilot.__main__ import make_agent

    agent = make_agent(lambda *a, **k: True, allowed=PHONE_ALLOWED)
    names = set(agent.tools)
    assert {"search_files", "read_mail", "list_agenda", "search_web"} <= names
    for dangerous in ("power", "launch_app", "install_app", "set_volume", "apply_theme", "tidy_apply",
                      "look_at_screen", "optimize_memory", "exclude_folder", "send_sms", "lock_screen"):
        assert dangerous not in names
    assert names <= PHONE_ALLOWED


def test_make_client_on_a_paired_phone(tmp_path):
    assert isinstance(make_client(), OllamaClient)
    path = delegate.pc_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": "https://192.168.1.5:8743", "key": "k", "fingerprint": "ab", "pc": "Studio"}))
    client = make_client()
    assert isinstance(client, HybridModel) and client.remote.model == "PC (Studio)"
