import os
import struct
import time
from datetime import datetime
from pathlib import Path

import pytest

from aios_copilot import metadata, organize
from aios_copilot.agent import Agent
from aios_copilot.fastpath import FastPath, Intent
from aios_copilot.organize import Library
from aios_copilot.tools import organize as organize_tools


def jpeg(date: str | None, camera: str = "Pixel 8") -> bytes:
    """JPEG minimo con EXIF (fotocamera e data di scatto)."""
    model = camera.encode() + b"\0"
    tiff = bytearray(b"II*\x00" + struct.pack("<I", 8))
    entries = 2 if date else 1
    data_off = 8 + 2 + entries * 12 + 4
    exif_off = data_off + len(model)
    tiff += struct.pack("<H", entries) + struct.pack("<HHI", 0x0110, 2, len(model)) + struct.pack("<I", data_off)
    if date:
        tiff += struct.pack("<HHI", 0x8769, 4, 1) + struct.pack("<I", exif_off)
    tiff += struct.pack("<I", 0) + model
    if date:
        raw = date.encode() + b"\0"
        tiff += struct.pack("<H", 1) + struct.pack("<HHI", 0x9003, 2, len(raw)) + struct.pack("<I", exif_off + 18)
        tiff += struct.pack("<I", 0) + raw
    app1 = b"Exif\0\0" + bytes(tiff)
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xda" + b"\0" * 16


def mp3(title: str, artist: str, album: str = "") -> bytes:
    def frame(fid: bytes, text: str) -> bytes:
        body = b"\x03" + text.encode()
        return fid + struct.pack(">I", len(body)) + b"\0\0" + body

    frames = frame(b"TIT2", title) + frame(b"TPE1", artist) + (frame(b"TALB", album) if album else b"")
    n = len(frames)
    return b"ID3\x03\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + frames + b"\xff\xfb" * 64


def flac(artist: str, title: str) -> bytes:
    vendor = b"test"
    comments = [f"ARTIST={artist}".encode(), f"TITLE={title}".encode()]
    block = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    block += b"".join(struct.pack("<I", len(c)) + c for c in comments)
    return b"fLaC" + bytes([0x84]) + len(block).to_bytes(3, "big") + block


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for d in ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Music"):
        (h / d).mkdir(parents=True)
    desk, dl = h / "Desktop", h / "Downloads"
    (desk / "fattura-idraulico.txt").write_text("Fattura n. 117 del 12/03/2025. Imponibile 300 euro, IVA 22%.")
    (desk / "contratto affitto.txt").write_text("Contratto di locazione. Le parti convengono il recesso con 6 mesi.")
    (desk / "referto.txt").write_text("Referto esami del sangue 2026: valori nella norma.")
    (desk / "varie.txt").write_text("Cose a caso senza argomento preciso.")
    (desk / "Schermata del 2026-09-01.png").write_bytes(b"\x89PNG")
    (desk / "mare1.jpg").write_bytes(jpeg("2026:08:14 10:30:00"))
    (desk / "mare2.jpg").write_bytes(jpeg("2026:08:15 18:00:00"))
    (desk / "compleanno.jpg").write_bytes(jpeg("2026:09:20 20:00:00"))
    (desk / "vacanze.mp4").write_bytes(b"\0" * 100)
    (desk / "Azzurro.mp3").write_bytes(mp3("Azzurro", "Adriano Celentano", "Azzurro"))
    (desk / "Lucio Dalla - Caruso.flac").write_bytes(flac("Lucio Dalla", "Caruso"))
    (desk / ".nascosto.txt").write_text("fattura segreta")
    (desk / "progetto").mkdir()
    (desk / "progetto/.git").mkdir()
    (desk / "progetto/fattura.txt").write_text("fattura del progetto")  # dentro un repository: mai toccato
    (dl / "setup.exe").write_bytes(b"MZ")
    (dl / "film.mkv.part").write_bytes(b"\0")
    (dl / "Azzurro (1).mp3").write_bytes(mp3("Azzurro", "Adriano Celentano", "Azzurro"))  # duplicato
    old = time.time() - 60 * 86400
    os.utime(dl / "setup.exe", (old, old))
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "supertux.desktop").write_text("[Desktop Entry]\nType=Application\nName=SuperTux\nCategories=Game;\nExec=supertux2\n")
    (apps / "writer.desktop").write_text("[Desktop Entry]\nType=Application\nName=LibreOffice Writer\nCategories=Office;\nExec=lowriter\n")
    (apps / "notepad.desktop").write_text("[Desktop Entry]\nType=Application\nName=Notepad++\nCategories=Development;\n"
                                          "Exec=flatpak run com.usebottles.bottles -b Notepad\n")
    (apps / "nascosta.desktop").write_text("[Desktop Entry]\nType=Application\nName=X\nNoDisplay=true\n")
    return h, apps


