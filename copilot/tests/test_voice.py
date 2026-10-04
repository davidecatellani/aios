"""Nova a voce: parola di attivazione, frase, risposta parlata, conferme, comandi."""

import json

from aios_copilot import voice
from aios_copilot.agent import Agent
from aios_copilot.tools import voice as voice_tools


class FakeRec:
    """Un riconoscitore Vosk finto: ogni pezzo di audio è una parola (in byte)."""

    def __init__(self, grammar):
        self.grammar, self.words = grammar, []

    def AcceptWaveform(self, data):
        word = data.decode()
        if self.grammar and word not in json.loads(self.grammar):
            word = ""  # fuori dalla grammatica: non lo «sente»
        if word == "|":
            return True  # pausa: frase finita
        if word:
            self.words.append(word)
        return False

    def Result(self):
        text, self.words = " ".join(self.words), []
        return json.dumps({"text": text})

    def PartialResult(self):
        return json.dumps({"partial": " ".join(self.words)})

    def FinalResult(self):
        return self.Result()


def chunks(*words):
    return iter([w.encode() for w in words])


def test_wake_word_then_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    ears = voice.Ears(FakeRec)
    audio = chunks("che", "bella", "giornata", "nova", "alza", "il", "volume", "|", "dopo")
    import collections

    recent = collections.deque(maxlen=15)
    assert ears.wait_for_wake(audio, recent)
    assert ears.transcribe(audio, recent=None) == "alza il volume"


def test_does_not_hear_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    voice.speaking_flag().touch()  # Nova sta parlando e dice il suo nome
    import collections

    assert not voice.Ears(FakeRec).wait_for_wake(chunks("sono", "nova"), collections.deque())


def test_text_helpers():
    assert voice.strip_wake("ehi nova, che ore sono") == "che ore sono"
    assert voice.heard_wake("ok nova") and not voice.heard_wake("novanta euro")
    assert voice.yes_no("sì procedi") is True and voice.yes_no("no lascia stare") is False
    assert voice.yes_no("boh") is None
    spoken = voice.for_speech("✅ Fatto! Guarda https://esempio.it/a/b «qui»\n" + "\n".join(f"- riga {i}" for i in range(10)))
    assert "https" not in spoken and "✅" not in spoken and "«" not in spoken and "sullo schermo" in spoken


def test_speak_uses_piper_then_plays(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("AIOS_VOCE", str(tmp_path / "voce"))
    (tmp_path / "voce").mkdir()
    (tmp_path / "voce" / "it_IT-paola-medium.onnx").write_bytes(b"x")
    ran = []

    def run(cmd, **kw):
        ran.append(cmd)
        if cmd[0] == "/bin/piper":
            out = cmd[cmd.index("--output_file") + 1]
            open(out, "wb").write(b"0" * 100)
            assert kw["input"] == "Fatto: il volume è al 60%."
            assert voice.speaking_flag().exists()  # mentre parla non ascolta

    which = {"piper": "/bin/piper", "pw-play": "/bin/pw-play"}.get
    assert voice.speak("✅ Fatto: il volume è al 60%.", which=which, run=run)
    assert ran[1][0] == "pw-play" and not voice.speaking_flag().exists()
    ran.clear()
    assert voice.speak("ciao", which={"espeak-ng": "/bin/espeak-ng"}.get, run=lambda cmd, **kw: ran.append(cmd))
    assert ran == [["espeak-ng", "-v", "it", "-s", "165", "ciao."]]


def test_capture_command_prefers_pipewire():
    assert voice.capture_command({"pw-record": "x", "arecord": "y"}.get)[0] == "pw-record"
    assert voice.capture_command({"arecord": "y"}.get)[0] == "arecord"
    assert voice.capture_command(lambda p: None) is None


def test_on_off_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    class R:
        ran = []

        def run(self, cmd):
            self.ran.append(cmd)
            return 0, ""

    r = R()
    agent = Agent(NoModel(), voice_tools.make_tools(r), confirm=lambda *a, **k: True, routers=[voice_tools.VoiceRouter()])
    assert agent.ask("smetti di ascoltare").startswith("Non ascolto più") and not voice.listening_enabled()
    assert "In ascolto" not in agent.ask("mi stai ascoltando?")
    assert agent.ask("ascoltami").startswith("Ti ascolto") and voice.listening_enabled()
    assert ["systemctl", "--user", "enable", "--now", "aios-voce.service"] in r.ran


def test_install_recommended_models_is_understood():
    from aios_copilot.tools.ai import ModelsRouter

    for text in ("installa tutti i consigliati", "installa i modelli consigliati", "scarica i modelli"):
        intent = ModelsRouter().match(text)
        assert intent is not None and intent.tool == "install_models", text


def test_missing_model_is_explained_and_downloaded(monkeypatch, tmp_path):
    import http.server
    import threading

    from aios_copilot import llm

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"model \'qwen2.5:1.5b-instruct\' not found"}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    pulled = []
    monkeypatch.setattr(llm, "start_pull", lambda model: pulled.append(model) or True)
    client = llm.OllamaClient(model="qwen2.5:1.5b-instruct", url=f"http://127.0.0.1:{srv.server_address[1]}")
    try:
        client.chat([{"role": "user", "content": "ciao"}], [])
        raise AssertionError("doveva fallire")
    except llm.LLMError as exc:
        assert "lo sto scaricando" in str(exc) and "404" not in str(exc)
    assert pulled == ["qwen2.5:1.5b-instruct"]
    srv.shutdown()


def test_silence_does_not_wake_the_recognizer(tmp_path, monkeypatch):
    """In una stanza silenziosa il riconoscitore non lavora (batteria)."""
    import collections

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    fed = []

    class Counting(FakeRec):
        def AcceptWaveform(self, data):
            fed.append(data)
            return super().AcceptWaveform(data)

    quiet = bytes(voice.CHUNK)
    assert voice.loudness(quiet) == 0
    assert not voice.Ears(Counting).wait_for_wake(iter([quiet] * 50), collections.deque(maxlen=15))
    assert fed == []
    assert voice.Ears(Counting).wait_for_wake(iter([quiet] * 5 + [b"nova"]), collections.deque(maxlen=15))
