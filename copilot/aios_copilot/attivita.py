"""Gestione attività: chi usa il processore e la memoria, chiudere a forza un programma bloccato, e lo stato
della macchina (temperature, ventole, scheda video, rete) con un occhio di riguardo per l'AI: quali modelli
sono caricati, quanto stanno nella scheda video e quanto nella memoria normale, quanto pesa Nova.

Tutto da /proc e /sys (nessun privilegio), più nvidia-smi per le schede NVIDIA e l'API di Ollama per i
modelli. I processi si raggruppano per programma, come in Windows: le venti schede di Firefox sono «Firefox».
Si possono chiudere solo i programmi dell'utente, mai quelli che tengono in piedi la sessione.
`root` permette di provare tutto su un albero di file finto.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA
TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
MB = 1024 * 1024

# nomi dei processi → il programma che l'utente conosce
FRIENDLY = {
    "firefox": "Firefox", "firefox-bin": "Firefox", "isolated web co": "Firefox", "web content": "Firefox",
    "webextensions": "Firefox", "rdd process": "Firefox", "socket process": "Firefox", "privileged cont": "Firefox",
    "utility process": "Firefox", "forkserver": "Firefox", "chrome": "Chrome", "chromium": "Chromium",
    "chromium-browser": "Chromium", "code": "Visual Studio Code", "steam": "Steam", "steamwebhelper": "Steam",
    "ollama": "Ollama (modelli AI)", "hyprland": "Hyprland (grafica)", "pipewire": "Audio (PipeWire)",
    "wireplumber": "Audio (PipeWire)", "pipewire-pulse": "Audio (PipeWire)", "wf-recorder": "Schermo AIOS",
    "webkitwebprocess": "AIOS (pagine)", "webkitnetworkpro": "AIOS (pagine)", "webkitgpuprocess": "AIOS (pagine)",
    "mako": "Notifiche", "swayidle": "Blocco schermo", "networkmanager": "Rete", "wine64-preloader": "App Windows",
    "wineserver": "App Windows", "discord": "Discord", "spotify": "Spotify", "thunderbird": "Thunderbird",
    "telegram-desktop": "Telegram", "obs": "OBS Studio", "gimp": "GIMP", "blender": "Blender",
    "libreoffice": "LibreOffice", "soffice.bin": "LibreOffice", "vlc": "VLC", "mpv": "mpv",
}
# parti di AIOS (riconosciute dalla riga di comando): l'AI e la shell
AIOS_PARTS = (("aios_copilot.shell", "AIOS (schermata e Nova)"), ("aios-shell", "AIOS (schermata e Nova)"),
              ("aios_copilot.schermo", "Schermo AIOS"), ("aios-schermo", "Schermo AIOS"),
              ("aios_copilot.decisore", "Laya (decisore)"), ("aios-decisore", "Laya (decisore)"),
              ("aios_copilot.nucleo", "AIOS (servizi)"), ("aios-nucleo", "AIOS (servizi)"),
              ("aios_copilot", "Nova"), ("aios-copilot", "Nova"))
AI_GROUPS = {"Ollama (modelli AI)", "Nova", "Laya (decisore)", "AIOS (schermata e Nova)"}
# senza questi la sessione cade: mai chiusi da qui
PROTECTED = re.compile(r"^(hyprland|aios|systemd|dbus|pipewire|wireplumber|greetd|gtklock|xdg-desktop-portal|"
                       r"polkit|webkit|mako|swayidle|labwc|login|sd-pam|bash|sh|ollama)", re.I)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _num(path: Path) -> float | None:
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _run(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _http(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read() or b"{}")


@dataclass
class Proc:
    pid: int
    name: str  # il nome del processo (comm)
    group: str  # il programma come lo conosce l'utente
    uid: int
    ticks: int  # tempo di processore usato finora
    rss: int  # memoria in byte
    cmd: str = ""


def friendly(comm: str, cmd: str) -> str:
    for needle, label in AIOS_PARTS:
        if needle in cmd:
            return label
    low = comm.lower()
    if low in FRIENDLY:
        return FRIENDLY[low]
    exe = os.path.basename(cmd.split("\0", 1)[0].split(" ", 1)[0]).lower() if cmd else ""
    if exe in FRIENDLY:
        return FRIENDLY[exe]
    if low.startswith(("python", "node", "java", "bwrap")) and exe and exe != low:
        return exe.capitalize()
    if low.startswith("kworker") or low.startswith("ksoftirq") or low.startswith("rcu_"):
        return "Kernel"
    return comm[:1].upper() + comm[1:]


def read_procs(root: Path = Path("/")) -> list[Proc]:
    out = []
    for d in (root / "proc").iterdir() if (root / "proc").is_dir() else []:
        if not d.name.isdigit():
            continue
        stat = _read(d / "stat")
        m = re.match(r"\d+ \((.*)\) (\S) (.*)", stat, re.S)
        if not m:
            continue
        f = m.group(3).split()
        try:
            ticks = int(f[10]) + int(f[11])  # utime + stime (campi 14 e 15)
            rss = int(f[20]) * PAGE  # campo 24
            ppid = int(f[0])
        except (IndexError, ValueError):
            continue
        cmd = _read(d / "cmdline").replace("\0", " ").strip()
        uid = 0
        status = _read(d / "status")
        um = re.search(r"^Uid:\s+(\d+)", status, re.M)
        if um:
            uid = int(um.group(1))
        comm = m.group(1)
        kernel = not cmd and (ppid == 2 or d.name == "2")  # i thread del kernel non hanno riga di comando
        out.append(Proc(int(d.name), comm, "Kernel" if kernel else friendly(comm, cmd), uid, ticks, rss, cmd[:300]))
    return out


def cpu_times(root: Path = Path("/")) -> list[tuple[int, int]]:
    """(occupato, totale) per il processore intero e per ogni core."""
    out = []
    for line in _read(root / "proc/stat").splitlines():
        if not line.startswith("cpu"):
            break
        v = [int(x) for x in line.split()[1:]]
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        total = sum(v[:8])
        out.append((total - idle, total))
    return out


def meminfo(root: Path = Path("/")) -> dict[str, int]:
    info = {}
    for line in _read(root / "proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        try:
            info[k] = int(v.split()[0]) * 1024
        except (IndexError, ValueError):
            pass
    return info


def net_bytes(root: Path = Path("/")) -> tuple[int, int]:
    rx = tx = 0
    for line in _read(root / "proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        if name.strip() == "lo" or not rest:
            continue
        v = rest.split()
        rx, tx = rx + int(v[0]), tx + int(v[8])
    return rx, tx


# --- sensori ------------------------------------------------------------------------------------------
CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal")
CPU_LABELS = ("package id 0", "tctl", "tdie", "cpu")


@dataclass
class Sensors:
    cpu: float | None = None  # gradi
    gpu: float | None = None
    disco: float | None = None
    scheda_madre: float | None = None
    ventole: list[dict[str, Any]] = field(default_factory=list)  # {nome, giri}
    altre: list[dict[str, Any]] = field(default_factory=list)


def read_sensors(root: Path = Path("/")) -> Sensors:
    s = Sensors()
    for hw in sorted((root / "sys/class/hwmon").glob("hwmon*")):
        chip = _read(hw / "name").strip().lower()
        temps = []
        for t in sorted(hw.glob("temp*_input")):
            v = _num(t)
            if v is None or v <= 0:
                continue
            label = _read(hw / t.name.replace("_input", "_label")).strip()
            temps.append((label.lower(), v / 1000))
        if temps:
            best = max(temps)[1] if chip in CPU_CHIPS else temps[0][1]
            if chip in CPU_CHIPS:
                pkg = next((v for lab, v in temps if lab in CPU_LABELS or lab.startswith("package")), None)
                s.cpu = round(pkg if pkg is not None else max(v for _, v in temps), 1)
            elif chip in ("amdgpu", "nouveau", "radeon", "i915", "xe"):
                s.gpu = round(best, 1) if s.gpu is None else s.gpu
            elif chip in ("nvme", "drivetemp"):
                s.disco = max(s.disco or 0, round(best, 1))
            elif chip.startswith(("nct", "it8", "asus", "acpitz", "f71")):
                if chip == "acpitz" and s.cpu is None:
                    s.cpu = round(best, 1)
                else:
                    s.scheda_madre = round(best, 1)
            else:
                s.altre.append({"nome": chip, "gradi": round(best, 1)})
        for fan in sorted(hw.glob("fan*_input")):
            v = _num(fan)
            if v is None:
                continue
            label = _read(hw / fan.name.replace("_input", "_label")).strip()
            n = re.sub(r"\D", "", fan.name)
            name = label or {"1": "Ventola del processore", "2": "Ventola del case"}.get(n, f"Ventola {n}")
            if v > 0 or label:
                s.ventole.append({"nome": name, "giri": int(v)})
    if s.cpu is None:  # niente hwmon del processore: le zone termiche
        for z in sorted((root / "sys/class/thermal").glob("thermal_zone*")):
            if _read(z / "type").strip() in ("x86_pkg_temp", "cpu-thermal", "acpitz"):
                v = _num(z / "temp")
                if v:
                    s.cpu = round(v / 1000, 1)
                    break
    return s


@dataclass
class Gpu:
    nome: str
    uso: int | None = None  # %
    gradi: float | None = None
    memoria_usata: int | None = None  # MB
    memoria_totale: int | None = None
    ventola: int | None = None  # %
    watt: float | None = None
    driver: bool = True


def read_gpus(root: Path = Path("/"), run: Callable[[list[str]], str] = _run) -> list[Gpu]:
    gpus = []
    out = run(["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,fan.speed,power.draw",
               "--format=csv,noheader,nounits"])

    def val(x: str) -> float | None:
        try:
            return float(x.strip())
        except ValueError:
            return None

    for line in out.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) >= 7:
            v = [val(x) for x in f[1:]]
            gpus.append(Gpu(f[0], int(v[0]) if v[0] is not None else None, v[1],
                            int(v[2]) if v[2] is not None else None, int(v[3]) if v[3] is not None else None,
                            int(v[4]) if v[4] is not None else None, v[5]))
    for card in sorted((root / "sys/class/drm").glob("card[0-9]")):
        vendor = _read(card / "device/vendor").strip()
        dev = card / "device"
        if vendor == "0x10de" and not gpus:
            gpus.append(Gpu("NVIDIA", driver=(root / "proc/driver/nvidia/version").exists()))
        elif vendor == "0x1002":
            g = Gpu("AMD Radeon")
            busy = _num(dev / "gpu_busy_percent")
            g.uso = int(busy) if busy is not None else None
            used, total = _num(dev / "mem_info_vram_used"), _num(dev / "mem_info_vram_total")
            if used is not None and total:
                g.memoria_usata, g.memoria_totale = int(used / MB), int(total / MB)
            for hw in dev.glob("hwmon/hwmon*"):
                t = _num(hw / "temp1_input")
                g.gradi = round(t / 1000, 1) if t else None
                p = _num(hw / "power1_average")
                g.watt = round(p / 1e6, 1) if p else None
            gpus.append(g)
        elif vendor == "0x8086" and not any(x.nome.startswith("Intel") for x in gpus):
            gpus.append(Gpu("Intel (integrata)"))
    return gpus


# --- i modelli AI caricati ---------------------------------------------------------------------------------
def loaded_models(http: Callable[..., dict[str, Any]] = _http) -> list[dict[str, Any]] | None:
    """I modelli in memoria (Ollama /api/ps), None se Ollama non risponde."""
    try:
        data = http(f"{OLLAMA}/api/ps")
    except Exception:
        return None
    out = []
    for m in data.get("models") or []:
        size, vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
        out.append({"nome": m.get("name") or m.get("model", "?"), "memoria_mb": size // MB, "in_gpu_mb": vram // MB,
                    "in_gpu": round(100 * vram / size) if size else 0, "scade": m.get("expires_at", "")})
    return out


def unload_model(name: str, http: Callable[..., dict[str, Any]] = _http) -> bool:
    try:
        http(f"{OLLAMA}/api/generate", {"model": name, "keep_alive": 0})
        return True
    except Exception:
        return False


# --- il quadro complessivo ------------------------------------------------------------------------------------
class TaskManager:
    """Fotografie successive del sistema: le percentuali vengono dalla differenza con quella prima."""

    def __init__(self, root: Path = Path("/"), run: Callable[[list[str]], str] = _run,
                 http: Callable[..., dict[str, Any]] = _http, clock: Callable[[], float] = time.monotonic,
                 me: int | None = None, kill: Callable[[int, int], None] = os.kill):
        self.root, self.run, self.http, self.clock, self.kill = root, run, http, clock, kill
        self.me = os.getuid() if me is None else me
        self.prev: dict[str, Any] | None = None
        self.lock = threading.Lock()

    def _sample(self) -> dict[str, Any]:
        return {"t": self.clock(), "procs": {p.pid: p for p in read_procs(self.root)}, "cpu": cpu_times(self.root),
                "net": net_bytes(self.root)}

    def snapshot(self, wait: float = 0.4) -> dict[str, Any]:
        with self.lock:
            if self.prev is None or self.clock() - self.prev["t"] > 30:
                self.prev = self._sample()
                time.sleep(wait)
            now = self._sample()
            prev, self.prev = self.prev, now
        dt = max(0.05, now["t"] - prev["t"])
        cores = max(1, len(now["cpu"]) - 1)
        mem = meminfo(self.root)
        total_mem = mem.get("MemTotal", 1)

        groups: dict[str, dict[str, Any]] = {}
        for pid, p in now["procs"].items():
            old = prev["procs"].get(pid)
            used = (p.ticks - old.ticks) if old and old.name == p.name else 0
            cpu = max(0.0, used / TICK / dt * 100 / cores)  # % del processore intero, come in Windows
            g = groups.setdefault(p.group, {"nome": p.group, "cpu": 0.0, "memoria_mb": 0, "processi": 0, "pid": [],
                                            "mio": False, "ai": p.group in AI_GROUPS, "protetto": False})
            g["cpu"] += cpu
            g["memoria_mb"] += p.rss // MB
            g["processi"] += 1
            g["pid"].append(pid)
            g["mio"] = g["mio"] or p.uid == self.me
            g["protetto"] = g["protetto"] or bool(PROTECTED.match(p.name)) or p.group.startswith("AIOS") or p.uid != self.me
        rows = [g for g in groups.values() if g["nome"] != "Kernel" or g["cpu"] >= 1]
        for g in rows:
            g["cpu"] = round(g["cpu"], 1)
            g["chiudibile"] = g["mio"] and not g["protetto"]
            g["pid"] = sorted(g["pid"])[:200]
        rows.sort(key=lambda g: (g["cpu"] + g["memoria_mb"] / 400), reverse=True)

        def pct(i: int) -> float:
            (b0, t0), (b1, t1) = prev["cpu"][i], now["cpu"][i]
            return round(100 * (b1 - b0) / max(1, t1 - t0), 1)

        cpu_all = pct(0) if len(now["cpu"]) == len(prev["cpu"]) and now["cpu"] else 0.0
        per_core = [pct(i) for i in range(1, len(now["cpu"]))] if len(now["cpu"]) == len(prev["cpu"]) else []
        rx = max(0, now["net"][0] - prev["net"][0]) / dt
        tx = max(0, now["net"][1] - prev["net"][1]) / dt
        sensors = read_sensors(self.root)
        gpus = read_gpus(self.root, self.run)
        if sensors.gpu is not None and gpus and gpus[0].gradi is None:
            gpus[0].gradi = sensors.gpu
        models = loaded_models(self.http)
        avail = mem.get("MemAvailable", 0)
        state = {
            "processore": {"uso": cpu_all, "core": per_core, "gradi": sensors.cpu,
                           "carico": _read(self.root / "proc/loadavg").split()[:3]},
            "memoria": {"usata_mb": (total_mem - avail) // MB, "totale_mb": total_mem // MB,
                        "scambio_mb": (mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) // MB},
            "schede_video": [asdict(g) for g in gpus],
            "sensori": asdict(sensors),
            "rete": {"giu_kbs": round(rx / 1024), "su_kbs": round(tx / 1024)},
            "acceso_da": _uptime(self.root),
            "programmi": rows[:80],
            "ai": {"modelli": models, "ollama": models is not None,
                   "nova_mb": sum(g["memoria_mb"] for g in rows if g["ai"]),
                   "nova_cpu": round(sum(g["cpu"] for g in rows if g["ai"]), 1)},
        }
        state["consigli"] = advice(state)
        return state

    def end(self, name: str, pids: list[int] | None = None, wait: float = 3.0) -> tuple[bool, str]:
        """Chiude a forza un programma (tutti i suoi processi): prima gentilmente, poi d'autorità."""
        procs = [p for p in read_procs(self.root) if p.group.lower() == name.lower() or (pids and p.pid in pids)]
        if not procs:
            low = name.lower()
            procs = [p for p in read_procs(self.root) if low in p.group.lower() or low == p.name.lower()]
        mine = [p for p in procs if p.uid == self.me and not PROTECTED.match(p.name) and not p.group.startswith("AIOS")
                and p.pid != os.getpid()]
        if not procs:
            return False, f"Non trovo «{name}» tra i programmi in esecuzione."
        if not mine:
            return False, f"«{procs[0].group}» fa parte del sistema: non lo chiudo da qui."
        for p in mine:
            self._signal(p.pid, signal.SIGTERM)
        deadline = time.monotonic() + wait
        alive = [p for p in mine]
        while alive and time.monotonic() < deadline:
            time.sleep(0.2)
            alive = [p for p in alive if (self.root / "proc" / str(p.pid)).exists()]
        for p in alive:
            self._signal(p.pid, signal.SIGKILL)
        return True, f"Ho chiuso {mine[0].group}" + (" (non rispondeva: chiuso d'autorità)." if alive else ".")

    def _signal(self, pid: int, sig: int) -> None:
        try:
            self.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _uptime(root: Path) -> int:
    try:
        return int(float(_read(root / "proc/uptime").split()[0]))
    except (IndexError, ValueError):
        return 0


