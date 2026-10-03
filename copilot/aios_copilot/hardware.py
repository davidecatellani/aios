"""Caratteristiche del dispositivo che contano per l'AI locale.

Letture standard di Linux (/proc, /sys, nvidia-smi): nessun privilegio richiesto.
`root` permette di provarle su un albero di file finto.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

GB = 1024**3


@dataclass
class GPU:
    name: str
    vram_gb: float  # 0 = memoria condivisa con il sistema (integrata)
    vendor: str  # nvidia | amd | intel | altro


@dataclass
class Device:
    ram_gb: float
    ram_available_gb: float
    cpu: str
    cores: int
    arch: str
    avx2: bool
    gpus: list[GPU] = field(default_factory=list)
    npu: str = ""
    disk_free_gb: float = 0.0
    battery: bool = False

    @property
    def vram_gb(self) -> float:
        return max((g.vram_gb for g in self.gpus), default=0.0)

    @property
    def fast_cpu(self) -> bool:
        """Abbastanza per un modello da 7 miliardi senza GPU: istruzioni vettoriali e core sufficienti."""
        return (self.avx2 or self.arch in ("aarch64", "arm64")) and self.cores >= 8

    def summary(self) -> str:
        parts = [f"{self.ram_gb:.0f} GB di memoria", f"{self.cores} core ({self.cpu})"]
        for g in self.gpus:
            parts.append(f"GPU {g.name}" + (f" con {g.vram_gb:.0f} GB" if g.vram_gb else " (integrata)"))
        if self.npu:
            parts.append(f"NPU {self.npu}")
        parts.append(f"{self.disk_free_gb:.0f} GB liberi su disco")
        return ", ".join(parts)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _run(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


def detect(root: Path = Path("/"), run: Callable[[list[str]], str] = _run, home: Path | None = None) -> Device:
    meminfo = {k: int(v.split()[0]) for k, _, v in
               (line.partition(":") for line in _read(root / "proc/meminfo").splitlines()) if v.strip()}
    cpuinfo = _read(root / "proc/cpuinfo")
    model = re.search(r"^(?:model name|Hardware|Model)\s*:\s*(.+)$", cpuinfo, re.M)
    flags = re.search(r"^(?:flags|Features)\s*:\s*(.+)$", cpuinfo, re.M)
    cores = len(re.findall(r"^processor\s*:", cpuinfo, re.M)) or (os.cpu_count() or 1)

    gpus: list[GPU] = []
    for line in run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).splitlines():
        name, _, mem = line.rpartition(",")
        if name and mem.strip().isdigit():
            gpus.append(GPU(name.strip(), int(mem) / 1024, "nvidia"))
    for card in sorted((root / "sys/class/drm").glob("card[0-9]")):
        vendor = VENDORS.get(_read(card / "device/vendor").strip(), "altro")
        if vendor == "nvidia" and any(g.vendor == "nvidia" for g in gpus):
            continue  # già letta da nvidia-smi
        vram = _read(card / "device/mem_info_vram_total").strip()
        vram_gb = int(vram) / GB if vram.isdigit() else 0.0
        if vendor == "amd" and vram_gb < 1:
            vram_gb = 0.0  # APU: memoria condivisa
        gpus.append(GPU({"amd": "AMD Radeon", "intel": "Intel", "nvidia": "NVIDIA"}.get(vendor, "grafica"), vram_gb, vendor))

    npu = ""
    if list((root / "dev/accel").glob("accel*")) or (root / "sys/class/accel").exists():
        npu = "Intel NPU" if (root / "sys/module/intel_vpu").exists() else \
              "AMD XDNA" if (root / "sys/module/amdxdna").exists() else "acceleratore"

    battery = any(_read(p / "type").strip() == "Battery" for p in (root / "sys/class/power_supply").glob("*"))
    try:
        free = shutil.disk_usage(home or Path.home()).free / GB
    except OSError:
        free = 0.0
    return Device(
        ram_gb=meminfo.get("MemTotal", 0) / 1024**2, ram_available_gb=meminfo.get("MemAvailable", 0) / 1024**2,
        cpu=(model.group(1).strip() if model else platform.processor() or "sconosciuta")[:60], cores=cores,
        arch=platform.machine() if root == Path("/") else ("aarch64" if "asimd" in (flags.group(1) if flags else "") else "x86_64"),
        avx2="avx2" in (flags.group(1).split() if flags else []), gpus=gpus, npu=npu, disk_free_gb=free, battery=battery)
