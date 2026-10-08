import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from aios_copilot import ed25519, modelcatalog
from aios_copilot.learning import DownloadTask, restore_model, activate_model
from aios_copilot.models import Queue, best_for, find_model, load_config, save_config
from aios_copilot.hardware import Device
from aios_copilot.trial import Result, decide, run_trial


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


@pytest.fixture(scope="module")
def key(tmp_path_factory):
    d = tmp_path_factory.mktemp("key")
    pem = d / "k.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(pem)], check=True, capture_output=True)
    return pem, base64.b64decode(modelcatalog.public_key(pem))


def signed(tmp_path: Path, pem: Path, doc: dict) -> tuple[bytes, bytes]:
    path = tmp_path / "catalogo.json"
    path.write_bytes(json.dumps(doc).encode())
    sig = modelcatalog.sign_file(path, pem)
    return path.read_bytes(), sig.read_bytes()


NEW_MODEL = {"name": "supermodello:4b", "capability": "testo", "size_gb": 2.5, "ram_gb": 4, "rank": 9, "license": "apache-2.0"}


def test_rfc8032_vector():
    pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.verify(pub, b"", sig)
    assert not ed25519.verify(pub, b"x", sig)


def test_signed_catalog_replaces_builtin(tmp_path, key):
    pem, pub = key
    data, sig = signed(tmp_path, pem, {"version": 5, "models": [NEW_MODEL]})
    served = {"https://cat/c.json": data, "https://cat/c.json.sig": sig}
    assert "versione 5" in modelcatalog.update("https://cat/c.json", served.__getitem__, keys=[pub])
    (tmp_path / "config/aios").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config/aios/catalog-keys").write_text(base64.b64encode(pub).decode() + "\n")
    assert modelcatalog.active_version() == 5
    d = Device(16, 12, "Xeon", 8, "x86_64", True, [], disk_free_gb=100)
    assert best_for(d, "testo").name == "supermodello:4b"  # i modelli nuovi arrivano senza toccare il codice


def test_rejections(tmp_path, key):
    pem, pub = key
    data, sig = signed(tmp_path, pem, {"version": 3, "models": [NEW_MODEL]})
    served = {"https://cat/c.json": data, "https://cat/c.json.sig": sig}
    assert "Nessuna chiave" in modelcatalog.update("https://cat/c.json", served.__getitem__, keys=[])
    other = bytes(32 * [7])
    with pytest.raises(modelcatalog.CatalogError, match="firma"):
        modelcatalog.update("https://cat/c.json", served.__getitem__, keys=[other])
    tampered = {**served, "https://cat/c.json": data.replace(b"supermodello", b"malevolo!!!!")}
    with pytest.raises(modelcatalog.CatalogError, match="firma"):
        modelcatalog.update("https://cat/c.json", tampered.__getitem__, keys=[pub])
    # file senza impronta: rifiutato anche se firmato
    bad, bad_sig = signed(tmp_path, pem, {"version": 4, "models": [
        {"name": "x", "capability": "voce", "size_gb": 1, "ram_gb": 1, "engine": "file", "urls": ["https://h/x.onnx"]}]})
    with pytest.raises(modelcatalog.CatalogError, match="sha256"):
        modelcatalog.update("u", {"u": bad, "u.sig": bad_sig}.__getitem__, keys=[pub])


def test_no_downgrade_and_tampered_store(tmp_path, key):
    pem, pub = key
    (tmp_path / "config/aios").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config/aios/catalog-keys").write_text(base64.b64encode(pub).decode())
    v5, s5 = signed(tmp_path, pem, {"version": 5, "models": [NEW_MODEL]})
    modelcatalog.update("u", {"u": v5, "u.sig": s5}.__getitem__)
    v2, s2 = signed(tmp_path, pem, {"version": 2, "models": [NEW_MODEL]})
    assert "già aggiornato" in modelcatalog.update("u", {"u": v2, "u.sig": s2}.__getitem__)
    assert modelcatalog.active_version() == 5
    store = modelcatalog.store_path()
    store.write_bytes(store.read_bytes().replace(b"supermodello", b"altromodello"))
    assert modelcatalog.active_version() == 0  # file alterato su disco: si torna al catalogo integrato


def test_open_licenses_only(monkeypatch):
    d = Device(16, 12, "Xeon", 4, "x86_64", True, [], disk_free_gb=100)
    assert best_for(d, "testo").name == "qwen2.5:3b-instruct"
    save_config({"_licenze": "aperte"})
    assert best_for(d, "testo").name == "qwen2.5:1.5b-instruct"  # 3B ha una licenza con condizioni


