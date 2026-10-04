import json
import subprocess
import time
from pathlib import Path

import pytest

from aios_copilot import engines
from aios_copilot.hardware import GPU, Device, detect
from aios_copilot.learning import DownloadTask, activate_model
from aios_copilot.models import Queue, find_model, best_for, file_download_step, load_config, propose
from aios_copilot.tools import ai as ai_tools


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
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("aios_copilot.moe.has_server", lambda: False)


def fake_root(tmp_path: Path, ram_kb: int, cores: int, flags: str, gpu: tuple[str, int] | None = None,
              battery: bool = False, npu: bool = False) -> Path:
    root = tmp_path / "root"
    (root / "proc").mkdir(parents=True)
    (root / "proc/meminfo").write_text(f"MemTotal: {ram_kb} kB\nMemAvailable: {ram_kb // 2} kB\n")
    (root / "proc/cpuinfo").write_text("".join(f"processor\t: {i}\nmodel name\t: CPU di prova\nflags\t\t: {flags}\n\n"
                                               for i in range(cores)))
    if gpu:
        card = root / "sys/class/drm/card0/device"
        card.mkdir(parents=True)
        (card / "vendor").write_text(gpu[0])
        (card / "mem_info_vram_total").write_text(str(gpu[1]))
    if battery:
        bat = root / "sys/class/power_supply/BAT0"
        bat.mkdir(parents=True)
        (bat / "type").write_text("Battery")
    if npu:
        (root / "sys/module/intel_vpu").mkdir(parents=True)
        (root / "dev/accel").mkdir(parents=True)
        (root / "dev/accel/accel0").write_text("")
    return root


def test_detect_reads_hardware(tmp_path):
    root = fake_root(tmp_path, 32 * 1024**2, 16, "fpu sse avx avx2", gpu=("0x1002", 16 * 1024**3), battery=True, npu=True)
    d = detect(root, run=lambda c: "", home=tmp_path)
    assert round(d.ram_gb) == 32 and d.cores == 16 and d.avx2
    assert d.gpus == [GPU("AMD Radeon", 16.0, "amd")] and d.npu == "Intel NPU" and d.battery
    nvidia = detect(fake_root(tmp_path / "n", 16 * 1024**2, 8, "avx2"), run=lambda c: "RTX 4060, 8188\n", home=tmp_path)
    assert nvidia.gpus[0].name == "RTX 4060" and round(nvidia.vram_gb) == 8


@pytest.mark.parametrize("device, expected", [
    (Device(4, 2, "Celeron", 2, "x86_64", False, [], disk_free_gb=20), {"testo": "qwen2.5:0.5b-instruct"}),
    (Device(16, 12, "Xeon", 4, "x86_64", True, [], disk_free_gb=100), {"testo": "qwen2.5:3b-instruct", "vista": "qwen2.5vl:3b"}),
    (Device(32, 28, "Ryzen", 16, "x86_64", True, [GPU("RTX", 8, "nvidia")], disk_free_gb=300),
     {"testo": "qwen3:30b-a3b-instruct-2507-q4_K_M", "vista": "qwen2.5vl:7b", "immagini": "sd-turbo"}),  # a esperti, in RAM
    (Device(64, 60, "TR", 32, "x86_64", True, [GPU("RTX 4090", 24, "nvidia")], disk_free_gb=900),
     {"testo": "qwen2.5:32b-instruct-q3_K_M", "immagini": "sdxl-turbo"}),  # il 32B compresso a 3 bit
])
def test_best_models_fit_the_device(device, expected):
    for cap, name in expected.items():
        assert best_for(device, cap).name == name
    assert best_for(device, "video") is None  # mai proposto senza un vero download disponibile


