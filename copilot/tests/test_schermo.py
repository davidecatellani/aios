import os
import socket
import struct
import threading

import pytest
from pathlib import Path

from aios_copilot import ed25519
from aios_copilot import identity as ident
from aios_copilot.schermo import cattura, ingresso, scoperta, servizio, visore
from aios_copilot.schermo import protocollo as P


def user(name="Davide"):
    master = os.urandom(32)

    def device(dev, revoked=()):
        seed = os.urandom(32)
        cert = ident.issue(master, ed25519.public_key(seed), dev, "pc")
        data = {"utente": ident.b64(ed25519.public_key(master)), "nome": name, "certificato": cert.to_dict()}
        if revoked:
            data["revoche"] = ident.revocation_list(master, list(revoked), 1)
        return ident.Identity(data, device_seed=seed, sync_key=b"k" * 32)

    return device


def handshake(server_id, client_id):
    a, b = socket.socketpair()
    out = {}

    def srv():
        try:
            out["s"] = P.accept(a, server_id)
        except Exception as exc:  # noqa: BLE001
            out["s"] = exc

    t = threading.Thread(target=srv)
    t.start()
    try:
        out["c"] = P.connect(b, client_id)
    except Exception as exc:  # noqa: BLE001
        out["c"] = exc
        b.close()
    t.join(5)
    return out["s"], out["c"]


def test_devices_of_the_same_user_connect_and_talk_encrypted():
    dev = user()
    pc, laptop = dev("PC da gaming"), dev("portatile")
    s, c = handshake(pc, laptop)
    assert s.peer.name == "portatile" and c.peer.name == "PC da gaming"
    c.send_json(P.CONTROL, {"tipo": "ping", "t": 1})
    assert P.parse_json(s.recv()[1]) == {"tipo": "ping", "t": 1}
    big = b"x" * 5000  # comandi grandi: compressi
    s.send(P.CLIPBOARD, big)
    assert c.recv() == (P.CLIPBOARD, big) and c.recv_bytes < 200
    s.send(P.VIDEO, b"\x00\x00\x00\x01video")
    assert c.recv() == (P.VIDEO, b"\x00\x00\x00\x01video")


def test_strangers_and_revoked_devices_are_refused():
    mine, other = user(), user("Estraneo")
    s, c = handshake(mine("PC"), other("intruso"))
    assert isinstance(s, Exception) or isinstance(c, Exception)
    stolen = mine("telefono rubato")
    pc = mine("PC", revoked=[stolen.certificate.id])
    s, c = handshake(pc, stolen)
    assert isinstance(s, P.HandshakeError)


def test_tampered_frames_are_rejected():
    dev = user()
    a, b = socket.socketpair()
    ch_a = P.Channel(a, b"1" * 32, b"2" * 32, dev("x").certificate)
    ch_b = P.Channel(b, b"2" * 32, b"1" * 32, dev("y").certificate)
    ch_a.send(P.VIDEO, b"ciao")
    size = struct.unpack(">I", b.recv(4))[0]
    frame = bytearray(b.recv(size))
    frame[3] ^= 1
    a2, b2 = socket.socketpair()
    a2.sendall(struct.pack(">I", size) + bytes(frame))
    with pytest.raises(P.DecryptError):
        P.Channel(b2, b"2" * 32, b"1" * 32, ch_b.peer).recv()


def test_input_events_round_trip():
    events = [(P.EV_MOVE, 100, 65535), (P.EV_BUTTON, 1, 1), (P.EV_WHEEL, 0, -240), (P.EV_KEY, 30, 1), (P.EV_RELEASE_ALL,)]
    assert P.unpack_events(P.pack_events(events)) == events
    assert P.unpack_events(b"\x63garbage") == []


def test_discovery_only_understands_beacons_of_the_same_user():
    key = scoperta.beacon_key(b"s" * 32)
    me = scoperta.Discovery(key, {"id": "aaa", "nome": "portatile"}, save=False)
    data = scoperta.make_beacon(key, {"id": "bbb", "nome": "PC da gaming", "tipo": "pc", "porta": 7340}, now=1000)
    assert b"gaming" not in data  # gli estranei non leggono niente
    peer = me.handle(data, ("192.168.1.20", 7341), now=1001)
    assert peer.nome == "PC da gaming" and peer.indirizzo == "192.168.1.20"
    assert me.handle(scoperta.make_beacon(scoperta.beacon_key(b"z" * 32), {"id": "ccc"}, now=1000), ("1.2.3.4", 1), now=1000) is None
    assert me.handle(data, ("192.168.1.20", 7341), now=5000) is None  # annuncio vecchio, ripetuto
    me.expire(now=1001 + scoperta.GONE_AFTER + 1)
    assert me.peers == {}


