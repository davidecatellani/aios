import os
import time
from datetime import datetime

import pytest

from aios_copilot import versioni
from aios_copilot.tools.versioni import VersionsRouter, make_tools


@pytest.fixture
def casa(tmp_path, monkeypatch):
    home = tmp_path / "casa"
    (home / "Documenti").mkdir(parents=True)
    monkeypatch.setenv("AIOS_CASA", str(home))
    return home


def write(p, text, when):
    p.write_text(text)
    os.utime(p, (when, when))


def test_versions_restore_and_changes(casa, tmp_path):
    clock = [datetime(2026, 10, 6, 18, 0).timestamp()]
    s = versioni.Store(tmp_path / "versioni", clock=lambda: clock[0])
    doc = casa / "Documenti" / "Contratto affitto.txt"
    write(doc, "Canone: 700 euro\nDurata: 4 anni\n", datetime(2026, 10, 5, 10, 0).timestamp())
    (casa / "Documenti" / "foto.jpg").write_bytes(b"jpg")  # non è un documento
    (casa / ".nascosto").mkdir()
    write(casa / ".nascosto" / "x.txt", "x", clock[0])
    assert s.scan() == 1
    assert s.scan() == 0  # niente di nuovo
    write(doc, "Canone: 750 euro\nDurata: 4 anni\nCauzione: 2 mensilità\n", datetime(2026, 10, 6, 11, 0).timestamp())
    assert s.scan() == 1
    vs = s.versions(doc)
    assert len(vs) == 2 and vs[0]["t"] > vs[1]["t"]

    # «com'era ieri» → la versione salvata prima di mezzanotte
    old = s.at(doc, versioni.moment("ieri", datetime(2026, 10, 6, 18, 0)))
    assert old["id"] == vs[1]["id"] and s.previous(doc)["id"] == vs[1]["id"]
    diff = s.changes(doc, old["id"])
    assert "+ Canone: 750 euro" in diff and "+ Cauzione: 2 mensilità" in diff and "- Canone: 700 euro" in diff

    msg = s.restore(doc, old["id"])
    assert "com'era ieri alle 10:00" in msg and doc.read_text() == "Canone: 700 euro\nDurata: 4 anni\n"
    # la versione di prima del ripristino resta: si può tornare indietro
    assert "Cauzione" in s.file_of(s.previous(doc)["id"]).read_text()
    assert s.find("il contratto") == doc and s.find("contratti") == doc and s.find("bolletta") is None
    u = s.usage()
    assert u["documenti"] == 1 and u["versioni"] >= 2


def test_old_documents_without_reflink_are_followed_not_copied(casa, tmp_path, monkeypatch):
    now = time.time()
    s = versioni.Store(tmp_path / "v", clock=lambda: now)
    s._reflink = False
    monkeypatch.setattr(s, "can_reflink", lambda: False)
    old = casa / "Documenti" / "tesi.odt"
    write(old, "vecchia", now - 400 * 86400)
    assert s.scan() == 0 and s.versions(old) == []
    write(old, "nuova", now)
    assert s.scan() == 1  # da quando cambia, si segue


def test_thinning_keeps_recent_and_spreads_old(tmp_path, casa):
    now = datetime(2026, 10, 6, 12, 0).timestamp()
    s = versioni.Store(tmp_path / "v", clock=lambda: now)
    doc = casa / "Documenti" / "note.md"
    for i in range(30):  # una versione ogni 10 minuti, 3 giorni fa
        write(doc, f"versione {i}", now - 3 * 86400 + i * 600)
        s.snapshot(doc)
    for i in range(5):  # e 5 nell'ultima ora
        write(doc, f"oggi {i}", now - 3000 + i * 600)
        s.snapshot(doc)
    s.thin()
    ts = [v["t"] for v in s.versions(doc)]
    assert len([t for t in ts if now - t < 86400]) == 5  # l'ultimo giorno: tutte
    assert len([t for t in ts if now - t > 86400]) == 5  # tre giorni fa: una all'ora (5 ore diverse)


def test_moments():
    now = datetime(2026, 10, 7, 15, 30)  # mercoledì
    assert versioni.moment("ieri", now) == datetime(2026, 10, 7).timestamp()
    assert versioni.moment("ieri mattina", now) == datetime(2026, 10, 6, 13).timestamp()
    assert versioni.moment("stamattina", now) == datetime(2026, 10, 7, 13).timestamp()
    assert versioni.moment("due ore fa", now) == datetime(2026, 10, 7, 13, 30).timestamp()
    assert versioni.moment("mezz'ora fa", now) == datetime(2026, 10, 7, 15).timestamp()
    assert versioni.moment("2 ore fa", now) == datetime(2026, 10, 7, 13, 30).timestamp()
    assert versioni.moment("un'ora fa", now) == datetime(2026, 10, 7, 14, 30).timestamp()
    assert versioni.moment("lunedì", now) == datetime(2026, 10, 6).timestamp()  # fine di lunedì
    assert versioni.moment("il 3 ottobre", now) == datetime(2026, 10, 4).timestamp()
    assert versioni.moment("boh", now) is None


def test_router_and_tools(casa, tmp_path):
    r = VersionsRouter()
    assert r.match("rimetti il contratto com'era ieri").args == {"documento": "il contratto", "quando": "ieri"}
    assert r.match("ripristina la tesi come era stamattina").args == {"documento": "la tesi", "quando": "stamattina"}
    assert r.match("rimetti la relazione com'era due ore fa").args == {"documento": "la relazione", "quando": "due ore fa"}
    assert r.match("rimetti la versione precedente del preventivo").args == {"documento": "preventivo", "quando": "precedente"}
    assert r.match("le versioni della tesi").tool == "document_versions"
    assert r.match("cosa è cambiato nel contratto da ieri?").args == {"documento": "contratto", "da": "ieri"}
    assert r.match("rimetti tutto com'era stamattina") is None  # è il registro delle azioni di Nova
    s = versioni.Store(tmp_path / "v")
    doc = casa / "Documenti" / "preventivo cucina.txt"
    write(doc, "totale 5000", time.time() - 7200)
    s.scan()
    write(doc, "totale 4800", time.time())
    s.scan()
    tools = {t.name: t for t in make_tools(lambda: s)}
    assert tools["restore_document"].requires_confirmation
    assert "Le versioni di «preventivo cucina.txt» (2)" in tools["document_versions"].func(documento="preventivo")
    assert "+ totale 4800" in tools["document_changes"].func(documento="preventivo")
    assert "Ho rimesso" in tools["restore_document"].func(documento="preventivo", quando="")
    assert doc.read_text() == "totale 5000"
    assert "Non trovo versioni" in tools["document_versions"].func(documento="bolletta gas")
