import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from aios_copilot import learning, moe, trial
from aios_copilot.agent import Agent
from aios_copilot.hardware import GPU, Device, detect
from aios_copilot.llm import LlamaServerClient, OllamaClient, make_client
from aios_copilot.models import candidates_for, find_model, load_config, save_config
from aios_copilot.tools.base import Tool, params


@pytest.fixture(autouse=True)
def catalogo_qwen25(monkeypatch):
    """Queste prove controllano la logica di scelta (compressione, esperti, licenze, prove) sul catalogo
    della generazione Qwen 2.5; la scelta con Qwen 3.5 è in test_models.py::test_qwen35_is_the_default_choice."""
    from aios_copilot import models as _models

    full = _models.catalog
    monkeypatch.setattr(_models, "catalog", lambda: tuple(m for m in full() if not m.name.startswith("qwen3.5")))



@pytest.fixture(autouse=True)
def catalogo_qwen25(monkeypatch):
    """Queste prove controllano la logica di scelta (compressione, esperti, licenze, prove) sul catalogo
    della generazione Qwen 2.5; la scelta con Qwen 3.5 è in test_models.py::test_qwen35_is_the_default_choice."""
    from aios_copilot import models as _models

    full = _models.catalog
    monkeypatch.setattr(_models, "catalog", lambda: tuple(m for m in full() if not m.name.startswith("qwen3.5")))



@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("AIOS_MODEL", raising=False)


QWEN3 = find_model("qwen3:30b-a3b-instruct-2507-q4_K_M")


def device(ram, gpu=0, disk="nvme", cores=8):
    return Device(ram, ram - 2, "CPU", cores, "x86_64", True, [GPU("GPU", gpu, "nvidia")] if gpu else [],
                  disk_free_gb=500, disk_kind=disk)


def test_disk_kind(tmp_path):
    root = tmp_path / "root"
    for name, rot in (("sda", "1"), ("nvme0n1", None), ("loop0", "0")):
        (root / "sys/block" / name / "queue").mkdir(parents=True)
        if rot:
            (root / "sys/block" / name / "queue/rotational").write_text(rot)
    (root / "proc").mkdir()
    (root / "proc/meminfo").write_text("MemTotal: 16000000 kB\n")
    (root / "proc/cpuinfo").write_text("processor : 0\n")
    d = detect(root, run=lambda c: "", home=tmp_path)
    assert d.disk_kind == "nvme" and "(NVMe)" in d.summary()


def test_placement_depends_on_the_device():
    # 32 GB senza GPU: entra tutto in RAM, e per ogni parola si leggono solo ~2 GB → veloce.
    p = moe.best_placement(device(32), QWEN3, server=False)
    assert p.mode == "ram" and p.tokens_per_second == 20 and not p.needs_server
    # 16 GB con NVMe: gli esperti meno usati restano sul disco (serve llama.cpp).
    p = moe.best_placement(device(16), QWEN3, server=True)
    assert p.mode == "disco" and 4 <= p.tokens_per_second < 10 and p.needs_server
    assert moe.best_placement(device(16), QWEN3, server=False) is None  # Ollama non lo caricherebbe
    assert moe.best_placement(device(16, disk="hdd"), QWEN3, server=True) is None  # disco troppo lento
    assert moe.best_placement(device(8), QWEN3, server=True) is None  # troppo poca RAM per la cache
    # GPU da 8 GB e 32 GB di RAM: attenzione sulla GPU, esperti in RAM, la più veloce.
    p = moe.best_placement(device(32, gpu=8), QWEN3, server=True)
    assert p.mode == "gpu+ram" and p.tokens_per_second > 20
    assert moe.best_placement(device(64, gpu=24), QWEN3, server=False).mode == "gpu"


def test_moe_beats_dense_models_when_it_fits(monkeypatch):
    monkeypatch.setattr(moe, "has_server", lambda: True)
    assert candidates_for(device(16), "testo")[0].name == QWEN3.name  # 30B a esperti dal disco NVMe
    monkeypatch.setattr(moe, "has_server", lambda: False)
    assert candidates_for(device(16), "testo")[0].name == "qwen2.5:7b-instruct"  # senza llama.cpp: il miglior denso


