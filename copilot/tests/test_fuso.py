from aios_copilot import fuso
from aios_copilot.tools.fuso import TimezoneRouter


def test_guess_and_find():
    assert fuso.guess("it_IT.UTF-8") == "Europe/Rome"
    assert fuso.guess("en_GB.UTF-8") == "Europe/London"
    assert fuso.find("Londra") == "Europe/London"
    assert fuso.find("ora italiana") == "Europe/Rome"
    assert fuso.find("Asia/Tokyo") == "Asia/Tokyo"
    assert fuso.find("atlantide") == ""


def test_ensure_fixes_utc_only(monkeypatch):
    monkeypatch.setenv("LANG", "it_IT.UTF-8")  # il fuso si deduce dalla lingua: non da quella della macchina di prova
    monkeypatch.delenv("LC_TIME", raising=False)
    done = []
    assert fuso.ensure(apply=lambda z: done.append(z) or True, now=lambda: "Etc/UTC") == fuso.guess()
    assert done == [fuso.guess()]
    assert fuso.ensure(apply=lambda z: done.append(z) or True, now=lambda: "Europe/Paris") == "Europe/Paris"
    assert len(done) == 1


def test_router():
    r = TimezoneRouter()
    assert r.match("metti l'ora italiana").args == {"luogo": "italiana"}
    assert r.match("imposta il fuso orario di Londra").tool == "set_timezone"
    assert r.match("che fuso orario ho?").tool == "get_timezone"
    assert r.match("metti la musica") is None
