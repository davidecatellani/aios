"""Vicini senza Wi-Fi: codici BLE, scelta del collegamento, reti NetworkManager, comandi a Nova."""

import json

from aios_copilot.agent import Agent
from aios_copilot.energy import EnergyBrain, Reading
from aios_copilot.mesh import nearby as nb
from aios_copilot.mesh.files import Devices
from aios_copilot.mesh.service import nearby_secrets
from aios_copilot.tools import phone as phone_tools

T = 1_800_000_000.0
PHONE = nb.secret_from_device("ab" * 32, "Pixel 8", "AA:BB:CC:DD:EE:FF")
OTHER = nb.secret_from_device("cd" * 32, "telefono di un altro")


def test_beacon_only_for_own_devices_and_rotating():
    b = nb.make_beacon(PHONE, "telefono", nb.F_INTERNET, T)
    assert len(b) == 11
    seen = nb.read_beacon(b, [OTHER, PHONE], T + 60)
    assert seen and seen.secret.name == "Pixel 8" and seen.role == "telefono" and seen.has(nb.F_INTERNET)
    assert nb.read_beacon(b, [OTHER], T) is None  # gli estranei non lo riconoscono
    assert nb.make_beacon(PHONE, "telefono", nb.F_INTERNET, T + nb.WINDOW) != b  # cambia ogni 15 minuti
    assert nb.read_beacon(b, [PHONE], T + 3 * nb.WINDOW) is None  # un codice vecchio non vale più
    forged = b[:2] + bytes([b[2] | nb.F_CHIEDE_INTERNET]) + b[3:]
    assert nb.read_beacon(forged, [PHONE], T) is None  # le richieste non si possono aggiungere


def test_direct_network_name_is_shared_and_daily():
    ssid, password = nb.network_for(PHONE, T)
    assert ssid.startswith("AIOS-") and len(ssid) == 11 and len(password) == 20
    assert nb.network_for(PHONE, T + 3600) == (ssid, password)
    assert nb.network_for(PHONE, T + 86400) != (ssid, password)
    assert nb.network_for(OTHER, T)[0] != ssid


def beacon(flags=0):
    return nb.read_beacon(nb.make_beacon(PHONE, "telefono", flags, T), [PHONE], T)


def test_nova_chooses_the_link():
    S = nb.Situation
    assert nb.choose_link(S(True, True, True, beacon())).kind == "rete"
    assert nb.choose_link(S(False, True, False, beacon(), "pesante")).kind == "wifi"
    assert nb.choose_link(S(False, False, False, beacon(), "pesante")).kind == "bluetooth"  # Wi-Fi del PC occupato
    assert nb.choose_link(S(False, True, False, beacon(nb.F_BATTERIA_BASSA), "pesante")).kind == "bluetooth"
    assert nb.choose_link(S(False, True, False, beacon(), "leggero")).kind == "bluetooth"
    internet = nb.choose_link(S(False, True, False, beacon(nb.F_INTERNET), "internet"))
    assert internet.kind == "internet" and internet.ask
    assert not nb.choose_link(S(False, True, False, beacon(nb.F_INTERNET), "internet", "sempre")).ask
    assert nb.choose_link(S(False, True, False, beacon(nb.F_INTERNET), "internet", "mai")).kind == "bluetooth"
    assert nb.choose_link(S(False, True, False, beacon(nb.F_INTERNET | nb.F_BATTERIA_BASSA), "internet")).kind == "bluetooth"
    assert nb.choose_link(S(False, True, False, beacon(), "internet")).kind == "bluetooth"  # niente da condividere


def test_parse_bluez_objects():
    payload = list(nb.make_beacon(PHONE, "telefono", 0, T))
    doc = {"type": "a{oa{sa{sv}}}", "data": [{
        "/org/bluez/hci0/dev_11": {"org.bluez.Device1": {
            "Address": {"type": "s", "data": "11:22:33:44:55:66"}, "RSSI": {"type": "n", "data": -58},
            "ManufacturerData": {"type": "a{qv}", "data": {"65535": {"type": "ay", "data": payload}}}}},
        "/org/bluez/hci0/dev_22": {"org.bluez.Device1": {
            "Address": {"type": "s", "data": "77:88:99:AA:BB:CC"},
            "ManufacturerData": {"type": "a{qv}", "data": {"76": {"type": "ay", "data": [1, 2]}}}}},  # un iPhone
        "/org/bluez/hci0": {"org.bluez.Adapter1": {}}}]}
    assert nb.parse_managed_objects(json.dumps(doc)) == [("11:22:33:44:55:66", bytes(payload), -58)]
    assert nb.parse_managed_objects("non json") == []