def advice(state: dict[str, Any]) -> list[str]:
    """Cosa dire all'utente guardando i numeri: le cose che contano, soprattutto per l'AI."""
    out = []
    cpu, mem = state["processore"], state["memoria"]
    if cpu.get("gradi") and cpu["gradi"] >= 90:
        out.append(f"Il processore scotta ({cpu['gradi']:.0f} °C): controlla che le ventole girino e che il PC respiri.")
    for g in state["schede_video"]:
        if not g.get("driver", True):
            out.append("La scheda NVIDIA non ha il driver caricato: l'AI e la grafica usano il processore, molto più lenti.")
        if g.get("gradi") and g["gradi"] >= 87:
            out.append(f"La scheda video è a {g['gradi']:.0f} °C: molto calda.")
    for m in state["ai"]["modelli"] or []:
        if m["memoria_mb"] and m["in_gpu"] < 100:
            where = "tutto nel processore" if m["in_gpu"] == 0 else f"solo il {m['in_gpu']}% nella scheda video"
            out.append(f"Il modello {m['nome']} gira {where}: risponde più lento. Un modello più piccolo starebbe tutto nella scheda video.")
    if mem["totale_mb"] and mem["usata_mb"] / mem["totale_mb"] > 0.9:
        top = next((p for p in state["programmi"] if p["chiudibile"]), None)
        out.append("La memoria è quasi piena" + (f": il programma che ne usa di più è {top['nome']} ({top['memoria_mb'] // 1024 or top['memoria_mb']} "
                                                  f"{'GB' if top['memoria_mb'] >= 1024 else 'MB'})." if top else "."))
    if mem["scambio_mb"] > 1024:
        out.append("Il sistema sta usando il disco come memoria: tutto rallenta. Chiudi qualcosa che non usi.")
    busy = [p for p in state["programmi"] if p["cpu"] >= 60 and not p["ai"]]
    if busy:
        out.append(f"{busy[0]['nome']} sta usando molto il processore ({busy[0]['cpu']:.0f}%).")
    return out


