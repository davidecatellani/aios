"""Il controllo preliminare del telefono deve limitarsi alla lettura via ADB."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


path = Path(__file__).resolve().parents[2] / "phone/scripts/verifica-telefono.py"
spec = importlib.util.spec_from_file_location("phone_probe", path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_treble_does_not_establish_edge50neo_compatibility():
    props = {"ro.product.manufacturer": "motorola", "ro.product.model": "motorola edge 50 neo",
             "ro.product.cpu.abi": "arm64-v8a", "ro.treble.enabled": "true", "ro.build.version.sdk": "36"}
    result = probe.assess(props)
    assert result["edge_50_neo_riconosciuto"] and result["prerequisiti_gsi_rilevati"]
    assert not result["compatibilita_soia_verificata"]
    assert not probe.assess({**props, "ro.product.model": "motorola edge 50"})["edge_50_neo_riconosciuto"]
    assert not probe.assess({**props, "ro.treble.enabled": ""})["prerequisiti_gsi_rilevati"]


def test_oem_unlock_is_not_an_unlocked_bootloader():
    props = {"sys.oem_unlock_allowed": "1", "ro.boot.flash.locked": "1",
             "ro.boot.vbmeta.device_state": "locked", "ro.product.manufacturer": "motorola",
             "ro.product.model": "XT2409-1"}
    result = probe.assess(props)
    assert result["edge_50_neo_riconosciuto"]
    assert result["bootloader"] == {"sblocco_oem_attivato": True, "sbloccato": False}
    props["ro.boot.flash.locked"] = "0"
    assert probe.assess(props)["bootloader"]["sbloccato"] is None  # proprietà in conflitto
    props["ro.boot.vbmeta.device_state"] = "unlocked"
    assert probe.assess(props)["bootloader"]["sbloccato"] is True


@pytest.mark.parametrize("sdk", ["34", "35", "37", "", "non numerico"])
def test_probe_rejects_other_android_versions(sdk):
    result = probe.assess({"ro.product.cpu.abi": "arm64-v8a", "ro.treble.enabled": "true",
                          "ro.build.version.sdk": sdk})
    assert not result["prerequisiti_gsi_rilevati"]


def test_probe_reads_only_selected_properties(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["adb", "devices"]:
            return SimpleNamespace(stdout="List of devices attached\nprivate-serial\tdevice\n")
        assert cmd[:4] == ["adb", "-s", "private-serial", "shell"]
        assert cmd[4] == "getprop" and cmd[5] in probe.PROPERTIES
        return SimpleNamespace(stdout="value\n")

    monkeypatch.setattr(probe.subprocess, "run", run)
    report = probe.assess(probe.collect("adb"))
    assert len(calls) == len(probe.PROPERTIES) + 1
    assert "private-serial" not in str(report)


def test_probe_refuses_ambiguous_or_unauthorized_devices(monkeypatch):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout="List of devices attached\none\tdevice\ntwo\tdevice\nthree\tunauthorized\n"))
    with pytest.raises(ValueError, match="un solo telefono"):
        probe.collect("adb")
    with pytest.raises(ValueError, match="autorizzato"):
        probe.collect("adb", "three")