def test_advertiser_commands():
    class Proc:
        def __init__(self):
            import io
            self.stdin, self.killed = io.StringIO(), False

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    procs = []
    adv = nb.BleAdvertiser(popen=lambda *a, **k: procs.append(Proc()) or procs[-1])
    adv.set(b"\x01\x02")
    adv.set(b"\x01\x02")  # uguale: nessun nuovo processo
    assert len(procs) == 1 and "manufacturer 0xffff 0x01 0x02" in procs[0].stdin.getvalue()
    assert procs[0].stdin.getvalue().endswith("advertise on\n")


class FakeScanner:
    def __init__(self):
        self.seen = []

    def scan(self):
        return self.seen


class FakeAdvertiser:
    def __init__(self):
        self.payload = b""

    def set(self, payload):
        self.payload = payload

    def stop(self):
        self.payload = b""


class FakeLinks:
    def __init__(self, wifi_free=True, internet=False, phone_answers=True):
        self.wifi_free, self.net, self.answers, self.calls = wifi_free, internet, phone_answers, []

    def wifi(self):
        return "wlan0", self.wifi_free

    def internet(self):
        return self.net

    def host_wifi(self, dev, ssid, pw):
        self.calls.append(("ospita", ssid))
        return True

    def join_wifi(self, dev, ssid, pw):
        self.calls.append(("entra", ssid))
        return self.answers

    def join_bluetooth(self, mac):
        self.calls.append(("bluetooth", mac))
        return self.answers

    def down(self):
        self.calls.append(("chiudi",))


def make(links, clock=lambda: T):
    scanner, adv = FakeScanner(), FakeAdvertiser()
    return nb.Nearby(scanner, adv, links, lambda: [PHONE], clock=clock), scanner, adv


def test_round_links_and_unlinks_by_itself():
    links = FakeLinks()
    near, scanner, adv = make(links)
    assert near.round(False) == "nessun dispositivo vicino"
    assert nb.read_beacon(adv.payload, [PHONE], T).role == "pc"  # il PC si annuncia comunque
    scanner.seen = [("11:22", nb.make_beacon(PHONE, "telefono", 0, T), -50)]
    assert near.round(False, "pesante") == "collegato (Wi-Fi diretto)"
    assert links.calls[-1] == ("ospita", nb.network_for(PHONE, T)[0])
    assert nb.read_beacon(adv.payload, [PHONE], T).has(nb.F_RETE)  # il telefono sa che può entrare
    assert "Wi-Fi diretto" in near.status()
    assert near.round(True, "pesante") == "collegato (Wi-Fi diretto)"  # la «stessa rete» è quella creata da Nova
    scanner.seen = []
    for _ in range(nb.LOST_AFTER - 1):
        near.round(False)
    assert near.link == "wifi"
    assert near.round(False) == "telefono lontano: chiuso Wi-Fi diretto" and near.link == ""


def test_round_bluetooth_when_wifi_busy_and_waits_for_phone():
    links = FakeLinks(wifi_free=False, phone_answers=False)
    near, scanner, adv = make(links)
    scanner.seen = [("11:22", nb.make_beacon(PHONE, "telefono", 0, T), -50)]
    assert near.round(False, "pesante").startswith("provo Bluetooth")
    assert links.calls[-1] == ("bluetooth", "AA:BB:CC:DD:EE:FF")
    assert nb.read_beacon(adv.payload, [PHONE], T).has(nb.F_CHIEDE_BT)  # «apri la rete Bluetooth»
    links.answers = True
    assert near.round(False) == "collegato (Bluetooth)"


def test_internet_only_with_permission():
    links = FakeLinks()
    near, scanner, adv = make(links)
    scanner.seen = [("11:22", nb.make_beacon(PHONE, "telefono", nb.F_INTERNET, T), -50)]
    asked = []
    near.request("internet")
    assert near.round(False, confirm=lambda why: asked.append(why) or False) == "collegato (internet del telefono)"
    assert not asked  # chiesto dall'utente: niente doppia domanda
    assert nb.read_beacon(adv.payload, [PHONE], T).has(nb.F_CHIEDE_INTERNET)
    assert ("entra", nb.network_for(PHONE, T)[0]) in links.calls
    assert near.release().startswith("Ho chiuso") and near.link == ""

    near2, scanner2, _ = make(FakeLinks())
    scanner2.seen = scanner.seen
    near2.share_internet = "chiedi"
    assert near2.round(False, "internet", confirm=lambda why: False) == "collegato (Bluetooth)"


