from aios_copilot import cronologia_appunti as CA


class Clip:
    def __init__(self):
        self.types, self.data, self.copied = "", b"", []

    def __call__(self, cmd, data=None, timeout=5):
        if cmd[:2] == ["wl-paste", "--list-types"]:
            return 0, self.types.encode()
        if cmd[0] == "wl-paste":
            return 0, self.data
        if cmd[0] == "wl-copy":
            self.copied.append((cmd, data))
            return 0, b""
        return 1, b""


def test_history_records_text_images_and_never_passwords(tmp_path):
    clip, t = Clip(), [1000]
    h = CA.History(tmp_path, run=clip, clock=lambda: t[0])
    clip.types, clip.data = "text/plain;charset=utf-8\nUTF8_STRING", b"ciao"
    assert h.capture()["testo"] == "ciao"
    clip.types, clip.data = "text/plain\nx-kde-passwordManagerHint", b"segreta"
    assert h.capture() is None
    clip.types, clip.data = "image/png", b"\x89PNG fake"
    t[0] = 1001
    img = h.capture()
    assert img["tipo"] == "immagine" and (tmp_path / img["file"]).exists()
    clip.types, clip.data = "text/plain", b"ciao"
    t[0] = 1002
    h.capture()  # di nuovo: torna in cima, niente doppioni
    assert [i.get("testo", "img") for i in h.load()] == ["ciao", "img"]
    assert h.put(img["id"]) and clip.copied[-1][0] == ["wl-copy", "--type", "image/png"]
    assert h.put(h.load()[0]["id"]) and clip.copied[-1][1] == b"ciao"


def test_pinned_survive_limit_and_clear(tmp_path):
    t = [0]
    h = CA.History(tmp_path, run=Clip(), clock=lambda: t[0])
    first = h.add_text("da tenere")
    h.pin(first["id"])
    for i in range(CA.MAX_ITEMS + 5):
        t[0] += 1
        h.add_text(f"voce {i}")
    items = h.load()
    assert len(items) == CA.MAX_ITEMS + 1 and any(i["testo"] == "da tenere" for i in items)
    assert h.clear() == CA.MAX_ITEMS and [i["testo"] for i in h.load()] == ["da tenere"]
    assert h.clear(keep_pinned=False) == 1 and h.load() == []


def test_emoji_list_has_italian_words():
    emoji = CA.emoji_list()
    assert len(emoji) > 400
    heart = next(e for e in emoji if e["e"] == "❤️")
    assert "cuore" in heart["n"]
    assert any("pizza" in e["n"] and e["e"] == "🍕" for e in emoji)
    assert len({e["e"] for e in emoji}) == len(emoji)
