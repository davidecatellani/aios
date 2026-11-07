import json

import pytest

from aios_copilot import memory
from aios_copilot.agent import Agent
from aios_copilot.hardware import GPU, Device
from aios_copilot.learning import DownloadTask
from aios_copilot.models import Queue, best_for, candidates_for, find_model, load_config, next_candidate, propose
from aios_copilot.tools import ai as ai_tools
from aios_copilot.tools.base import Runner


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


SMALL = Device(4, 2, "Celeron", 2, "x86_64", False, [], disk_free_gb=20)
LAPTOP = Device(16, 12, "Ryzen 7", 8, "x86_64", True, [], disk_free_gb=200)
# 16 GB di RAM: il modello a esperti non ci sta, quindi qui si confrontano i modelli densi compressi.
GAMER = Device(16, 12, "Ryzen", 16, "x86_64", True, [GPU("RTX 4070", 12, "nvidia")], disk_free_gb=300)


# --- RAM compressa e memoria del modello ------------------------------------------------------------


def test_plan_adapts_to_the_device():
    small, laptop, gamer = memory.plan_for(SMALL), memory.plan_for(LAPTOP), memory.plan_for(GAMER)
    assert (small.zram_mb, small.kv_cache, small.context) == (2048, "q4_0", 4096)
    assert (laptop.zram_mb, laptop.kv_cache, laptop.context) == (8192, "q8_0", 8192)
    assert gamer.zram_mb == 8192 and gamer.context == 16384
    conf = small.files[memory.ZRAM_CONF]
    assert "compression-algorithm = zstd" in conf and "zram-size = 2048" in conf
    dropin = small.files[memory.OLLAMA_DROPIN]
    assert "OLLAMA_FLASH_ATTENTION=1" in dropin and "OLLAMA_KV_CACHE_TYPE=q4_0" in dropin  # la KV compressa vuole flash attention


class RecordingRunner(Runner):
    def __init__(self, fail=()):
        super().__init__(which=lambda p: p)
        self.ran, self.fail = [], fail

    def run(self, cmd):
        self.ran.append(cmd)
        return (1, "autenticazione annullata") if any(f in " ".join(cmd) for f in self.fail) else (0, "")


def test_apply_writes_system_files_with_privileges():
    r = RecordingRunner()
    assert memory.apply_plan(memory.plan_for(LAPTOP), r) == []
    assert all(c[0] == "pkexec" for c in r.ran)
    written = [c[3] for c in r.ran if c[1] == "sh"]
    assert len(written) == 3 and "zram-generator.conf" in written[0] and "zram-size = 8192" in written[0]
    assert ["pkexec", "systemctl", "try-restart", "ollama.service"] in r.ran  # Ollama riparte solo se già attivo
    errors = memory.apply_plan(memory.plan_for(LAPTOP), RecordingRunner(fail=("zram-generator",)))
    assert len(errors) == 1 and "annullata" in errors[0]


def test_status_reads_zram_and_ollama_settings(tmp_path):
    block = tmp_path / "sys/block/zram0"
    block.mkdir(parents=True)
    (block / "disksize").write_text(str(8 * 2**30))
    (block / "mm_stat").write_text(f"{3000 * 2**20} {1000 * 2**20} {1100 * 2**20} 0 0 0 0 0 0")
    (block / "comp_algorithm").write_text("lzo lz4 [zstd]")
    z = memory.zram_status(tmp_path)
    assert z.algorithm == "zstd" and z.ratio == 3.0 and z.saved_mb == 2000
    text = memory.describe(LAPTOP, tmp_path, ollama_env={})
    assert "×3.0" in text and "non compressa" in text and "ottimizza la memoria" in text
    dropin = tmp_path / str(memory.OLLAMA_DROPIN).lstrip("/")
    dropin.parent.mkdir(parents=True)
    dropin.write_text(memory.plan_for(LAPTOP).files[memory.OLLAMA_DROPIN])
    text = memory.describe(LAPTOP, tmp_path)
    assert "compressa (q8_0)" in text and "ottimizza" not in text
    assert "non attiva" in memory.describe(LAPTOP, tmp_path / "vuoto", ollama_env={})


