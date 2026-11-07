import pytest

from aios_copilot.mesh import bluetooth as bt_mod
from aios_copilot.mesh.bluetooth import Bluetooth, BluetoothAdapter, share_round
from aios_copilot.tools.base import Runner

SONY, KEYBOARD, CAR = "AA:BB:CC:11:22:33", "DD:EE:FF:44:55:66", "12:34:56:78:9A:BC"


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


class FakeBt(Runner):
    def __init__(self, paired, around, icons):
        super().__init__(which=lambda p: p if p == "bluetoothctl" else None)
        self.paired_now, self.around, self.icons, self.ran = dict(paired), dict(around), icons, []

    def run(self, cmd):
        self.ran.append(cmd)
        if cmd[1:3] == ["devices", "Paired"]:
            return 0, "".join(f"Device {m} {n}\n" for m, n in self.paired_now.items())
        if cmd[1:] == ["devices"]:
            return 0, "".join(f"Device {m} {n}\n" for m, n in {**self.paired_now, **self.around}.items())
        if cmd[1] == "info":
            return 0, f"Device {cmd[2]}\n\tIcon: {self.icons.get(cmd[2], '')}\n"
        if cmd[1] == "pair":
            self.paired_now[cmd[2]] = self.around.get(cmd[2], "?")
        if cmd[1] == "remove":
            self.paired_now.pop(cmd[2], None)
        return 0, ""


ICONS = {SONY: "audio-headset", KEYBOARD: "input-keyboard", CAR: "audio-card"}


def test_devices_follow_the_user_safely():
    phone = FakeBt({SONY: "WH-1000XM5", KEYBOARD: "Tastiera MX"}, {}, ICONS)
    Bluetooth(phone).remember_local()  # il telefono annota i suoi
    records = BluetoothAdapter().records()
    assert set(records) == {SONY, KEYBOARD} and records[SONY]["icona"] == "audio-headset"

    bt_mod.save_known({})  # ora siamo sul PC: l'elenco arriva dalla sincronizzazione
    for mac, value in records.items():
        BluetoothAdapter().apply(mac, value)
    BluetoothAdapter().apply("non-un-mac", {"nome": "x"})
    pc = FakeBt({}, {SONY: "WH-1000XM5", KEYBOARD: "Tastiera MX"}, ICONS)
    notes, asked = [], []
    done = share_round(Bluetooth(pc), lambda t, b: notes.append(t), lambda t, b, a: asked.append(t) or "")
    assert done == ["WH-1000XM5"] and notes == ["🎧 WH-1000XM5 collegato anche qui"]
    assert asked == ["🔵 Tastiera MX è qui"] and KEYBOARD not in pc.paired_now  # tastiera: solo con conferma
    assert ["bluetoothctl", "trust", SONY] in pc.ran
    assert share_round(Bluetooth(pc), lambda *a: None, lambda t, b, a: "collega") == ["Tastiera MX"]


def test_far_devices_and_forgetting():
    pc = FakeBt({}, {}, ICONS)
    BluetoothAdapter().apply(CAR, {"nome": "Auto", "icona": "audio-card", "da": "Pixel 8"})
    assert share_round(Bluetooth(pc), lambda *a: None, lambda *a: "") == []  # l'auto non è vicina
    pc.paired_now[CAR] = "Auto"
    assert bt_mod.forget(Bluetooth(pc), "auto") == [CAR] and CAR not in pc.paired_now
    assert BluetoothAdapter().records() == {}  # tolto per tutti (alla sincronizzazione diventa una cancellazione)
    BluetoothAdapter().apply(SONY, {"nome": "Cuffie"})
    BluetoothAdapter().apply(SONY, None)
    assert SONY not in bt_mod.load_known()
