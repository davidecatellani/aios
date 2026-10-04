"""Nova risponde solo alle voci che conosce, se l'utente lo sceglie."""

import json

from aios_copilot import voice, voiceprint as vp


def vec(*xs):
    return list(xs) + [0.0] * (128 - len(xs))


def test_enroll_and_recognize(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert vp.accepted(vec(1, 0)) is True  # nessuna impronta: risponde a tutti
    davide = [vec(1, 0.1, 0.05), vec(1, 0.05, 0.1), vec(0.95, 0.1, 0), vec(1, 0, 0.1), vec(0.9, 0.1, 0.1)]
    vp.enroll("Davide", davide)
    assert vp.load()["solo_conosciute"] is True
    assert vp.who(vec(1, 0.08, 0.05))[0] == "Davide"
    assert vp.accepted(vec(0, 1, 0)) is False  # il film
    assert vp.accepted(None) is True  # frase troppo corta per riconoscerla: meglio rispondere
    vp.set_only_known(False)
    assert vp.accepted(vec(0, 1, 0)) is True  # «ascolta tutti»
    vp.forget("Davide")
    assert vp.load() == {"persone": [], "solo_conosciute": False}


def test_unknown_voice_is_ignored_by_ears():
    class Rec:
        def __init__(self, spk):
            self.spk = spk

        def AcceptWaveform(self, data):
            return False

        def PartialResult(self):
            return json.dumps({"partial": "che ore sono"})

        def FinalResult(self):
            return json.dumps({"text": "nova che ore sono", "spk": self.spk})

    known = vec(1, 0)
    ears = voice.Ears(recognizer=lambda g: Rec(vec(0, 1)), accept=lambda v: v == known)
    assert ears.transcribe(iter([b"x"] * 3)) == ""  # voce sconosciuta: niente
    ears = voice.Ears(recognizer=lambda g: Rec(known), accept=lambda v: v == known)
    assert ears.transcribe(iter([b"x"] * 3)) == "che ore sono"