def test_copilot_understands_memory_requests():
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    asked = []
    r = RecordingRunner()
    tools = ai_tools.make_management_tools(lambda: LAPTOP, lambda: [], lambda: Queue(), runner=r)
    agent = Agent(NoModel(), tools, confirm=lambda tool, args, **k: asked.append(tool.name) or True,
                  routers=[ai_tools.ModelsRouter(ready=dict)])
    assert agent.ask("ottimizza la memoria").startswith("Fatto: RAM compressa da 8.0 GB")
    assert asked == ["optimize_memory"] and r.ran  # impostazione di sistema: sempre con conferma
    assert "Memoria: 16 GB" in agent.ask("quanta memoria ho")


# --- modelli compressi e catena di prove ------------------------------------------------------------


def test_compressed_variants_fit_bigger_models():
    # Portatile senza GPU ma con CPU veloce: il 7B a 4 bit ci sta; il 14B solo compresso, ma è troppo
    # grande da leggere senza GPU, quindi non viene proposto.
    assert [m.name for m in candidates_for(LAPTOP, "testo")][:2] == ["qwen2.5:7b-instruct", "qwen2.5:7b-instruct-q3_K_M"]
    # GPU da 12 GB: il 14B intero non entra (12 GB), quello a 3 bit sì.
    assert best_for(GAMER, "testo").name == "qwen2.5:14b-instruct-q3_K_M"
    # GPU da 24 GB: il 32B compresso a 3 bit batte il 14B a 4 bit.
    big = Device(64, 60, "TR", 32, "x86_64", True, [GPU("RTX 4090", 24, "nvidia")], disk_free_gb=900)
    assert best_for(big, "testo").name == "qwen2.5:32b-instruct-q3_K_M"
    # Con poca RAM non cambia nulla: niente modelli che non stanno in memoria.
    assert best_for(SMALL, "testo").name == "qwen2.5:0.5b-instruct"


def test_rejected_models_are_not_proposed_again():
    config = {"_scartati": json.dumps(["qwen2.5:14b-instruct-q3_K_M"])}
    assert best_for(GAMER, "testo", config).name == "qwen2.5:14b-instruct-q2_K"
    assert next(p for p in propose(GAMER, [], config) if p.capability == "testo").model.name == "qwen2.5:14b-instruct-q2_K"


def test_trial_chain_tries_the_next_variant(tmp_path):
    q = Queue(tmp_path / "q.json")
    q.add(find_model("qwen2.5:14b-instruct-q3_K_M"))
    verdicts = {"qwen2.5:14b-instruct-q3_K_M": (False, "troppo lento qui (2.1 token/s)"),
                "qwen2.5:14b-instruct-q2_K": (False, "meno preciso del modello attuale (60% contro 80%)"),
                "qwen2.5:7b-instruct": (True, "90% dei compiti, 25.0 token/s")}
    activated, discarded = [], []
    task = DownloadTask(q, pull=lambda name, s: (True, 1, 1), activate=lambda n, c: activated.append(n),
                        trial=lambda name: verdicts[name], discard=discarded.append, device=lambda: GAMER)
    while task.has_work():
        task.step(0.1)
    assert discarded == ["qwen2.5:14b-instruct-q3_K_M", "qwen2.5:14b-instruct-q2_K"]
    assert activated == ["qwen2.5:7b-instruct"]
    assert "provo qwen2.5:14b-instruct-q2_K" in q.items[0].error
    assert json.loads(load_config()["_scartati"]) == discarded


def test_chain_stops_below_the_current_model():
    # Se il modello attivo è già il 7B, dopo lo scarto del 14B compresso non si scende sotto il 7B.
    assert next_candidate(LAPTOP, "testo", "qwen2.5:7b-instruct") is None
    assert next_candidate(GAMER, "testo", "qwen2.5:3b-instruct").name == "qwen2.5:14b-instruct-q3_K_M"
