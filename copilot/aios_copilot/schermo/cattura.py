"""Lo schermo diventa video compresso.

La cattura e la codifica le fa wf-recorder (cattura Wayland dal compositore, «a danno»: un fotogramma
nuovo solo quando qualcosa cambia, quindi uno schermo fermo non manda quasi niente). Il codificatore è il
migliore che c'è, provato in quest'ordine:

    H.265 della scheda video (NVENC per NVIDIA, VA-API per Intel e AMD)
    H.264 della scheda video
    H.264 col processore (x264 «ultrafast + zerolatency», poi OpenH264)
    JPEG dei singoli fotogrammi con grim (solo se manca tutto il resto: funziona, ma è lento)

Senza B-frame (aggiungono ritardo) e con il bitrate della qualità scelta. Un fotogramma chiave si chiede
riavviando il codificatore (succede solo cambiando qualità o schermo).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

QUALITA = {  # bitrate, fotogrammi al secondo massimi (per la cattura JPEG)
    "alta": (20_000_000, 60),
    "media": (8_000_000, 60),
    "bassa": (3_000_000, 30),
}
RENDER = "/dev/dri/renderD128"
# codec → codificatori in ordine (nome, usa la scheda video)
ENCODERS: dict[str, list[tuple[str, bool]]] = {
    "hevc": [("hevc_nvenc", True), ("hevc_vaapi", True)],
    "h264": [("h264_nvenc", True), ("h264_vaapi", True), ("libx264", False), ("libopenh264", False)],
}
MUXER = {"hevc": "hevc", "h264": "h264"}
START_WAIT = 1.2  # un codificatore che non si apre esce subito


def run(cmd: list[str], timeout: float = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def ffmpeg_encoders(runner: Callable[[list[str]], tuple[int, str]] = run) -> set[str]:
    code, out = runner(["ffmpeg", "-hide_banner", "-encoders"])
    if code != 0:
        return set()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] == "V":
            names.add(parts[1])
    return names


def has_nvidia() -> bool:
    return os.path.exists("/dev/nvidia0") or os.path.exists("/proc/driver/nvidia/version")


def candidates(wanted: list[str], available: set[str], nvidia: bool | None = None,
               render: bool | None = None) -> list[tuple[str, str]]:
    """(codec, codificatore) da provare: prima quelli della scheda video nell'ordine dei codec che il
    visore sa leggere, poi quelli del processore, poi JPEG."""
    nvidia = has_nvidia() if nvidia is None else nvidia
    render = os.path.exists(RENDER) if render is None else render
    hw, sw = [], []
    for codec in wanted:
        for enc, gpu in ENCODERS.get(codec, []):
            if enc not in available:
                continue
            if enc.endswith("_nvenc") and not nvidia:
                continue
            if enc.endswith("_vaapi") and not render:
                continue
            (hw if gpu else sw).append((codec, enc))
    out = hw + sw
    if "jpeg" in wanted:
        out.append(("jpeg", "grim"))
    return out


def encoder_args(encoder: str, bitrate: int) -> list[str]:
    p = lambda k, v: ["-p", f"{k}={v}"]  # noqa: E731
    common = p("b", bitrate) + p("maxrate", bitrate) + p("bufsize", bitrate // 2) + p("bf", 0) + p("g", 600)
    if encoder.endswith("_nvenc"):
        return common + p("preset", "p1") + p("tune", "ull") + p("zerolatency", 1) + p("rc", "cbr") + p("delay", 0)
    if encoder.endswith("_vaapi"):
        return ["-d", RENDER] + common + p("rc_mode", "CBR")
    if encoder == "libx264":
        return ["-x", "yuv420p"] + common + p("preset", "ultrafast") + p("tune", "zerolatency")
    if encoder == "libopenh264":
        return ["-x", "yuv420p"] + common + p("allow_skip_frames", 1)
    return common


def wf_command(codec: str, encoder: str, monitor: str, quality: str) -> list[str]:
    bitrate, _ = QUALITA.get(quality, QUALITA["alta"])
    cmd = ["wf-recorder", "-y", "-c", encoder, "-m", MUXER[codec], "-f", "pipe:1"]
    if monitor:
        cmd += ["-o", monitor]
    return cmd + encoder_args(encoder, bitrate)


# --- schermi --------------------------------------------------------------------------------------
@dataclass
class Monitor:
    name: str
    width: int    # pixel veri
    height: int
    x: int        # posizione nella disposizione (pixel logici)
    y: int
    scale: float
    focused: bool = False

    @property
    def logical(self) -> tuple[float, float]:
        return self.width / (self.scale or 1), self.height / (self.scale or 1)

    def to_dict(self) -> dict[str, Any]:
        return {"nome": self.name, "larghezza": self.width, "altezza": self.height}


def parse_hyprland_monitors(text: str) -> list[Monitor]:
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            if r.get("disabled"):
                continue
            out.append(Monitor(str(r["name"]), int(r["width"]), int(r["height"]), int(r.get("x", 0)), int(r.get("y", 0)),
                               float(r.get("scale", 1) or 1), bool(r.get("focused"))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def monitors(runner: Callable[[list[str]], tuple[int, str]] = run) -> list[Monitor]:
    code, out = runner(["hyprctl", "-j", "monitors"])
    found = parse_hyprland_monitors(out) if code == 0 else []
    if found:
        return found
    code, out = runner(["wlr-randr", "--json"])  # labwc e gli altri compositori wlroots
    if code == 0:
        try:
            rows = json.loads(out)
            for r in rows:
                if not r.get("enabled", True):
                    continue
                mode = next((m for m in r.get("modes", []) if m.get("current")), {})
                pos = r.get("position", {})
                found.append(Monitor(r["name"], int(mode.get("width", 0)), int(mode.get("height", 0)),
                                     int(pos.get("x", 0)), int(pos.get("y", 0)), float(r.get("scale", 1) or 1)))
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    return found


def pick_monitor(mons: list[Monitor], wanted: str | int | None) -> Monitor | None:
    if not mons:
        return None
    if isinstance(wanted, int) and 0 <= wanted < len(mons):
        return mons[wanted]
    if isinstance(wanted, str) and wanted:
        hit = next((m for m in mons if m.name == wanted), None)
        if hit:
            return hit
    return next((m for m in mons if m.focused), mons[0])


def thumbnail(monitor: Monitor | None, width: int = 480, quality: int = 60,
              runner: Callable[..., Any] = subprocess.run) -> bytes:
    """Un'anteprima JPEG dello schermo, larga circa `width` pixel."""
    cmd = ["grim", "-t", "jpeg", "-q", str(quality)]
    if monitor is not None:
        lw = monitor.logical[0] or 1
        cmd += ["-o", monitor.name, "-s", f"{min(1.0, width / lw):.3f}"]
    cmd.append("-")
    try:
        p = runner(cmd, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return b""
    return p.stdout if p.returncode == 0 else b""


# --- il flusso ------------------------------------------------------------------------------------
class Capture:
    """Avvia il codificatore migliore che funziona e ne legge il flusso."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen, which: Callable[[str], Any] = shutil.which,
                 encoders: Callable[[], set[str]] = ffmpeg_encoders, wait: float = START_WAIT):
        self.popen, self.which, self.encoders, self.wait = popen, which, encoders, wait
        self.proc: Any = None
        self.codec = self.encoder = ""
        self.monitor: Monitor | None = None
        self.quality = "alta"
        self._stop = threading.Event()

    def start(self, wanted: list[str], monitor: Monitor | None, quality: str = "alta") -> tuple[str, str]:
        """→ (codec, codificatore) partiti; ("", "") se non parte niente."""
        self.stop()
        self._stop.clear()
        self.monitor, self.quality = monitor, quality if quality in QUALITA else "alta"
        available = self.encoders() if self.which("wf-recorder") else set()
        for codec, enc in candidates(wanted, available):
            if codec == "jpeg":
                if self.which("grim"):
                    self.codec, self.encoder = codec, enc
                    return codec, enc
                continue
            cmd = wf_command(codec, enc, monitor.name if monitor else "", self.quality)
            try:
                proc = self.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, bufsize=0)
            except OSError:
                continue
            deadline = time.monotonic() + self.wait
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.05)
            if proc.poll() is None:
                self.proc, self.codec, self.encoder = proc, codec, enc
                return codec, enc
        self.codec = self.encoder = ""
        return "", ""

    def chunks(self, size: int = 65536) -> Iterator[bytes]:
        """I pezzi del flusso, come arrivano (per JPEG: un'immagine intera alla volta)."""
        if self.codec == "jpeg":
            _, fps = QUALITA[self.quality]
            every = 1 / min(fps, 15)
            while not self._stop.is_set():
                t = time.monotonic()
                img = thumbnail(self.monitor, width=int(self.monitor.logical[0]) if self.monitor else 1920, quality=70)
                if img:
                    yield img
                self._stop.wait(max(0.0, every - (time.monotonic() - t)))
            return
        proc = self.proc
        while proc is not None and not self._stop.is_set():
            data = proc.stdout.read(size) if not hasattr(proc.stdout, "read1") else proc.stdout.read1(size)
            if not data:
                break
            yield data

    def stop(self) -> None:
        self._stop.set()
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()


# --- il suono -------------------------------------------------------------------------------------
AUDIO_BITRATE = 128_000


def audio_command(bitrate: int = AUDIO_BITRATE) -> list[str]:
    """Quello che il PC sta suonando (il «monitor» dell'uscita predefinita di PipeWire), in Opus a bassa
    latenza: pacchetti da 10 ms, pagine Ogg mandate subito."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "pulse", "-fragment_size", "1920",
            "-i", "@DEFAULT_MONITOR@", "-ac", "2", "-ar", "48000", "-c:a", "libopus", "-b:a", str(bitrate),
            "-application", "lowdelay", "-frame_duration", "10", "-f", "ogg", "-page_duration", "10000",
            "-flush_packets", "1", "pipe:1"]


class AudioCapture:
    def __init__(self, popen: Callable[..., Any] = subprocess.Popen, which: Callable[[str], Any] = shutil.which):
        self.popen, self.which = popen, which
        self.proc: Any = None

    def start(self) -> bool:
        self.stop()
        if not self.which("ffmpeg"):
            return False
        try:
            self.proc = self.popen(audio_command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, bufsize=0)
        except OSError:
            self.proc = None
        return self.proc is not None

    def chunks(self, size: int = 8192) -> Iterator[bytes]:
        proc = self.proc
        while proc is not None and proc.stdout is not None:
            data = proc.stdout.read(size)
            if not data:
                break
            yield data

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
