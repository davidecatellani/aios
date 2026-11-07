import collections

import numpy as np

from aios_copilot import parlanti, voice


def test_merge_joins_short_pauses_and_splits_overlaps():
    turns = [(0.0, 2.0, 0), (2.3, 4.0, 0), (4.1, 6.0, 1), (5.5, 7.0, 0)]
    assert parlanti.merge(turns) == [(0.0, 4.0, 0), (4.1, 6.0, 1), (6.0, 7.0, 0)]


def test_main_voice_keeps_the_longest_speaker():
    samples = np.arange(parlanti.RATE * 6, dtype=np.float32)
    kept = parlanti.main_voice(samples, [(0.0, 1.0, 1), (1.0, 5.0, 0), (5.0, 6.0, 1)])
    assert len(kept) == parlanti.RATE * 4 and kept[0] == parlanti.RATE


def test_features_shape():
    mel = parlanti.features(np.zeros(parlanti.RATE * 2, np.float32))
    assert mel.shape == (200, 128)


def test_cache_compresses_to_its_size():
    cache = parlanti.SpeakerCache()
    rng = np.random.default_rng(0)
    for _ in range(4):
        n = 340
        embeds = np.concatenate([cache.get(), rng.standard_normal((n + 40, 512)).astype(np.float32)])
        logits = rng.standard_normal((len(embeds) * parlanti.SUB, parlanti.SPEAKERS)).astype(np.float32)
        cache.update(embeds, logits, np.zeros(512, np.float32), n)
    assert len(cache.embeds) == parlanti.CACHE and len(cache.fifo) <= parlanti.FIFO


def test_utterance_keeps_only_the_main_voice(tmp_path, monkeypatch):
    """In una frase con la TV accesa, Parakeet trascrive solo la voce di chi parla di più."""
    from test_voice import FakeRec

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    seen = []
    ears = voice.Ears(FakeRec, accept=lambda v: True, fine=lambda pcm: seen.append(len(pcm)) or "alza il volume",
                      voices=lambda samples: [(0.0, 0.05, 1), (0.05, 0.5, 0)])
    quiet = bytes(voice.CHUNK)
    text = ears.next_utterance(iter([quiet] * 3 + [b"alza", b"volume", b"|"]), collections.deque(maxlen=15))
    assert text == "alza il volume" and seen and seen[0] < sum(len(c) for c in [quiet] * 3 + [b"alza", b"volume"])