def test_find_peer_by_name():
    peers = [scoperta.Peer("1", "PC da gaming", "pc", "10.0.0.2", 7340, 0), scoperta.Peer("2", "Portatile", "pc", "10.0.0.3", 7340, 0)]
    assert scoperta.find_peer("il pc da gaming", peers).id == "1"
    assert scoperta.find_peer("portatile", peers).id == "2"
    assert scoperta.find_peer("10.0.0.3", peers).id == "2"
    assert scoperta.find_peer("l'altro pc", peers[:1]).id == "1"


def test_best_encoder_first():
    have = {"hevc_nvenc", "h264_nvenc", "h264_vaapi", "libx264", "libopenh264"}
    assert cattura.candidates(["hevc", "h264", "jpeg"], have, nvidia=True, render=True)[:2] == [("hevc", "hevc_nvenc"), ("h264", "h264_nvenc")]
    no_gpu = cattura.candidates(["hevc", "h264", "jpeg"], have, nvidia=False, render=False)
    assert no_gpu == [("h264", "libx264"), ("h264", "libopenh264"), ("jpeg", "grim")]
    cmd = cattura.wf_command("h264", "libx264", "DP-1", "media")
    assert cmd[:8] == ["wf-recorder", "-y", "-c", "libx264", "-m", "h264", "-f", "pipe:1"]
    assert "tune=zerolatency" in cmd and "b=8000000" in cmd and "bf=0" in cmd and "-o" in cmd
    out = "Encoders:\n V....D h264_nvenc  NVIDIA\n V....D libx264 x264\n A....D aac AAC\n"
    assert cattura.ffmpeg_encoders(lambda c: (0, out)) == {"h264_nvenc", "libx264"}


def test_capture_falls_back_when_an_encoder_does_not_start():
    started = []

    class Proc:
        def __init__(self, cmd, **kw):
            started.append(cmd[3])
            self.code = 1 if cmd[3] == "h264_vaapi" else None
            self.stdout = None

        def poll(self):
            return self.code

        def terminate(self):
            self.code = 0

        def wait(self, timeout=None):
            return 0

    cap = cattura.Capture(popen=Proc, which=lambda n: "/usr/bin/" + n, encoders=lambda: {"h264_vaapi", "libx264"}, wait=0.05)
    os.environ.pop("X", None)
    assert cap.start(["h264"], None) in (("h264", "libx264"),)
    assert started[-1] == "libx264"


def test_monitors_from_hyprland():
    text = '[{"name":"DP-1","width":2560,"height":1440,"x":0,"y":0,"scale":1.25,"focused":true},{"name":"HDMI-A-1","width":1920,"height":1080,"x":2048,"y":0,"scale":1}]'
    mons = cattura.parse_hyprland_monitors(text)
    assert [m.name for m in mons] == ["DP-1", "HDMI-A-1"] and mons[0].logical == (2048, 1152)
    assert cattura.pick_monitor(mons, None).name == "DP-1" and cattura.pick_monitor(mons, "HDMI-A-1").name == "HDMI-A-1"


class FakeDev:
    def __init__(self):
        self.events = []

    def emit(self, evs):
        self.events.append(evs)

    def close(self):
        pass


def test_input_lands_on_the_right_spot_and_keys_never_stay_stuck():
    mons = cattura.parse_hyprland_monitors('[{"name":"A","width":1920,"height":1080,"x":0,"y":0,"scale":1},{"name":"B","width":2560,"height":1440,"x":1920,"y":0,"scale":2}]')
    hypr, kb = [], FakeDev()
    inj = ingresso.Injector(mons[1], mons, keyboard=kb, hypr=hypr.append)
    inj.handle(P.pack_events([(P.EV_MOVE, 0, 0), (P.EV_MOVE, 65535, 65535), (P.EV_KEY, 29, 1), (P.EV_BUTTON, 1, 1)]))
    assert hypr == ["dispatch movecursor 1920 0", "dispatch movecursor 3199 719"]
    inj.handle(P.pack_events([(P.EV_WHEEL, 0, 60), (P.EV_WHEEL, 0, 60)]))
    assert kb.events[-1] == [(ingresso.EV_REL, ingresso.REL_WHEEL_HI_RES, -60), (ingresso.EV_REL, ingresso.REL_WHEEL, -1)]
    inj.close()
    assert kb.events[-1] == [(ingresso.EV_KEY, 29, 0), (ingresso.EV_KEY, ingresso.BTN_LEFT, 0)]