def describe(state: dict[str, Any]) -> str:
    """Il quadro in parole, per Nova."""
    cpu, mem = state["processore"], state["memoria"]
    lines = [f"Processore: {cpu['uso']:.0f}%" + (f", {cpu['gradi']:.0f} °C" if cpu.get("gradi") else ""),
             f"Memoria: {mem['usata_mb'] / 1024:.1f} GB usati su {mem['totale_mb'] / 1024:.0f} GB"]
    for g in state["schede_video"]:
        bits = [g["nome"]]
        if g.get("uso") is not None:
            bits.append(f"uso {g['uso']}%")
        if g.get("gradi"):
            bits.append(f"{g['gradi']:.0f} °C")
        if g.get("memoria_totale"):
            bits.append(f"memoria {g['memoria_usata']}/{g['memoria_totale']} MB")
        if g.get("ventola") is not None:
            bits.append(f"ventola {g['ventola']}%")
        if not g.get("driver", True):
            bits.append("driver non caricato")
        lines.append("Scheda video: " + ", ".join(bits))
    fans = state["sensori"]["ventole"]
    if fans:
        lines.append("Ventole: " + ", ".join(f"{f['nome']} {f['giri']} giri/min" for f in fans))
    models = state["ai"]["modelli"]
    if models:
        lines.append("Modelli AI caricati: " + ", ".join(
            f"{m['nome']} ({m['memoria_mb'] / 1024:.1f} GB, {m['in_gpu']}% nella scheda video)" for m in models))
    elif models is not None:
        lines.append("Nessun modello AI caricato in questo momento.")
    top = state["programmi"][:6]
    lines.append("Chi consuma di più: " + "; ".join(f"{p['nome']} {p['cpu']:.0f}% processore, {p['memoria_mb']} MB" for p in top))
    lines += state["consigli"]
    return "\n".join(lines)


_shared: TaskManager | None = None


def shared() -> TaskManager:
    global _shared
    if _shared is None:
        _shared = TaskManager()
    return _shared
