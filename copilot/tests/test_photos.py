import json
import os
import shutil
import ssl
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from aios_copilot.agent import Agent
from aios_copilot.mesh import photos
from aios_copilot.mesh.calls import Ofono
from aios_copilot.mesh.files import Devices, FileShare, PhoneServer
from aios_copilot.mesh.messages import PhoneBus
from aios_copilot.mesh.phone import KdeConnect
from aios_copilot.mesh.service import MeshService
from aios_copilot.tools import phone as phone_tools
from aios_copilot.tools.base import Runner

PHONE = "1a2b3c4d"


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "home").mkdir()


@pytest.fixture
def phone_root(tmp_path):
    root = tmp_path / "telefono"
    files = {
        "storage/emulated/0/DCIM/Camera/IMG_20261003_101500.jpg": b"\xff\xd8\xff foto1",
        "storage/emulated/0/DCIM/Camera/IMG_20261003_101600.jpg": b"\xff\xd8\xff foto2",
        "storage/emulated/0/DCIM/Camera/VID_20261003_102000.mp4": b"video" * 100,
        "storage/emulated/0/DCIM/Camera/.trashed-IMG_1.jpg": b"cestino",
        "storage/emulated/0/DCIM/Screenshots/Screenshot_1.png": b"screen",
        "storage/emulated/0/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/IMG-WA0001.jpg": b"wa",
        "storage/emulated/0/Pictures/Telegram/IMG_2.jpg": b"tg",
        "storage/emulated/0/Download/volantino.jpg": b"dl",
    }
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        os.utime(root / rel, (1_759_480_000, 1_759_480_000))  # ottobre 2025
    return root


def test_only_camera_photos_and_videos(phone_root):
    names = [f.name for f in photos.camera_files(phone_root)]
    assert names == ["IMG_20261003_101500.jpg", "IMG_20261003_101600.jpg", "VID_20261003_102000.mp4"]


def test_sync_copies_only_whats_missing(phone_root, tmp_path):
    pics, vids = tmp_path / "Immagini/Telefono", tmp_path / "Video/Telefono"
    sync = photos.PhotoSync(tmp_path / "db.sqlite", pics, vids)
    first = sync.run(PHONE, phone_root)
    assert (first["foto"], first["video"]) == (2, 1)
    month = pics / "2026" / "10"  # data dello scatto dal nome del file
    assert sorted(p.name for p in month.iterdir()) == ["IMG_20261003_101500.jpg", "IMG_20261003_101600.jpg"]
    assert (vids / "2026" / "10" / "VID_20261003_102000.mp4").exists()
    assert sync.run(PHONE, phone_root)["foto"] == 0  # la seconda volta: niente doppioni
    assert "già sul PC" in photos.describe(sync.run(PHONE, phone_root), "Pixel 8")

    new = phone_root / "storage/emulated/0/DCIM/Camera/IMG_20261004_090000.jpg"
    new.write_bytes(b"\xff\xd8\xff nuova")
    (month / "IMG_20261003_101500.jpg").write_bytes(b"modificata sul PC")  # mai sovrascritta
    result = sync.run(PHONE, phone_root)
    assert result["foto"] == 1 and (month / "IMG_20261003_101500.jpg").read_bytes() == b"modificata sul PC"
    assert sync.last_batch()[0].name == "IMG_20261004_090000.jpg"


def test_sync_resumes_after_a_deadline(phone_root, tmp_path):
    sync = photos.PhotoSync(tmp_path / "db.sqlite", tmp_path / "p", tmp_path / "v")
    result = sync.run(PHONE, phone_root, deadline=time.monotonic() - 1)
    assert result["mancanti"] == 3 and "continuo appena posso" in photos.describe(result, "Pixel")
    assert sync.run(PHONE, phone_root)["mancanti"] == 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="serve ffmpeg")