def test_uinput_device_setup_and_events():
    calls, written = [], []
    dev = ingresso.VirtualDevice("tastiera", ioctl=lambda fd, req, arg=0: calls.append(req), opener=lambda p, f: os.open(os.devnull, os.O_WRONLY),
                                 writer=lambda fd, data: written.append(data))
    assert calls[-2:] == [ingresso.UI_DEV_SETUP, ingresso.UI_DEV_CREATE]
    dev.emit([(ingresso.EV_KEY, 30, 1)])
    assert len(written[0]) == 2 * struct.calcsize("llHHi")
    assert len(ingresso.uinput_setup("x")) == 92 and len(ingresso.abs_setup(0, 65535)) == 28


def test_viewer_maps_the_letterboxed_video():
    rect = visore.fit(2000, 1000, 1920, 1080)  # bande ai lati
    assert rect[1] == 0 and round(rect[0]) == 111
    assert visore.to_norm(rect[0], 0, rect) == (0, 0)
    assert visore.to_norm(rect[0] + rect[2], 1000, rect) == (65535, 65535)
    assert visore.to_norm(-50, 2000, rect) == (0, 65535)
    assert visore.decoders(lambda n: n in {"h264parse", "avdec_h264"}) == ["h264", "jpeg"]
    assert "h265parse" in visore.pipeline_text("hevc")


class FakeCapture:
    def __init__(self):
        self.quality = "alta"
        self.stopped = 0
        self.wanted = None

    def start(self, wanted, monitor, quality="alta"):
        self.wanted, self.quality = wanted, quality
        return "hevc", "hevc_nvenc"

    def chunks(self):
        yield b"\x00\x00\x00\x01frame1"
        yield b"\x00\x00\x00\x01frame2"

    def stop(self):
        self.stopped += 1


def test_full_session_stream_thumbnail_and_input(monkeypatch):
    dev = user()
    pc, laptop = dev("PC da gaming"), dev("portatile")
    notes, kb = [], FakeDev()
    mons = cattura.parse_hyprland_monitors('[{"name":"DP-1","width":2560,"height":1440,"x":0,"y":0,"scale":1}]')

    def session(ch):
        return servizio.Session(ch, capture=FakeCapture(), list_monitors=lambda: mons,
                                make_injector=lambda m, layout: ingresso.Injector(m, layout, keyboard=kb, hypr=lambda c: None),
                                snap=lambda m, width=480: b"JPEG" + m.name.encode(), conf={"condividi": True, "comandi": True},
                                on_notify=notes.append)

    monkeypatch.setattr(servizio, "settings", lambda: {"condividi": True, "comandi": True})
    server = servizio.Server(pc, port=0, host="127.0.0.1", session=session)
    port = server.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    assert servizio.fetch_thumbnail(laptop, "127.0.0.1", port) == b"JPEGDP-1"

    ch = servizio.open_channel(laptop, "127.0.0.1", port)
    ch.send_json(P.CONTROL, {"tipo": "avvia", "codec": ["hevc", "h264", "jpeg"], "qualita": "media"})
    kind, cfg = ch.recv()
    cfg = P.parse_json(cfg)
    assert kind == P.CONFIG and cfg["codec"] == "hevc" and cfg["larghezza"] == 2560 and cfg["comandi"] is True
    assert ch.recv() == (P.VIDEO, b"\x00\x00\x00\x01frame1") and ch.recv() == (P.VIDEO, b"\x00\x00\x00\x01frame2")
    ch.send(P.INPUT, P.pack_events([(P.EV_KEY, 30, 1)]))
    ch.send_json(P.CONTROL, {"tipo": "ping", "t": 5})
    assert P.parse_json(ch.recv()[1]) == {"tipo": "pong", "t": 5}
    assert kb.events == [[(ingresso.EV_KEY, 30, 1)]]
    ch.close("fatto")
    for _ in range(50):
        if len(notes) == 2:
            break
        threading.Event().wait(0.05)
    assert notes[0] == "portatile sta guardando questo schermo." and "smesso" in notes[1]
    assert kb.events[-1] == [(ingresso.EV_KEY, 30, 0)]  # rilasciato alla chiusura
    server.close()