def test_proposals_skip_installed_and_respect_disk():
    d = Device(16, 12, "Xeon", 4, "x86_64", True, [], disk_free_gb=100)
    caps = {p.capability for p in propose(d, ["qwen2.5:3b-instruct"], {})}
    assert "testo" not in caps and {"vista", "dettatura", "voce", "significato"} <= caps
    small_disk = Device(16, 12, "Xeon", 4, "x86_64", True, [], disk_free_gb=12)
    assert sum(p.model.size_gb for p in propose(small_disk, [], {})) <= 2  # mai riempire il disco


def test_install_requires_confirmation_and_queues(tmp_path):
    d = Device(16, 12, "Xeon", 4, "x86_64", True, [], disk_free_gb=100)
    q = Queue(tmp_path / "q.json")
    tools = {t.name: t for t in ai_tools.make_management_tools(lambda: d, lambda: [], lambda: q)}
    assert tools["install_models"].requires_confirmation
    out = tools["install_models"].func("vista")
    assert "In coda: vista (qwen2.5vl:3b)" in out and [i.name for i in q.items] == ["qwen2.5vl:3b"]
    assert "già tutto in coda" in tools["install_models"].func("vista")
    assert "Testo: qwen2.5:3b-instruct" in tools["suggest_models"].func()


def test_download_task_resumes_and_activates(tmp_path):
    q = Queue(tmp_path / "q.json")
    model = find_model("qwen2.5vl:3b")
    q.add(model)
    progress = iter([(False, 100, 1000), (False, 600, 1000), (True, 1000, 1000)])
    activated = []
    task = DownloadTask(q, pull=lambda name, s: next(progress), activate=lambda n, c: activated.append((n, c)))
    task.step(0.1)
    assert q.items[0].status == "in corso" and q.items[0].done_bytes == 100
    q2 = Queue(tmp_path / "q.json")  # riavvio: lo stato è salvato
    assert q2.items[0].done_bytes == 100
    task.step(0.1)
    task.step(0.1)
    assert q.items[0].status == "fatto" and activated == [("qwen2.5vl:3b", "vista")]
    assert not task.has_work()


def test_download_errors_retry_later(tmp_path):
    q = Queue(tmp_path / "q.json")
    q.add(find_model("bge-m3"))

    def boom(name, s):
        raise OSError("rete assente")

    task = DownloadTask(q, pull=boom)
    task.step(0.1)
    assert q.items[0].status == "in corso" and "rete assente" in q.items[0].error and not task.available()


class FakeResponse:
    def __init__(self, data: bytes, status: int):
        self.data, self.status, self.headers = data, status, {"Content-Length": str(len(data))}

    def read(self, n):
        chunk, self.data = self.data[:n], self.data[n:]
        time.sleep(0.01)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_file_download_resumes_with_range(tmp_path):
    payload = bytes(range(256)) * 20000  # ~5 MB
    calls = []

    def opener(req):
        start = int(req.headers["Range"].split("=")[1].rstrip("-"))
        calls.append(start)
        return FakeResponse(payload[start:], 206 if start else 200)

    done, have, total = file_download_step(["https://x/modello.bin"], tmp_path, 0.01, opener)
    assert not done and 0 < have < len(payload)
    while not done:
        done, have, total = file_download_step(["https://x/modello.bin"], tmp_path, 5, opener)
    assert (tmp_path / "modello.bin").read_bytes() == payload and calls[1] > 0  # ripreso, non ricominciato


def test_activation_switches_the_copilot_model():
    activate_model("qwen2.5:7b-instruct", "testo")
    from aios_copilot.llm import OllamaClient

    assert OllamaClient().model == "qwen2.5:7b-instruct"
    calibrated = []
    activate_model("bge-m3", "significato", calibrate=calibrated.append)
    assert calibrated == ["bge-m3"] and load_config()["significato"] == "bge-m3"


