import math

from aios_copilot import galleria
from aios_copilot.tools.foto import PhotosRouter


def unit(*v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def gallery(tmp_path, prints):
    g = galleria.Gallery(tmp_path / "g.db", roots=lambda: [])
    for i, (name, vec) in enumerate(prints.items()):
        g.db.execute("INSERT INTO foto (percorso, mtime) VALUES (?, ?)", (name, i))
        g.store_print(name, vec)
    return g


def test_meaning_search_and_hub_correction(tmp_path):
    g = gallery(tmp_path, {"mare.jpg": unit(1, 0, 0.2), "gatto.jpg": unit(0, 1, 0.2), "scontrino.jpg": unit(0.5, 0.5, 1)})
    neutral = unit(0, 0, 1)
    # lo scontrino «somiglia a tutto»: senza correzione vincerebbe, con la correzione vince il mare
    assert g.search_meaning(unit(0.8, 0.3, 0.5), neutral)[0][1] == "mare.jpg"
    assert g.stats()["impronte"] == 3


def test_duplicates_groups(tmp_path):
    g = gallery(tmp_path, {"a1.jpg": unit(1, 0, 0), "a2.jpg": unit(1, 0.05, 0), "b.jpg": unit(0, 1, 0),
                           "c1.jpg": unit(0, 0, 1), "c2.jpg": unit(0.02, 0, 1), "c3.jpg": unit(0, 0.03, 1)})
    groups = g.duplicates()
    assert [sorted(x) for x in groups] == [["c1.jpg", "c2.jpg", "c3.jpg"], ["a1.jpg", "a2.jpg"]]


def test_fingerprints_survive_old_archives(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "vecchio.db")
    db.execute("CREATE TABLE foto (percorso TEXT PRIMARY KEY, mtime REAL, descrizione TEXT DEFAULT '', etichette TEXT "
               "DEFAULT '', testo TEXT DEFAULT '', descritta INTEGER DEFAULT 0, volti_fatti INTEGER DEFAULT 0)")
    db.execute("INSERT INTO foto (percorso, mtime) VALUES ('x.jpg', 1)")
    db.commit()
    g = galleria.Gallery(tmp_path / "vecchio.db", roots=lambda: [])
    assert g.pending("impronta_fatta") == ["x.jpg"]


def test_duplicate_phrases():
    r = PhotosRouter()
    for text in ["trova le foto doppie", "ci sono foto duplicate?", "trovami i doppioni delle foto"]:
        assert r.match(text).tool == "duplicate_photos", text
