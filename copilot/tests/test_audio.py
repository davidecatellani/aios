import json

from aios_copilot import audio

DUMP = [
    {"id": 0, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
     "metadata": [{"subject": 0, "key": "default.audio.sink", "value": {"name": "alsa_output.pci-0000_00_1f.3.iec958-stereo"}},
                  {"subject": 0, "key": "default.audio.source", "value": {"name": "aios_mic_pulito"}}]},
    {"id": 65, "type": "PipeWire:Interface:Device", "info": {"props": {"media.class": "Audio/Device", "device.description": "GP104 High Definition Audio Controller"},
     "params": {"Profile": [{"index": 3, "name": "output:hdmi-stereo"}],
                "EnumProfile": [{"index": 0, "name": "off"}, {"index": 3, "name": "output:hdmi-stereo", "description": "Digital Stereo (HDMI) Output", "available": "yes"},
                                {"index": 5, "name": "output:hdmi-stereo-extra1", "description": "Digital Stereo (HDMI 2) Output", "available": "no"}]}}},
    {"id": 66, "type": "PipeWire:Interface:Device", "info": {"props": {"media.class": "Audio/Device", "device.description": "Built-in Audio"},
     "params": {"Profile": [{"index": 1, "name": "output:iec958-stereo+input:analog-stereo"}],
                "EnumProfile": [{"index": 1, "name": "output:iec958-stereo+input:analog-stereo", "available": "yes"},
                                {"index": 2, "name": "output:analog-stereo", "description": "Analog Stereo Output", "available": "yes"}]}}},
    {"id": 54, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Sink", "device.id": 66,
     "node.name": "alsa_output.pci-0000_00_1f.3.iec958-stereo", "node.description": "Built-in Audio Digital Stereo (IEC958)"}}},
    {"id": 70, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Sink", "device.id": 65, "node.nick": "VG245",
     "node.name": "alsa_output.pci-0000_01_00.1.hdmi-stereo", "node.description": "GP104 High Definition Audio Controller Digital Stereo (HDMI)"}}},
    {"id": 37, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source", "node.name": "aios_mic_pulito",
     "node.description": "Microfono (senza eco e rumori)"}}},
    {"id": 59, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source", "device.id": 66,
     "node.name": "alsa_input.pci-0000_00_1f.3.analog-stereo", "node.description": "Built-in Audio Analog Stereo"}}},
    {"id": 99, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Stream/Output/Audio", "application.name": "Firefox"}}},
]


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd == ["pw-dump"]:
            return 0, json.dumps(DUMP)
        if cmd[:2] == ["wpctl", "get-volume"]:
            return 0, "Volume: 0.40"
        return 0, ""


def test_reads_outputs_inputs_and_programs_like_the_users_pc():
    st = audio.Audio(Fake()).state()
    names = {e.nome: e for e in st["uscite"]}
    assert names["Uscita ottica (S/PDIF)"].predefinito and names["Monitor VG245 (HDMI)"].tipo == "monitor"
    assert names["Monitor VG245 (HDMI)"].livello == 40
    analog = next(e for e in st["uscite"] if e.da_attivare == {"dispositivo": 66, "profilo": 2})
    assert analog.tipo == "casse"  # la presa per casse e cuffie, spenta: si può accendere
    assert [e.nome for e in st["ingressi"]][0] == "Microfono (senza eco e rumori)" and st["ingressi"][0].predefinito
    assert st["programmi"][0].programma == "Firefox"
    assert not any("HDMI 2" in e.nome for e in st["uscite"])  # profilo non disponibile: niente


def test_optical_output_is_avoided_at_startup_if_the_user_never_chose():
    run = Fake()
    assert audio.Audio(run).auto_default() == "Monitor VG245 (HDMI)"
    assert ["wpctl", "set-default", "70"] in run.calls


def test_route_by_voice_and_per_app_volume():
    run = Fake()
    a = audio.Audio(run, wait=lambda s: None)
    assert a.route("fai uscire l'audio dal monitor") == "Fatto: l'audio esce da Monitor VG245 (HDMI)."
    assert ["wpctl", "set-default", "70"] in run.calls
    assert a.app_volume("firefox", 30) == "Volume di Firefox al 30%."
    assert ["wpctl", "set-volume", "-l", "1.5", "99", "30%"] in run.calls
    assert "non sta suonando" in a.app_volume("spotify", 30)


def test_hidden_hdmi_output_is_offered_and_switched_on():
    dump = [o for o in DUMP if o["id"] != 70]
    dump[1] = {**dump[1], "info": {**dump[1]["info"], "params": {**dump[1]["info"]["params"], "Profile": [{"index": 0, "name": "off"}]}}}

    class Off(Fake):
        def __call__(self, cmd):
            self.calls.append(cmd)
            if cmd == ["pw-dump"]:
                return 0, json.dumps(dump if not any(c[:2] == ["wpctl", "set-profile"] for c in self.calls) else DUMP)
            return super().__call__(cmd) if cmd[:2] == ["wpctl", "get-volume"] else (0, "")

    run = Off()
    a = audio.Audio(run, wait=lambda s: None)
    hidden = [e for e in a.state(volumes=False)["uscite"] if e.da_attivare]
    assert hidden and hidden[0].tipo == "monitor" and hidden[0].da_attivare["dispositivo"] == 65
    assert a.route("metti l'audio sul monitor").startswith("Fatto")
    assert ["wpctl", "set-profile", "65", "3"] in run.calls and ["wpctl", "set-default", "70"] in run.calls