def test_sharing_off_closes_the_door(monkeypatch):
    dev = user()
    monkeypatch.setattr(servizio, "settings", lambda: {"condividi": False, "comandi": True})
    server = servizio.Server(dev("PC"), port=0, host="127.0.0.1")
    port = server.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    assert servizio.fetch_thumbnail(dev("portatile"), "127.0.0.1", port) == b""
    server.close()


# --- suono, appunti e file -------------------------------------------------------------------------
from aios_copilot.schermo import appunti as A  # noqa: E402


class FakeClipboard:
    def __init__(self, content=None):
        self.content, self.writes = content, []
        self.on_change = None

    def read(self):
        return self.content

    def write(self, mime, data):
        self.writes.append((mime, data))
        self.content = (mime, data)
        if self.on_change:
            self.on_change()  # come wl-paste --watch: anche le copie fatte da noi

    def watch(self, cb):
        self.on_change = cb

    def stop(self):
        self.on_change = None


def test_clipboard_text_goes_across_and_does_not_bounce_back():
    sent = []
    clip = FakeClipboard(("text/plain;charset=utf-8", b"ciao dal portatile"))
    sync = A.ClipboardSync(clip, sent.append)
    sync.start()
    sync.local_changed()
    assert A.unpack_clip(sent[0]) == ("text/plain;charset=utf-8", b"ciao dal portatile")
    sync.remote(A.pack_clip(A.PNG, b"\x89PNG..."))
    assert clip.writes == [(A.PNG, b"\x89PNG...")] and len(sent) == 1  # non torna indietro


def test_copied_files_are_offered_not_pushed(tmp_path):
    f = tmp_path / "foto.jpg"
    f.write_bytes(b"x" * 10)
    offered = []
    sync = A.ClipboardSync(FakeClipboard((A.URIS, A.make_uris([f]))), lambda p: None, on_local_files=offered.append)
    sync.local_changed()
    assert offered == [[f]]
    offers = A.Offers(clock=lambda: 100)
    info = offers.add([tmp_path])
    assert info["numero"] == 1 and info["dimensione"] == 10 and offers.take(info["gettone"]) == [tmp_path]
    offers.clock = lambda: 100 + A.OFFER_TTL
    assert offers.take(info["gettone"]) is None and offers.take("inventato") is None


def test_unsafe_file_names_are_refused():
    assert A.safe_name("../../.bashrc") is None and A.safe_name("/etc/passwd") == Path("etc/passwd")
    assert A.safe_name("cartella/sotto/file.txt") == Path("cartella/sotto/file.txt")
    assert A.safe_name("a\x07b") is None


def test_audio_is_opus_from_what_the_pc_plays():
    cmd = cattura.audio_command()
    assert "@DEFAULT_MONITOR@" in cmd and "libopus" in cmd and cmd[-1] == "pipe:1" and "lowdelay" in cmd


class FakeAudio:
    def start(self):
        return True

    def chunks(self):
        yield b"OggS-audio"

    def stop(self):
        pass


def _server(identity, tmp_path, monkeypatch, clipboard=None, kb=None):
    monkeypatch.setattr(servizio, "settings", lambda: {"condividi": True, "comandi": True})
    mons = cattura.parse_hyprland_monitors('[{"name":"DP-1","width":1920,"height":1080,"x":0,"y":0,"scale":1}]')
    notes = []
    server = servizio.Server(identity, port=0, host="127.0.0.1")
    server.session = lambda ch: servizio.Session(
        ch, capture=FakeCapture(), list_monitors=lambda: mons, audio=FakeAudio(), clipboard=clipboard,
        make_injector=lambda m, layout: ingresso.Injector(m, layout, keyboard=kb or FakeDev(), hypr=lambda c: None),
        conf={"condividi": True, "comandi": True}, on_notify=notes.append, shared=server.shared,
        folders=lambda scope: tmp_path / ("appunti" if scope == "appunti" else "Scaricati"))
    port = server.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port, notes


