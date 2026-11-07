import time

from aios_copilot import galleria
from aios_copilot.tools import foto


def test_faces_group_into_people_and_get_names(tmp_path):
    g = galleria.Gallery(tmp_path / "g.db", roots=lambda: [])
    aurora = [1.0, 0.1, 0.0, 0.0]
    marco = [0.0, 0.0, 1.0, 0.1]
    g.db.execute("INSERT INTO foto (percorso, mtime) VALUES ('a.jpg', 1), ('b.jpg', 2), ('c.jpg', 3)")
    g.add_faces("a.jpg", [((0, 0, 50, 50), aurora)])
    g.add_faces("b.jpg", [((0, 0, 50, 50), [0.95, 0.15, 0.02, 0.0]), ((60, 0, 50, 50), marco)])
    g.add_faces("c.jpg", [((0, 0, 50, 50), [0.9, 0.2, 0.0, 0.05])])
    people = g.people(min_faces=1)
    assert [p["foto"] for p in people] == [3, 1]
    g.name_person(people[0]["id"], "Aurora")
    assert {p for _, p in g.search("le foto di aurora")} == {"a.jpg", "b.jpg", "c.jpg"}
    # due gruppi con lo stesso nome diventano una persona sola
    g.name_person(people[1]["id"], "aurora")
    assert len(g.people(min_faces=1)) == 1


def test_search_by_content_and_text(tmp_path):
    g = galleria.Gallery(tmp_path / "g.db", roots=lambda: [])
    g.db.execute("INSERT INTO foto (percorso, mtime) VALUES ('mare.jpg', 1), ('scontrino.jpg', 2)")
    g.store_caption("mare.jpg", "Due bambini giocano sulla spiaggia al tramonto", ["mare", "spiaggia", "tramonto"], "")
    g.store_caption("scontrino.jpg", "Uno scontrino su un tavolo", ["scontrino", "carta"], "ESSELUNGA totale 23,40")
    assert [p for _, p in g.search("foto al mare")] == ["mare.jpg"]
    assert [p for _, p in g.search("lo scontrino dell'esselunga")] == ["scontrino.jpg"]
    assert g.stats()["descritte"] == 2


def test_scan_finds_new_and_removed_photos(tmp_path):
    pics = tmp_path / "Immagini"
    pics.mkdir()
    (pics / "uno.jpg").write_bytes(b"x")
    g = galleria.Gallery(tmp_path / "g.db", roots=lambda: [pics])
    assert g.scan(every=0) == 1 and g.pending("descritta") == [str(pics / "uno.jpg")]
    (pics / "uno.jpg").unlink()
    g._scanned = 0
    g.scan(every=0)
    assert g.stats()["foto"] == 0


class FakeCaptioner:
    def __init__(self, result=None, interrupted=False):
        self.result, self.interrupted, self.unreachable, self.error = result, interrupted, False, ""
        self.started = []

    def busy(self):
        return False

    def poke(self):
        pass

    def start(self, image):
        self.started.append(image)


def test_task_describes_one_photo_at_a_time(tmp_path, monkeypatch):
    galleria.set_enabled(True)
    g = galleria.Gallery(tmp_path / "g.db", roots=lambda: [])
    g.db.execute("INSERT INTO foto (percorso, mtime, volti_fatti) VALUES ('p.jpg', 1, 1)")
    monkeypatch.setattr(galleria, "small_jpeg", lambda path, *a, **k: b"jpeg")
    g._scanned = time.monotonic()  # la cartella finta è vuota: niente riscansione
    cap = FakeCaptioner()
    task = galleria.GalleryTask(g, faces=lambda: None, captioner=lambda m: cap, model=lambda: "minicpm-v4.6:1b")
    assert task.has_work()
    task.step(0.1)
    assert cap.started == [b"jpeg"]
    cap.result = {"descrizione": "Un cane sul prato", "etichette": ["cane", "prato"], "testo": ""}
    task.step(0.1)
    assert [p for _, p in g.search("il cane")] == ["p.jpg"]
    assert not task.has_work()


def test_interrupted_photo_is_retried(tmp_path, monkeypatch):
    galleria.set_enabled(True)
    g = galleria.Gallery(tmp_path / "g.db", roots=lambda: [])
    g.db.execute("INSERT INTO foto (percorso, mtime, volti_fatti) VALUES ('p.jpg', 1, 1)")
    monkeypatch.setattr(galleria, "small_jpeg", lambda path, *a, **k: b"jpeg")
    cap = FakeCaptioner(interrupted=True)
    task = galleria.GalleryTask(g, faces=lambda: None, captioner=lambda m: cap, model=lambda: "m")
    task.step(0.1)
    task.step(0.1)  # l'utente è tornato: niente risultato, la foto resta da fare
    assert g.pending("descritta") == ["p.jpg"]


def test_disabled_does_nothing(tmp_path):
    galleria.set_enabled(False)
    task = galleria.GalleryTask(galleria.Gallery(tmp_path / "g.db", roots=lambda: []), model=lambda: "m")
    assert not task.available() and not task.has_work()


def test_photos_router_and_tools(tmp_path, monkeypatch):
    r = foto.PhotosRouter()
    assert r.match("riconosci le mie foto").args == {"attiva": "si"}
    assert r.match("spegni il riconoscimento delle foto").args == {"attiva": "no"}
    assert r.match("a che punto è il riconoscimento delle foto?").tool == "photo_recognition_status"
    pics = tmp_path / "Immagini"
    pics.mkdir()
    (pics / "IMG_9.jpg").write_bytes(b"x")
    tools = {t.name: t for t in foto.make_tools(roots=lambda: [pics], recognized=lambda q: [pics / "IMG_9.jpg"])}
    assert "1 foto" in tools["show_photos"].func("del mare")
    monkeypatch.setattr(galleria, "vision_model", lambda: "minicpm-v4.6:1b")
    assert "Acceso" in tools["photo_recognition"].func("si") and galleria.enabled()
