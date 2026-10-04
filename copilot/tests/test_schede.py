import os
import time
from datetime import date

from aios_copilot import schede
from aios_copilot.tools import foto
from aios_copilot.tools.base import attach, set_attach_sink


def test_titles_from_answers():
    assert schede.extract_titles("Ti consiglio «Dark», «Severance» e «The Bear».") == ["Dark", "Severance", "The Bear"]
    text = "1. Stranger Things (2016) - fantascienza\n2. **Squid Game**: thriller\n3. Mare fuori – italiana"
    assert schede.extract_titles(text) == ["Squid Game"]  # il grassetto vince sull'elenco
    assert schede.extract_titles("1. Stranger Things (2016) - fantascienza\n2. Mare fuori – italiana") == \
        ["Stranger Things", "Mare fuori"]
    assert schede.media_cards("che serie tv mi consigli?", "«Dark»") == [{"titolo": "Dark", "tipo": "serie"}]
    assert schede.media_cards("che ore sono?", "«Dark»") == []


def test_lookup_uses_wikipedia_and_justwatch():
    asked = []

    def get(url):
        asked.append(url)
        if "serie_televisiva" in url:
            return {"type": "standard", "extract": "Dark è una serie televisiva tedesca.",
                    "thumbnail": {"source": "https://upload.wikimedia.org/x.jpg"},
                    "content_urls": {"desktop": {"page": "https://it.wikipedia.org/wiki/Dark"}}}
        raise OSError
    card = schede.lookup("Dark", "serie", get)
    assert card["immagine"] == "https://upload.wikimedia.org/x.jpg"
    assert card["url"].endswith("/Dark") and "justwatch" in card["dove"]
    assert schede.lookup("Senza rete", "film", lambda u: (_ for _ in ()).throw(OSError()))["dove"]


def test_thumbnails_only_from_allowed_sites(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert schede.thumbnail("https://evil.example/x.jpg", lambda u: b"x") is None
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    assert schede.thumbnail("https://upload.wikimedia.org/a.png", lambda u: png) == (png, "image/png")
    assert schede.thumbnail("https://upload.wikimedia.org/a.png", lambda u: b"cambiato")[0] == png  # dalla cache
    assert schede.thumbnail("https://upload.wikimedia.org/s.svg", lambda u: b"<svg onload=x>") is None


def test_photos_by_name_and_period(tmp_path):
    pics = tmp_path / "Immagini"
    (pics / "Compleanno Aurora").mkdir(parents=True)
    (pics / "Mare").mkdir()
    a = pics / "Compleanno Aurora" / "IMG_1.jpg"
    b = pics / "Mare" / "aurora_spiaggia.jpg"
    c = pics / "Mare" / "tramonto.jpg"
    for p in (a, b, c):
        p.write_bytes(b"x")
    agosto = time.mktime((2026, 8, 10, 12, 0, 0, 0, 0, -1))
    os.utime(c, (agosto, agosto))
    found = foto.find_photos("di Aurora", [pics])
    assert set(found) == {a, b}
    assert foto.find_photos("di agosto", [pics], today=date(2026, 10, 4)) == [c]
    assert foto.find_photos("di Marco", [pics]) == []
    assert foto.PhotosRouter().match("mostrami le foto di Aurora").args == {"query": "di aurora"}
    assert foto.PhotosRouter().match("mostrami le foto") is None  # quella è l'app Foto


def test_cards_reach_the_page():
    got = []
    set_attach_sink(lambda kind, items, title: got.append((kind, title, items)))
    attach("file", [{"titolo": "contratto.pdf"}], "File trovati")
    set_attach_sink(None)
    attach("file", [{"titolo": "perso.pdf"}])
    assert got == [("file", "File trovati", [{"titolo": "contratto.pdf"}])]
