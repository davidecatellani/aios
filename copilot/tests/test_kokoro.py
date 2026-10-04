from aios_copilot import kokoro


def test_sentences_short_ones_join_the_next():
    assert kokoro.sentences("Ciao. Non hai collegato la posta: vuoi farlo adesso? Ti apro la schermata giusta.") == [
        "Ciao.", "Non hai collegato la posta: vuoi farlo adesso?", "Ti apro la schermata giusta."]


def test_speaks_sentence_by_sentence(tmp_path):
    import numpy as np

    made, played = [], []
    ok = kokoro.speak("Prima frase abbastanza lunga da stare da sola. Seconda frase, anche lei abbastanza lunga.",
                      "if_sara", play=lambda wav: played.append(wav.stat().st_size),
                      synth=lambda text, voice: made.append((text, voice)) or (np.zeros(2400, np.float32), 24000))
    assert ok and len(made) == 2 and len(played) == 2 and made[0][1] == "if_sara"