def make_library(home_and_apps, tmp_path):
    home, apps = home_and_apps
    return Library(tmp_path / "lib.db", read_text=lambda p: p.read_text(errors="ignore") if p.suffix == ".txt" else "",
                   excluded=[], app_dirs=[apps])


def test_metadata_readers(tmp_path):
    (tmp_path / "a.jpg").write_bytes(jpeg("2026:08:14 10:30:00"))
    assert metadata.photo_date(tmp_path / "a.jpg") == datetime(2026, 8, 14, 10, 30)
    assert metadata.exif(tmp_path / "a.jpg")["camera"] == "Pixel 8"
    (tmp_path / "IMG_20250301_181500.jpg").write_bytes(b"\xff\xd8\xff\xda")
    assert metadata.photo_date(tmp_path / "IMG_20250301_181500.jpg") == datetime(2025, 3, 1, 18, 15)
    (tmp_path / "x.mp3").write_bytes(mp3("Azzurro", "Adriano Celentano", "Azzurro"))
    assert metadata.music_tags(tmp_path / "x.mp3") == {"title": "Azzurro", "artist": "Adriano Celentano", "album": "Azzurro"}
    (tmp_path / "y.flac").write_bytes(flac("Lucio Dalla", "Caruso"))
    assert metadata.music_tags(tmp_path / "y.flac") == {"artist": "Lucio Dalla", "title": "Caruso"}
    (tmp_path / "01 - Mina - Grande grande grande.mp3").write_bytes(b"")
    assert metadata.music_tags(tmp_path / "01 - Mina - Grande grande grande.mp3")["artist"] == "Mina"
    (tmp_path / "rotto.jpg").write_bytes(b"\xff\xd8\xff\xe1\x00")  # file troncato: nessun errore
    assert metadata.exif(tmp_path / "rotto.jpg") == {}


def test_collections(home, tmp_path):
    lib = make_library(home, tmp_path)
    assert lib.refresh()
    by_name = {Path(e.path).name: e for e in lib.entries()}
    assert by_name["fattura-idraulico.txt"].collection == "Fatture e ricevute" and by_name["fattura-idraulico.txt"].group == "2025"
    assert by_name["contratto affitto.txt"].collection == "Contratti"
    assert by_name["referto.txt"].collection == "Salute"
    assert by_name["varie.txt"].collection == "Altri documenti"
    assert by_name["Schermata del 2026-09-01.png"].kind == "screenshot"
    assert by_name["mare1.jpg"].group == by_name["mare2.jpg"].group == "14–15 agosto 2026"  # stesso momento
    assert by_name["compleanno.jpg"].group == "20 settembre 2026"
    assert by_name["Azzurro.mp3"].collection == "Musica · Adriano Celentano"
    assert by_name["Lucio Dalla - Caruso.flac"].collection == "Musica · Lucio Dalla"
    assert by_name["setup.exe"].group == "Windows"
    assert by_name["film.mkv.part"].collection == "Da riordinare"
    assert ".nascosto.txt" not in by_name and "fattura.txt" not in by_name  # nascosti e progetti: mai
    games = lib.entries(collection="Giochi")
    assert [e.detail["name"] for e in games] == ["SuperTux"]
    assert {e.detail["name"]: e.group for e in lib.entries(kind="app")} == {"LibreOffice Writer": "", "Notepad++": "Windows"}


def test_incremental_refresh_and_resume(home, tmp_path):
    lib = make_library(home, tmp_path)
    assert lib.refresh(deadline=time.monotonic() - 1) is False  # si ferma subito...
    assert lib.refresh() is True  # ...e riprende
    n = len(lib.entries())
    (home[0] / "Desktop/referto.txt").unlink()
    (home[0] / "Documents/bolletta.txt").write_text("Bolletta luce Enel, condominio via Roma")
    lib.refresh()
    names = {Path(e.path).name for e in lib.entries()}
    assert "referto.txt" not in names and "bolletta.txt" in names and len(lib.entries()) == n


def test_view_uses_links_and_respects_user_folders(home, tmp_path):
    lib = make_library(home, tmp_path)
    lib.refresh()
    view = lib.build_view()
    link = view / "Fatture e ricevute" / "2025" / "fattura-idraulico.txt"
    assert link.is_symlink() and link.resolve() == (home[0] / "Desktop/fattura-idraulico.txt").resolve()
    assert (view / "Foto" / "14–15 agosto 2026" / "mare1.jpg").is_symlink()
    lib.build_view()  # ricostruita senza errori
    (view / ".aios-raccolte").unlink()
    with pytest.raises(RuntimeError, match="non la tocco"):
        lib.build_view()  # una cartella «Raccolte» non nostra non viene mai cancellata


