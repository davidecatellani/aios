"""Memoria compressa: più spazio per i modelli AI anche su dispositivi piccoli.

Tre leve, tutte decise in base al dispositivo:
1. RAM compressa (zram + zstd): le pagine delle app ferme restano in RAM ma compresse,
   di solito 2-4 volte; resta più memoria vera per il modello.
2. Memoria della conversazione del modello (KV cache) a 8 o 4 bit invece di 16,
   con flash attention: contesti lunghi in metà (o un quarto) della memoria.
3. Lunghezza del contesto adatta alla RAM.
La scelta del modello compresso (4, 3 o 2 bit) la fa models.py con la prova sul
dispositivo. Le impostazioni di sistema richiedono conferma e privilegi (pkexec).

    aios-memoria stato | configura
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .hardware import Device, detect
from .tools.base import Runner

ZRAM_CONF = Path("/etc/systemd/zram-generator.conf")
SYSCTL_CONF = Path("/etc/sysctl.d/90-aios-zram.conf")
OLLAMA_DROPIN = Path("/etc/systemd/system/ollama.service.d/aios-memoria.conf")


@dataclass
class Plan:
    zram_mb: int
    kv_cache: str  # q8_0 | q4_0
    context: int  # token
    files: dict[Path, str]


def plan_for(device: Device) -> Plan:
    ram_mb = int(device.ram_gb * 1024)
    # Metà della RAM come area compressa (con zstd vale circa il doppio), al massimo 8 GB.
    zram_mb = max(1024, min(ram_mb // 2, 8192))
    kv = "q4_0" if device.ram_gb < 8 and device.vram_gb < 6 else "q8_0"
    context = 4096 if device.ram_gb < 8 else 8192 if device.ram_gb < 24 and device.vram_gb < 12 else 16384
    files = {
        ZRAM_CONF: f"# Generato da SoIA: RAM compressa\n[zram0]\nzram-size = {zram_mb}\ncompression-algorithm = zstd\nswap-priority = 100\n",
        # Con zram conviene usare la memoria compressa prima del disco e leggere una pagina alla volta.
        SYSCTL_CONF: "# Generato da SoIA: parametri adatti alla RAM compressa\nvm.swappiness = 180\nvm.page-cluster = 0\n"
                     "vm.watermark_boost_factor = 0\nvm.watermark_scale_factor = 125\n",
        OLLAMA_DROPIN: f"# Generato da SoIA: memoria della conversazione compressa\n[Service]\nEnvironment=OLLAMA_FLASH_ATTENTION=1\n"
                       f"Environment=OLLAMA_KV_CACHE_TYPE={kv}\nEnvironment=OLLAMA_CONTEXT_LENGTH={context}\n",
    }
    return Plan(zram_mb, kv, context, files)


def apply_plan(plan: Plan, runner: Runner | None = None) -> list[str]:
    """Scrive i file di sistema (con pkexec) e riavvia ciò che serve. Restituisce eventuali errori."""
    runner = runner or Runner()
    errors = []
    for path, content in plan.files.items():
        code, out = runner.run(["pkexec", "sh", "-c", f"mkdir -p '{path.parent}' && cat > '{path}' <<'AIOS_EOF'\n{content}AIOS_EOF"])
        if code != 0:
            errors.append(f"{path}: {out[-200:]}")
    for cmd in (["pkexec", "sysctl", "--system"], ["pkexec", "systemctl", "daemon-reload"],
                ["pkexec", "systemctl", "restart", "systemd-zram-setup@zram0.service"],
                ["pkexec", "systemctl", "try-restart", "ollama.service"]):
        code, out = runner.run(cmd)
        if code != 0:
            errors.append(f"{' '.join(cmd[1:])}: {out[-200:]}")
    return errors


@dataclass
class ZramStatus:
    size_mb: int
    original_mb: float  # dati messi nell'area compressa
    compressed_mb: float  # memoria reale che occupano
    algorithm: str

    @property
    def ratio(self) -> float:
        return self.original_mb / self.compressed_mb if self.compressed_mb else 0.0

    @property
    def saved_mb(self) -> float:
        return max(0.0, self.original_mb - self.compressed_mb)


def zram_status(root: Path = Path("/")) -> ZramStatus | None:
    block = root / "sys/block/zram0"
    try:
        size = int((block / "disksize").read_text()) // 2**20
        stats = (block / "mm_stat").read_text().split()
        algo = (block / "comp_algorithm").read_text()
    except (OSError, ValueError, IndexError):
        return None
    if not size:
        return None
    active = next((a.strip("[]") for a in algo.split() if a.startswith("[")), algo.strip())
    return ZramStatus(size, int(stats[0]) / 2**20, int(stats[1]) / 2**20, active)


def _dropin_env(root: Path) -> dict[str, str]:
    """Le impostazioni che il servizio Ollama riceve dal file di SoIA."""
    try:
        text = (root / OLLAMA_DROPIN.relative_to("/")).read_text()
    except OSError:
        return {}
    return dict(re.findall(r"^Environment=(\w+)=(\S+)", text, re.M))


def describe(device: Device, root: Path = Path("/"), ollama_env: dict[str, str] | None = None) -> str:
    plan = plan_for(device)
    z = zram_status(root)
    env = ollama_env if ollama_env is not None else {**_dropin_env(root), **os.environ}
    lines = [f"Memoria: {device.ram_gb:.0f} GB."]
    if z:
        lines.append(f"RAM compressa attiva: {z.size_mb / 1024:.1f} GB ({z.algorithm}); ora contiene {z.original_mb:.0f} MB "
                     f"che occupano {z.compressed_mb:.0f} MB (×{z.ratio:.1f}, risparmiati {z.saved_mb:.0f} MB).")
    else:
        lines.append(f"RAM compressa non attiva: posso attivarla ({plan.zram_mb / 1024:.1f} GB con zstd).")
    kv = env.get("OLLAMA_KV_CACHE_TYPE")
    if kv:
        lines.append(f"Memoria della conversazione del modello compressa ({kv}).")
    else:
        lines.append(f"Memoria della conversazione del modello non compressa: con {plan.kv_cache} occuperebbe "
                     f"{'un quarto' if plan.kv_cache == 'q4_0' else 'metà'} della memoria.")
    if not z or not kv:
        lines.append("Dimmi «ottimizza la memoria» per attivare tutto (serve la password di amministratore).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv) or ["stato"]
    device = detect()
    if args[0] == "stato":
        print(describe(device))
    elif args[0] == "configura":
        plan = plan_for(device)
        print(f"RAM compressa {plan.zram_mb} MB (zstd), conversazione del modello {plan.kv_cache}, contesto {plan.context} token.")
        errors = apply_plan(plan)
        print("Fatto." if not errors else "Problemi:\n" + "\n".join(errors))
        return 1 if errors else 0
    else:
        print("aios-memoria [stato | configura]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