def test_secrets_from_paired_phones(tmp_path):
    devices = Devices(tmp_path / "devices.json")
    key = devices.add("Pixel 8", "aa:bb:cc:dd:ee:ff")
    devices.add("Vecchio", "non un indirizzo")
    found = nearby_secrets(devices)
    import hashlib

    phone_side = nb.secret_from_device(hashlib.sha256(key.encode()).hexdigest(), "x")
    assert any(s.key == phone_side.key for s in found)  # il telefono calcola lo stesso segreto dalla sua chiave
    assert {s.bt for s in found} == {"AA:BB:CC:DD:EE:FF", ""}


def test_energy_paces_the_search(tmp_path):
    brain = EnergyBrain(tmp_path / "e.json", clock=lambda: T, battery=lambda: Reading(None, False))
    assert brain.decide().intervals["vicini"] == 20
    brain = EnergyBrain(tmp_path / "e2.json", clock=lambda: T, battery=lambda: Reading(5, False))
    assert brain.decide().intervals["vicini"] is None  # batteria agli sgoccioli: niente ricerca
    assert "telefono senza Wi-Fi" in brain.explain("vicini")


def test_copilot_commands():
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    sent = []

    def command(c):
        sent.append(c)
        return {"testo": "ok"}

    agent = Agent(NoModel(), phone_tools.make_tools(command=command), confirm=lambda *a, **k: True,
                  routers=[phone_tools.PhoneRouter()])
    for text, kind in [("usa internet del telefono", "internet"), ("collegati al telefono", "auto"),
                       ("collegati al telefono via bluetooth", "bluetooth"),
                       ("smetti di usare internet del telefono", "chiudi")]:
        assert agent.ask(text) == "ok"
        assert sent[-1] == {"azione": "vicino", "tipo": kind}


def test_fixed_vector_shared_with_the_phone_app():
    # Gli stessi valori sono scritti in phone/packages/apps/Nova/.../nearby/NearbyCode.kt: se cambiano qui,
    # il telefono e il PC non si riconoscono più.
    s = nb.secret_from_device("ab" * 32, "x")
    assert nb.make_beacon(s, "pc", nb.F_RETE, 1_800_000_000).hex() == "0100029f0d5a6712816cbc"
    assert nb.network_for(s, 1_800_000_000) == ("AIOS-a66541", "4LAHyg7kCQV1ZzAoX8Xd")


def test_networkmanager_commands():
    class R:
        def __init__(self, zones=True):
            self.ran, self.zones = [], zones

        def has(self, prog):
            return prog != "firewall-cmd" or self.zones

        def run(self, cmd):
            self.ran.append(cmd)
            if cmd[:2] == ["firewall-cmd", "--get-zones"]:
                return 0, "public trusted aios-vicino"
            if cmd[:2] == ["nmcli", "-t"] and "device" in cmd:
                return 0, "wlp2s0:wifi:connected:Casa\nlo:loopback:connected (externally):lo"
            return 0, ""

    r = R()
    links = nb.Links(r)
    assert links.wifi() == ("wlp2s0", False)  # collegato alla rete di casa: occupato
    assert links.host_wifi("wlp2s0", "AIOS-abcdef", "segreta")
    add = next(c for c in r.ran if c[:3] == ["nmcli", "connection", "add"])
    assert {"802-11-wireless.hidden", "yes", "ipv4.method", "shared", "connection.zone", "aios-vicino"} <= set(add)
    assert links.join_bluetooth("aa:bb:cc:dd:ee:ff") and not links.join_bluetooth("niente; rm -rf /")
    assert ["nmcli", "connection", "up", "aios-vicino-bt"] in r.ran
    r2 = R(zones=False)
    nb.Links(r2).host_wifi("wlp2s0", "AIOS-abcdef", "segreta")
    assert "connection.zone" not in next(c for c in r2.ran if c[:3] == ["nmcli", "connection", "add"])