def test_cleanup_suggestions(home, tmp_path):
    lib = make_library(home, tmp_path)
    lib.refresh()
    reasons = {Path(p).name: why for p, why in lib.cleanup_suggestions()}
    assert reasons["film.mkv.part"] == "scaricamento interrotto"
    assert reasons["setup.exe"].startswith("installer Windows")
    assert any("copia identica" in why for why in reasons.values())


def test_tidy_plan_apply_undo(home, tmp_path):
    lib = make_library(home, tmp_path)
    lib.refresh()
    desk = home[0] / "Desktop"
    moves = organize.plan_tidy(lib, desk)
    targets = {Path(s).name: Path(d) for s, d in moves}
    assert targets["fattura-idraulico.txt"] == home[0] / "Documents/Fatture e ricevute/2025/fattura-idraulico.txt"
    assert targets["mare1.jpg"].parent == home[0] / "Pictures/14–15 agosto 2026"
    assert targets["Azzurro.mp3"].parent == home[0] / "Music/Adriano Celentano/Azzurro"
    assert "fattura.txt" not in targets  # il progetto con .git resta dov'è
    assert organize.apply_tidy(moves) == len(moves)
    assert not (desk / "mare1.jpg").exists() and targets["mare1.jpg"].exists()
    assert (desk / "progetto/fattura.txt").exists()
    assert organize.undo_tidy() == len(moves)
    assert (desk / "mare1.jpg").exists() and not (home[0] / "Pictures/14–15 agosto 2026").exists()
    assert organize.undo_tidy() == 0


def test_tools_and_router_end_to_end(home, tmp_path):
    lib = make_library(home, tmp_path)
    lib.refresh()
    tools = organize_tools.make_tools(lambda: lib)
    by = {t.name: t for t in tools}
    assert "Fatture e ricevute: 1" in by["list_collections"].func()
    assert "fattura-idraulico.txt" in by["show_collection"].func("fatture 2025")
    assert "non ho trovato" in by["show_collection"].func("fatture 2019").lower()
    assert "SuperTux" in by["show_collection"].func("giochi")
    assert "Notepad++" in by["show_collection"].func("app") and "(Windows)" in by["show_collection"].func("app")
    assert "mare1.jpg" in by["show_collection"].func("foto di agosto")
    assert by["tidy_apply"].requires_confirmation and by["tidy_undo"].requires_confirmation

    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM per queste frasi")

    asked = []
    agent = Agent(NoModel(), tools, lambda tool, args, **kw: asked.append(tool.name) or True,
                  routers=[FastPath(find_desktop=lambda n: None), organize_tools.OrganizeRouter()])
    plan = agent.ask("riordina la scrivania")
    assert plan.startswith("Riordino di Desktop: sposterei") and not (home[0] / "Documents/Contratti").exists()
    assert agent.ask("procedi con il riordino").startswith("Fatto:") and asked == ["tidy_apply"]
    assert (home[0] / "Documents/Contratti").exists()
    assert agent.ask("annulla il riordino").startswith("Annullato:")
    assert (home[0] / "Desktop/contratto affitto.txt").exists()


def test_router_order_keeps_folder_commands():
    fp, org = FastPath(find_desktop=lambda n: None), organize_tools.OrganizeRouter()

    def first(text):
        return fp.match(text) or org.match(text)

    assert first("apri le foto").tool == "open_location"  # resta l'apertura della cartella
    assert first("mostrami le fatture del 2025") == Intent("show_collection", {"what": "fatture del 2025"})
    assert first("che giochi ho?") == Intent("show_collection", {"what": "giochi"})
    assert first("le mie raccolte") == Intent("list_collections", {})
    assert first("metti in ordine i download") == Intent("tidy_plan", {"folder": "download"})
    assert first("cosa posso eliminare?") == Intent("cleanup_suggestions", {})


def test_learning_task_builds_view_after_full_refresh(home, tmp_path):
    from aios_copilot.learning import OrganizeTask

    lib = make_library(home, tmp_path)
    task = OrganizeTask(lib)
    assert task.has_work()
    task.step(5)
    assert (home[0] / "Raccolte" / ".aios-raccolte").exists() and not task.has_work()


def test_copilot_has_organize_tools(home):
    from aios_copilot.__main__ import make_agent

    agent = make_agent(lambda *a, **k: False)
    assert {"list_collections", "show_collection", "tidy_plan", "tidy_apply", "tidy_undo"} <= set(agent.tools)
    assert any(isinstance(r, organize_tools.OrganizeRouter) for r in agent.routers)
