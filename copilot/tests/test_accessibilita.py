from aios_copilot import accessibilita as A
from aios_copilot import hyprconf
from aios_copilot import sottotitoli as S
from aios_copilot.tools.display import DisplayRouter


def test_apply_writes_hyprland_and_gsettings(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    runs, hypr, caps = [], [], []
    conf, msg = A.apply({"contrasto": True, "meno_animazioni": True, "filtro": "deuteranopia", "cursore_grande": True,
                         "sottotitoli": True, "inventato": 1},
                        run=lambda c: runs.append(c) or (0, ""), hypr=lambda c: hypr.append(c) or (0, "ok"), captions=caps.append)
    assert conf["contrasto"] and conf["filtro"] == "deuteranopia" and caps == [True] and msg == "Fatto."
    assert ["keyword", "animations:enabled", "0"] in hypr and ["setcursor", "Adwaita", "48"] in hypr
    assert any(c[-2:] == ["high-contrast", "true"] for c in runs)
    text = hyprconf.conf_path().read_text()
    assert "enabled = false" in text and "deuteranopia.frag" in text and "XCURSOR_SIZE,48" in text
    A.apply({"filtro": "nessuno"}, run=lambda c: (0, ""), hypr=lambda c: hypr.append(c) or (0, "ok"))
    assert hypr[-1] == ["keyword", "decoration:screen_shader", "[[EMPTY]]"]


def test_zoom():
    calls = []

    def hypr(args):
        calls.append(args)
        return (0, "float: 2.000000\nset: true") if args[0] == "getoption" else (0, "ok")

    assert A.zoom("+", hypr) == 2.5 and calls[-1] == ["keyword", "cursor:zoom_factor", "2.50"]
    assert A.zoom("0", hypr) == 1.0


def test_captions_phrases():
    import array

    loud = array.array("h", [3000, -3000] * (S.CHUNK // 4)).tobytes()
    quiet = bytes(S.CHUNK)
    t = [0.0]

    def source(stop):
        for chunk in [loud] * 10 + [quiet] * 5 + [loud] * 3 + [quiet] * 4:
            t[0] += 0.2
            yield chunk

    shown = []
    cap = S.Captioner(lambda text, final: shown.append((text, final)), transcribe=lambda pcm: f"{len(pcm) // 6400} decimi",
                      source=source, clock=lambda: t[0])
    cap.loop()
    finals = [x for x in shown if x[1]]
    assert len(finals) == 2 and any(not f for _, f in shown)  # due frasi chiuse dalla pausa, con un provvisorio


def test_router():
    r = DisplayRouter()
    assert r.match("attiva i sottotitoli").args == {"opzione": "sottotitoli", "acceso": True}
    assert r.match("spegni il lettore dello schermo").args == {"opzione": "lettore", "acceso": False}
    assert r.match("ingrandisci lo schermo").args["opzione"] == "zoom"