def test_capability_tools_only_when_ready(tmp_path, monkeypatch):
    assert ai_tools.make_capability_tools({}) == []
    names = {t.name for t in ai_tools.make_capability_tools({"vista": "qwen2.5vl:3b", "voce": "piper-it-paola"})}
    assert names == {"describe_image", "look_at_screen", "read_aloud"}
    # Un modello a file conta solo se i file ci sono E il programma è installato.
    activate_model("piper-it-paola", "voce")
    assert "voce" not in engines.available(which=lambda p: "/usr/bin/piper")
    for f in engines.model_files("piper-it-paola"):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    assert engines.available(which=lambda p: None) == {}
    assert engines.available(which=lambda p: "/usr/bin/piper") == {"voce": "piper-it-paola"}


def test_vision_sends_image_to_local_model(tmp_path):
    img = tmp_path / "scontrino.png"
    img.write_bytes(b"\x89PNG fake")
    sent = []

    def chat(path, payload):
        sent.append(payload)
        return {"message": {"content": "Uno scontrino da 12,50 €."}}

    assert engines.describe_image(img, "Quanto ho speso?", "qwen2.5vl:3b", chat) == "Uno scontrino da 12,50 €."
    assert sent[0]["messages"][0]["images"] and sent[0]["model"] == "qwen2.5vl:3b"
    assert "non è un'immagine" in engines.describe_image(tmp_path / "x.txt", "", "m", chat)


def test_transcribe_and_speak_call_local_engines(tmp_path):
    activate_model("whisper-small", "dettatura")
    for f in engines.model_files("whisper-small"):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    audio = tmp_path / "nota.ogg"
    audio.write_bytes(b"audio")
    ran = []

    def run(cmd, **kw):
        ran.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, stdout="Ciao, ricordami di chiamare Luca.\n" if "whisper" in cmd[0] else "")

    which = lambda p: f"/usr/bin/{p}" if p in ("whisper-cli", "ffmpeg", "piper", "pw-play") else None
    assert engines.transcribe(audio, "whisper-small", run, which) == "Ciao, ricordami di chiamare Luca."
    assert ran == ["ffmpeg", "whisper-cli"]


def test_router():
    r = ai_tools.ModelsRouter(ready=lambda: {"vista": "m"})
    assert r.match("che modelli posso usare?").tool == "suggest_models"
    assert r.match("aggiorna i modelli").args == {"which": "tutti"}
    assert r.match("installa la dettatura").args == {"which": "dettatura"}
    assert r.match("cosa c'è sullo schermo?").tool == "look_at_screen"
    assert ai_tools.ModelsRouter(ready=lambda: {}).match("cosa c'è sullo schermo?") is None


def test_weekly_hint_in_briefing():
    from aios_copilot.models import weekly_hint

    d = Device(16, 12, "Xeon", 4, "x86_64", True, [], disk_free_gb=100)
    hint = weekly_hint(d, [], now=1_000_000)
    assert hint.startswith("🧠 Il tuo dispositivo può usare modelli AI più completi (testo, vista")
    assert weekly_hint(d, [], now=1_000_000 + 3 * 86400) is None  # non insistere
    assert weekly_hint(d, [], now=1_000_000 + 8 * 86400) is not None


def test_qwen35_is_the_default_choice(monkeypatch):
    from aios_copilot import llm, models as _models

    monkeypatch.setattr(_models, "catalog", lambda: _models.BUILTIN)
    laptop = Device(8, 6, "Core i3", 4, "x86_64", True, [], disk_free_gb=100)  # come l'ASUS X540UA
    assert best_for(laptop, "testo").name == "qwen3.5:2b" and llm.DEFAULT_MODEL == "qwen3.5:2b"
    assert best_for(Device(16, 12, "Core i5", 8, "x86_64", True, [], disk_free_gb=100), "testo").name == "qwen3.5:4b"
    client = llm.OllamaClient(model="qwen3.5:2b")
    assert client._payload([], [])["think"] is False  # risposta pronta, senza ragionamento lungo
    assert "think" not in llm.OllamaClient(model="qwen2.5:7b-instruct")._payload([], [])