def test_server_uses_the_file_downloaded_by_ollama(tmp_path):
    base = tmp_path / "ollama"
    manifest = base / "manifests/registry.ollama.ai/library/qwen3/30b-a3b-instruct-2507-q4_K_M"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:aa"},
                                               {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:bb"}]}))
    (base / "blobs").mkdir()
    (base / "blobs/sha256-bb").write_bytes(b"GGUF")
    assert moe.ollama_blob(QWEN3.name, [base]) == base / "blobs/sha256-bb"
    assert moe.ollama_blob("altro:1b", [base]) is None

    ran = []
    url = moe.start_server(QWEN3.name, moe.Placement("gpu+ram", 30, True), run=lambda c: ran.append(c) or (0, ""), dirs=[base])
    assert url == "http://127.0.0.1:11435" and ran[-1] == ["systemctl", "--user", "restart", "aios-esperti.service"]
    unit = moe.unit_path().read_text()
    assert f"-m {base}/blobs/sha256-bb" in unit and "--n-cpu-moe 999" in unit and "--host 127.0.0.1" in unit
    disk = moe.server_command(base / "m.gguf", moe.Placement("disco", 5, True))
    assert "-ngl" in disk and disk[disk.index("-ngl") + 1] == "0" and "--no-mmap" not in disk


def test_message_translation_round_trip():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "che ore sono?"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "ora", "arguments": {"zona": "Roma"}}}]},
            {"role": "tool", "tool_name": "ora", "content": "10:00"}]
    out = moe.to_openai(msgs)
    assert out[2]["tool_calls"][0]["function"]["arguments"] == '{"zona": "Roma"}'
    assert out[3] == {"role": "tool", "tool_call_id": out[2]["tool_calls"][0]["id"], "content": "10:00"}
    reply = moe.from_openai({"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "ora", "arguments": '{"zona": "Roma"}'}}]}}]})
    assert reply["tool_calls"] == [{"function": {"name": "ora", "arguments": {"zona": "Roma"}}}]


def test_trial_runs_on_llama_server():
    def post(path, payload):
        if path == "/completion":
            return {"timings": {"predicted_n": 96, "predicted_ms": 12000}}
        return {"choices": [{"message": {"content": "ciao"}}]}

    call = moe.server_call("http://x", post)
    assert trial.measure_speed("m", call) == 8.0


class FakeLlamaServer(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLlamaServer.seen.append(body)
        if body["messages"][-1]["role"] == "tool":
            msg = {"content": f"Sono le {body['messages'][-1]['content']}."}
        else:
            msg = {"content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                     "function": {"name": "ora", "arguments": '{"zona": "Roma"}'}}]}
        data = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_copilot_talks_to_the_experts_model(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), FakeLlamaServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    save_config({"testo": QWEN3.name, "_esperti": json.dumps({QWEN3.name: {"modo": "disco", "url": url}})})
    client = make_client()
    assert isinstance(client, LlamaServerClient)
    tools = [Tool("ora", "Che ore sono", params(zona="Fuso"), lambda zona: "10:00")]
    assert Agent(client, tools, confirm=lambda *a, **k: True).ask("che ore sono a Roma?") == "Sono le 10:00."
    assert FakeLlamaServer.seen[-1]["messages"][-1]["tool_call_id"] == "call_1"
    server.shutdown()
    save_config({"testo": "qwen2.5:7b-instruct"})
    assert isinstance(make_client(), OllamaClient)


def test_adopted_experts_model_is_remembered_and_released(monkeypatch):
    placed = {"mode": "disco", "how": "esperti dal disco", "url": "http://127.0.0.1:11435", "call": None}
    monkeypatch.setattr(learning, "_place_experts", lambda name: placed)
    monkeypatch.setattr(trial, "run_trial", lambda name, call=None: trial.Result(name, 0.9, 6.0, []))
    stopped = []
    monkeypatch.setattr(learning, "_stop_experts_server", lambda: stopped.append(1))
    adopt, why = learning._trial_text_model(QWEN3.name)
    assert adopt and "disco" in why
    assert json.loads(load_config()["_esperti"])[QWEN3.name]["url"] == placed["url"]
    learning.activate_model("qwen2.5:7b-instruct", "testo")
    learning.activate_model(QWEN3.name, "testo")
    assert learning.restore_model("testo").endswith("qwen2.5:7b-instruct.") and stopped == [1]  # memoria liberata

    monkeypatch.setattr(trial, "run_trial", lambda name, call=None: trial.Result(name, 0.9, 2.0, []))
    adopt, why = learning._trial_text_model(QWEN3.name)
    assert not adopt and "lento" in why and stopped == [1, 1]
