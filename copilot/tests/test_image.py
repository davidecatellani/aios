"""Chiavetta d'installazione: configurazione dell'installatore (image/config.toml)."""

import subprocess
import tomllib
from pathlib import Path

IMAGE = Path(__file__).resolve().parents[2] / "image"


def kickstart() -> str:
    return tomllib.loads((IMAGE / "config.toml").read_text())["customizations"]["installer"]["kickstart"]["contents"]


def test_log_command_is_the_repository_script():
    ks = kickstart()
    embedded = ks.split("<<'AIOSLOG'\n", 1)[1].split("AIOSLOG\n", 1)[0]
    assert embedded == (IMAGE / "aios-log.sh").read_text()


def test_kickstart_never_wipes_by_itself():
    ks = kickstart()
    for forbidden in ("clearpart", "zerombr", "autopart", "reqpart"):
        assert forbidden not in ks  # il disco lo sceglie la persona nell'installatore
    assert "%include /tmp/aios-dischi.ks" in ks and "ignoredisk --drives=" in ks


def test_pre_script_is_valid_bash():
    pre = kickstart().split("%pre\n", 1)[1].split("%end", 1)[0]
    assert subprocess.run(["bash", "-n"], input=pre, text=True).returncode == 0