def test_enhance_keeps_the_original(tmp_path):
    photo = tmp_path / "IMG_1.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240", "-frames:v", "1",
                    str(photo)], check=True)
    before = photo.read_bytes()
    out, method = photos.enhance(photo, which=lambda p: shutil.which(p) if p == "ffmpeg" else None)
    assert out.name == "IMG_1 (migliorata).jpg" and out.stat().st_size > 0 and "correzione automatica" in method
    assert photo.read_bytes() == before
    ran = []

    def fake(cmd):
        ran.append(cmd[0])
        Path(cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1]).write_bytes(b"x")
        return 0, ""

    out, method = photos.enhance(photo, run=fake, which=lambda p: p)
    assert ran == ["realesrgan-ncnn-vulkan", "ffmpeg"] and "Real-ESRGAN" in method


class FakePhone(Runner):
    def __init__(self, mount: Path):
        super().__init__(which=lambda p: p if p in ("kdeconnect-cli", "busctl", "ffmpeg") else None)
        self.mount, self.ran = mount, []

    def run(self, cmd):
        self.ran.append(cmd)
        if cmd[:2] == ["kdeconnect-cli", "-l"]:
            return 0, f"- Pixel 8: {PHONE} (paired and reachable)\n"
        if cmd[:2] == ["busctl", "--user"]:
            if cmd[-1] == "mountAndWait":
                return 0, json.dumps({"type": "b", "data": [True]})
            if cmd[-1] == "mountPoint":
                return 0, json.dumps({"type": "s", "data": [str(self.mount)]})
            return 0, json.dumps({"type": "as", "data": [[]]})
        return 0, ""


def test_copilot_saves_photos_from_the_phone(phone_root, tmp_path):
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    r = FakePhone(phone_root)
    agent = Agent(NoModel(), phone_tools.make_tools(r), confirm=lambda *a, **k: True, routers=[phone_tools.PhoneRouter()])
    out = agent.ask("salva le foto sul PC")
    assert out.startswith("Salvati sul PC 2 foto e 1 video dalla fotocamera di Pixel 8") and "WhatsApp" in out
    assert "già sul PC" in agent.ask("sincronizza le foto")
    from aios_copilot.xdg import resolve_folder

    assert len(list((resolve_folder("PICTURES") / "Telefono").rglob("*.jpg"))) == 2


def test_service_saves_photos_when_the_phone_arrives(phone_root, tmp_path):
    r = FakePhone(phone_root)
    notes = []
    server = type("S", (), {"running": True, "pairing": type("P", (), {"code": ""})(), "start": lambda s, **k: None,
                            "stop": lambda s: None})()
    svc = MeshService(KdeConnect(r), Ofono(r), server, notify=lambda t, b: notes.append((t, b)), bus=PhoneBus(r))
    svc.photos = photos.PhotoSync(tmp_path / "db.sqlite", tmp_path / "p", tmp_path / "v")
    svc.tick()
    deadline = time.time() + 5
    while not any(t.startswith("📷") for t, _ in notes) and time.time() < deadline:
        time.sleep(0.05)
    assert ("📷 Foto salvate sul PC" in [t for t, _ in notes]) and any("2 foto e 1 video" in b for _, b in notes)


def test_upload_from_iphone_page(tmp_path):
    srv = PhoneServer(FileShare(tmp_path / "home"), Devices(tmp_path / "devices.json"))
    srv.upload_dir = tmp_path / "caricate"
    port = srv.start("127.0.0.1", 0, cert_dir=tmp_path)
    key = srv.devices.add("iPhone")
    ctx = ssl.create_default_context(cafile=str(tmp_path / "pc.crt"))
    ctx.check_hostname = False

    def send(name, data, auth=True):
        req = urllib.request.Request(f"https://127.0.0.1:{port}/api/carica?nome={urllib.request.quote(name)}", data=data,
                                     method="POST", headers={"Authorization": f"Bearer {key}"} if auth else {})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        assert send("IMG_0001.HEIC", b"heic" * 1000) == (200, {"salvato": "IMG_0001.HEIC"})
        assert (tmp_path / "caricate/IMG_0001.HEIC").stat().st_size == 4000
        assert send("../../.bashrc.jpg", b"x")[1]["salvato"] == "bashrc.jpg"  # nessuna uscita dalla cartella
        assert send("virus.sh", b"x")[0] == 400
        assert send("IMG_0002.jpg", b"x", auth=False)[0] == 403
    finally:
        srv.stop()