def test_files_travel_between_pcs(tmp_path, monkeypatch):
    dev = user()
    pc, laptop = dev("PC da gaming"), dev("portatile")
    server, port, notes = _server(pc, tmp_path, monkeypatch)
    src = tmp_path / "da mandare"
    (src / "sotto").mkdir(parents=True)
    (src / "sotto" / "b.bin").write_bytes(os.urandom(600_000))
    (src / "a.txt").write_text("ciao")
    names = servizio.send_files_to(laptop, "127.0.0.1", port, [src, src / "a.txt"])
    got = tmp_path / "Scaricati"
    assert sorted(names) == ["a.txt", "da mandare"]
    assert (got / "da mandare" / "sotto" / "b.bin").read_bytes() == (src / "sotto" / "b.bin").read_bytes()
    assert (got / "a.txt").read_text() == "ciao" and "Scaricati" in notes[-1]
    servizio.send_files_to(laptop, "127.0.0.1", port, [src / "a.txt"])
    assert (got / "a (2).txt").exists()  # non sovrascrive
    # copiato sul PC → incollato sul portatile
    info = server.shared.offers.add([src / "a.txt"])
    pasted = servizio.fetch_offer(laptop, "127.0.0.1", port, info, tmp_path / "incolla")
    assert [p.read_text() for p in pasted] == ["ciao"]
    assert servizio.fetch_offer(laptop, "127.0.0.1", port, {"gettone": "rubato"}, tmp_path / "x") == []
    server.close()


def test_live_session_carries_sound_and_clipboard(tmp_path, monkeypatch):
    dev = user()
    pc, laptop = dev("PC da gaming"), dev("portatile")
    clip = FakeClipboard()
    server, port, _ = _server(pc, tmp_path, monkeypatch, clipboard=clip)
    ch = servizio.open_channel(laptop, "127.0.0.1", port)
    ch.send_json(P.CONTROL, {"tipo": "avvia", "codec": ["h264"], "audio": True, "appunti": True})
    seen = {}
    while len(seen) < 3:
        kind, payload = ch.recv()
        seen.setdefault(kind, payload)
    assert P.parse_json(seen[P.CONFIG])["audio"] is True and P.parse_json(seen[P.CONFIG])["appunti"] is True
    assert seen[P.AUDIO] == b"OggS-audio"
    ch.send(P.CLIPBOARD, A.pack_clip(A.TEXT, b"incollami"))
    clip_writes = []
    for _ in range(40):
        if clip.writes:
            break
        threading.Event().wait(0.05)
    assert clip.writes == [(A.TEXT, b"incollami")]
    clip.content = (A.TEXT, b"copiato sul PC")
    clip.on_change()
    while True:
        kind, payload = ch.recv()
        if kind == P.CLIPBOARD:
            break
    assert A.unpack_clip(payload) == (A.TEXT, b"copiato sul PC")
    ch.close()
    server.close()


def test_no_room_no_transfer(tmp_path):
    a, b = socket.socketpair()
    dev = user()
    ca = P.Channel(a, b"1" * 32, b"2" * 32, dev("x").certificate)
    cb = P.Channel(b, b"2" * 32, b"1" * 32, dev("y").certificate)
    f = tmp_path / "grande.bin"
    f.write_bytes(b"x" * 100)
    t = threading.Thread(target=lambda: A.receive_files(cb, tmp_path / "dest", free=lambda d: 10))
    t.start()
    with pytest.raises(OSError, match="spazio"):
        A.send_files(ca, [f])
    t.join(5)


def test_nova_understands_remote_screen_requests():
    from aios_copilot.tools.schermo import ScreensRouter

    r = ScreensRouter()
    assert r.match("fammi vedere il PC da gaming").args == {"chi": "pc da gaming"}
    assert r.match("mostrami il desktop dell'altro pc").args == {"chi": "altro pc"}
    assert r.match("quali pc sono accesi?").tool == "list_my_pcs"
    assert r.match("apri spotify") is None and r.match("apri il desktop") is None


def test_actions_open_viewer_and_send(tmp_path, monkeypatch):
    from aios_copilot.schermo import azioni

    peer = scoperta.Peer("abcdef0123456789", "PC da gaming", "pc", "10.0.0.2", 7340, 0)
    monkeypatch.setattr(azioni, "peers", lambda: [peer])
    monkeypatch.setattr(azioni, "identity", lambda: "io")
    launched = []
    ok, msg = azioni.open_viewer("pc da gaming", popen=lambda cmd, **kw: launched.append(cmd))
    assert ok and launched[0][1:] == ["guarda", "abcdef0123456789"]
    f = tmp_path / "foto.jpg"
    f.write_bytes(b"x")
    sent = []
    ok, msg = azioni.send("gaming", [f], sender=lambda me, h, p, paths: sent.append((h, paths)) or ["foto.jpg"], wait=True)
    assert ok and sent == [("10.0.0.2", [f])] and "Scaricati" in msg
    monkeypatch.setattr(azioni, "peers", lambda: [])
    assert "Non vedo" in azioni.open_viewer("pc da gaming")[1]
