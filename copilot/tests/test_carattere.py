from aios_copilot import carattere
from aios_copilot.tools.themes import ThemesRouter

FAMILIES = {"Inter", "Atkinson Hyperlegible", "Lexend", "DejaVu Sans"}


def test_scale_steps_and_limits():
    assert carattere.parse_scale("più grande", 1.0) == 1.1
    assert carattere.parse_scale("molto più grande", 1.0) == 1.2
    assert carattere.parse_scale("più piccolo", 0.8) == 0.8
    assert carattere.parse_scale("al 120%", 1.0) == 1.2
    assert carattere.parse_scale("300%", 1.0) == 1.6
    assert carattere.parse_scale("normale", 1.4) == 1.0
    assert carattere.parse_scale("boh", 1.0) is None


def test_resolve_family():
    assert carattere.resolve_family("più leggibile", FAMILIES) == "Atkinson Hyperlegible"
    assert carattere.resolve_family("lexend", FAMILIES) == "Lexend"
    assert carattere.resolve_family("dejavu", FAMILIES) == "DejaVu Sans"
    assert carattere.resolve_family("Comic Sans", FAMILIES) is None


def test_set_appearance_saves_and_applies_to_gtk():
    applied = []
    msg = carattere.set_appearance("leggibile", "più grande", FAMILIES, gtk=applied.append)
    assert "Atkinson Hyperlegible" in msg and "110%" in msg
    assert carattere.load() == {"carattere": "Atkinson Hyperlegible", "scala": 1.1}
    assert applied == [{"carattere": "Atkinson Hyperlegible", "scala": 1.1}]
    assert "Non conosco" in carattere.set_appearance("Comic Sans", "", FAMILIES, gtk=applied.append)


def test_offered_only_installed():
    names = [c["famiglia"] for c in carattere.offered(FAMILIES)]
    assert names == ["Inter", "Atkinson Hyperlegible", "Lexend"]


def test_router():
    r = ThemesRouter()
    assert r.match("ingrandisci il testo").args == {"carattere": "", "dimensione": "più grande"}
    assert r.match("metti il testo al 120%").args["dimensione"] == "al 120%"
    assert r.match("usa il carattere più leggibile").args["carattere"] == "più leggibile"
    assert r.match("metti il tema marino").tool == "apply_theme"