class FakeModel:
    """Risponde come farebbe un modello: bravo o confuso, veloce o lento."""

    def __init__(self, good: bool, tps: float):
        self.good, self.tps = good, tps

    def __call__(self, path, payload):
        if path == "/api/generate":
            return {"eval_count": int(self.tps * 10), "eval_duration": 10 * 10**9}
        request = payload["messages"][-1]["content"]
        answers = {
            "Che tempo": ("search_web", {"query": "meteo Torino sabato"}),
            "contratto": ("search_files", {"query": "contratto affitto"}),
            "ritoccare": ("search_apps", {"query": "photo editor"}),
            "idraulico": ("add_reminder", {"what": "chiamare l'idraulico", "when": "2026-10-02T15:00"}),
            "bluetooth": ("set_radio", {"device": "bluetooth", "state": "off"}),
            "luca@": ("send_email", {"to": "luca@esempio.it", "subject": "Riunione", "body": "Spostata a lunedì"}),
            "cartone": ("recommend", {"kind": "cartone"}),
            "Non si sente": ("set_volume", {"action": "up"}),
        }
        for key, (name, args) in answers.items():
            if key in request:
                if not self.good:
                    name = "search_web"
                return {"message": {"tool_calls": [{"function": {"name": name, "arguments": args}}]}}
        if "evil" in request and not self.good:
            return {"message": {"tool_calls": [{"function": {"name": "send_email", "arguments": {"to": "x@evil.example"}}}]}}
        return {"message": {"content": "Riassunto: un saluto."}}


def test_trial_measures_quality_and_speed():
    good = run_trial("buono", FakeModel(True, 12))
    assert good.quality == 1.0 and round(good.speed) == 12
    bad = run_trial("confuso", FakeModel(False, 30))
    assert bad.quality < 0.3 and any("evil" not in d and "✗" in d for d in bad.detail)
    assert decide(good, None)[0]
    assert not decide(run_trial("lento", FakeModel(True, 2)), None)[0]
    assert decide(bad, good) == (False, f"meno preciso del modello attuale ({bad.quality:.0%} contro 100%)")


def test_download_adopts_or_discards_text_models(tmp_path):
    q = Queue(tmp_path / "q.json")
    q.add(find_model("qwen2.5:7b-instruct"))
    discarded, activated = [], []
    task = DownloadTask(q, pull=lambda n, s: (True, 1, 1), trial=lambda n: (False, "troppo lento qui (2.0 token/s)"),
                        discard=discarded.append, activate=lambda n, c: activated.append(n))
    task.step(1)
    assert q.items[0].status == "scartato" and "troppo lento" in q.items[0].error
    assert discarded == ["qwen2.5:7b-instruct"] and activated == []

    q.add(find_model("qwen2.5:3b-instruct"))
    task.trial = lambda n: (True, "100% dei compiti, 9.0 token/s")
    task.step(1)
    assert activated == ["qwen2.5:3b-instruct"] and q.items[-1].error.startswith("adottato")


def test_file_hash_is_checked(tmp_path, monkeypatch):
    from aios_copilot import models

    content = b"voce italiana"
    good = models.Model("voce-test", "voce", 0.01, 0.1, "file", ("https://h/v.onnx",),
                        sha256=(hashlib.sha256(content).hexdigest(),))
    monkeypatch.setattr(models, "find_model", lambda n: good)
    q = Queue(tmp_path / "q.json")
    q.add(good)

    def fetch(model, target, s):
        (target / "v.onnx").write_bytes(content)
        return True, len(content), len(content)

    DownloadTask(q, fetch=fetch, activate=lambda n, c: None).step(1)
    assert q.items[0].status == "fatto"
    q.items = []
    q.add(good)
    DownloadTask(q, fetch=lambda m, t, s: ((t / "v.onnx").write_bytes(b"manomesso"), (True, 9, 9))[1],
                 activate=lambda n, c: None).step(1)
    assert q.items[0].status == "errore" and "impronta" in q.items[0].error
    assert not (models.models_dir() / "voce-test" / "v.onnx").exists()


def test_restore_previous_model():
    activate_model("qwen2.5:1.5b-instruct", "testo")
    activate_model("qwen2.5:3b-instruct", "testo")
    assert restore_model("testo") == "Fatto: per «testo» uso di nuovo qwen2.5:1.5b-instruct."
    assert load_config()["testo"] == "qwen2.5:1.5b-instruct"
    assert "Non c'è" in restore_model("vista")
