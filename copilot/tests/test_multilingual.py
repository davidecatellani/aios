import json
import math
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from aios_copilot import multilingual as ml
from aios_copilot.semantic import CATALOG, LexicalEncoder, Match


def fake_embedding(text: str, dims: int = 256) -> list[float]:
    """Vettore deterministico dalle feature lessicali: abbastanza per provare la catena."""
    v = [0.0] * dims
    for feat, weight in LexicalEncoder.features(text).items():
        v[zlib.crc32(feat.encode()) % dims] += weight
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeOllama(BaseHTTPRequestHandler):
    embed_calls = 0

    def log_message(self, *args):
        pass

    def _reply(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply({"models": [{"name": "fake-embed:latest"}, {"name": "fake-llm:latest"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            FakeOllama.embed_calls += 1
            self._reply({"embeddings": [fake_embedding(t) for t in body["input"]]})
        elif self.path == "/api/chat":
            # "Traduzione": restituisce le frasi con un prefisso, una per riga.
            lines = body["messages"][0]["content"].split("\n\n", 1)[1].splitlines()
            self._reply({"message": {"content": "\n".join(f"- es {line}" for line in lines)}})
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def ollama(monkeypatch, tmp_path):
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("AIOS_OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var))
    yield server
    server.shutdown()


def test_setup_end_to_end(ollama, capsys):
    assert ml.main(["--models", "fake-embed,missing-model", "--translate", "es", "--llm", "fake-llm"]) == 0
    out = capsys.readouterr().out
    assert "missing-model: non installato" in out

    translations = ml.load_translations()
    assert translations["volume_up"][0] == "es alza il volume"  # pulita da trattini e spazi

    config = ml.load_config()
    assert config.model == "fake-embed"
    assert config.false_accepts == 0  # la regola di sicurezza vale anche sulla metà di verifica

    # Il router neurale si costruisce dalla configurazione; dal secondo avvio gli
    # esempi arrivano tutti dalla cache su disco, senza chiamare il modello.
    ml.neural_router(config)
    calls_before = FakeOllama.embed_calls
    router = ml.neural_router(config)
    assert FakeOllama.embed_calls == calls_before
    assert router.match("alza il volume").args == {"action": "up"}
    assert ml.main(["--status"]) == 0


def test_setup_without_ollama(monkeypatch, capsys):
    monkeypatch.setenv("AIOS_OLLAMA_URL", "http://127.0.0.1:9")
    assert ml.main([]) == 1
    assert "Ollama non raggiungibile" in capsys.readouterr().out


def _match(name, score, runner_up=0.0):
    spec = next(s for s in CATALOG if s.name == name)
    return Match(spec, score, runner_up, spec.examples[0])


def test_choose_thresholds_never_accepts_out_of_scope():
    scored = [
        ("alza il volume", "volume_up", _match("volume_up", 0.9)),
        ("abbassa", "volume_down", _match("volume_down", 0.7)),
        ("fuori tema", None, _match("mute", 0.75)),
        ("dubbio", "lock", _match("reboot", 0.6)),
    ]
    threshold, margin, report = ml.choose_thresholds(scored)
    assert 0.75 < threshold <= 0.9
    assert report["false_accepts"] == [] and report["precision"] == 1.0


def test_negated_out_of_scope_is_rejected_at_any_threshold():
    scored = [("no apagues el wifi", None, _match("wifi_off", 0.95))]
    assert ml.measure(scored, 0.2, 0.0)["false_accepts"] == []


def test_eval_phrases_are_removed_from_catalog():
    catalog = ml.with_examples(CATALOG, {"volume_up": ["sube el volumen", "dale más volumen"]})
    cleaned = {s.name: s for s in ml.without_eval_phrases(catalog)}
    assert "sube el volumen" not in cleaned["volume_up"].examples  # è una frase di prova
    assert "dale más volumen" in cleaned["volume_up"].examples


def test_safest_model_wins():
    risky = ml.NeuralConfig("a", 0.5, 0.0, precision=1.0, coverage=0.95, false_accepts=1, ms_per_query=5)
    safe = ml.NeuralConfig("b", 0.7, 0.0, precision=0.98, coverage=0.80, false_accepts=0, ms_per_query=30)
    assert ml.is_better(safe, risky) and not ml.is_better(risky, safe)
