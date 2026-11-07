import base64
import hashlib
import io
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from aios_copilot import modelcatalog, themeapply, thememarket, themes
from aios_copilot.agent import Agent
from aios_copilot.themes import MIN_CONTRAST, contrast
from aios_copilot.tools import themes as theme_tools
from aios_copilot.tools.base import Runner


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def readable(theme: themes.Theme, minimum: float = MIN_CONTRAST) -> bool:
    return all(contrast(p.text, p.surface) >= minimum and contrast(p.text, p.bg) >= minimum
               and contrast(p.muted, p.surface) >= minimum and contrast(p.on_accent, p.accent) >= MIN_CONTRAST
               for p in (theme.light, theme.dark))


@pytest.mark.parametrize("description", [
    "stile marino", "un tema autunnale", "crea un tema dragon ball", "spazio profondo", "neon anni 80", "bosco di montagna",
    "tramonto nel deserto", "primavera pastello", "inverno con la neve", "sobrio per l'ufficio",
    "giardino giapponese zen", "il mio gatto arancione", "xyz", "",
])
def test_every_theme_is_readable(description):
    assert readable(themes.from_description(description))


def test_high_contrast_is_stronger():
    t = themes.from_description("alto contrasto per mia nonna")
    assert readable(t, 7.0) and t.font == "Atkinson Hyperlegible"


def test_franchise_themes_are_personal_only():
    t = themes.from_description("crea un tema in stile dragon ball")
    assert t.personal_only and t.name == "Energia (ispirato a Dragon Ball)"
    assert not themes.from_description("stile marino").personal_only


def test_llm_palette_is_validated():
    good = '{"name": "Zen", "base": "#4a7c59", "second": "#c9b79c", "style": "foglie", "radius": 99, "font": "Comic Neue"}'
    t = themes.from_description("giardino zen al tramonto?", ask_llm=lambda p: "Ecco:\n" + good)
    assert t.name == "Zen" or t.name == "Tramonto"  # «tramonto» è un'atmosfera conosciuta: vince la tabella
    t = themes.from_description("qualcosa di unico", ask_llm=lambda p: good)
    assert t.name == "Zen" and t.radius == 24 and t.wallpaper["style"] == "foglie" and readable(t)
    junk = themes.from_description("qualcosa di unico", ask_llm=lambda p: '{"base": "rosso", "second": "<script>"}')
    assert readable(junk)  # risposta non valida: si ripiega senza errori


def test_remix_directions():
    sea = themes.from_description("stile marino")
    dark = themes.remix(sea, "più scuro")
    assert themes.luminance(dark.light.bg) < themes.luminance(sea.light.bg) and readable(dark)
    warm, cool = themes.remix(sea, "più caldo"), themes.remix(sea, "più freddo")
    assert themes.hls(warm.light.accent)[0] > themes.hls(sea.light.accent)[0] > themes.hls(cool.light.accent)[0]
    with pytest.raises(ValueError):
        themes.remix(sea, "più croccante")


def test_wallpapers_are_safe_svg():
    for style in themes.STYLES:
        svg = themes.wallpaper_svg({"style": style, "colors": ["#0b6e99", "#e9c46a", "#2ec4b6"], "seed": 3})
        root = ET.fromstring(svg)  # XML valido
        assert "script" not in svg.lower() and "href" not in svg and root.get("width") == "1920"
    evil = themes.wallpaper_svg({"style": "onde", "colors": ['"/><script>alert(1)</script>', "#000000"]})
    assert "script" not in evil


def png(path: Path, color: str) -> Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x64",
                    "-frames:v", "1", str(path)], check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="serve ffmpeg")
def test_theme_from_drawing(tmp_path):
    drawing = png(tmp_path / "disegno.png", "0xE76F51")
    t = themes.from_image(drawing)
    assert themes.hls(t.light.accent)[0] < 0.1 and readable(t)  # arancio come il disegno
    folder = themes.save(t, drawing)
    assert (folder / "wallpaper.png").exists() and t.wallpaper == {"kind": "immagine", "file": "wallpaper.png"}


def test_dominant_colors():
    pixels = [(10, 100, 200)] * 70 + [(240, 200, 100)] * 30
    colors = themes.dominant_colors(pixels, k=3)
    assert colors[0][0] == "#0a64c8" and round(colors[0][1], 1) == 0.7


class FakeRunner(Runner):
    def __init__(self, programs=("gsettings",)):
        super().__init__(which=lambda p: p if p in programs else None)
        self.ran = []

    def run(self, cmd):
        self.ran.append(cmd)
        return 0, "'default'"


def test_apply_preserves_user_gtk_css_and_remembers_previous(tmp_path):
    user_css = tmp_path / "config/gtk-4.0/gtk.css"
    user_css.parent.mkdir(parents=True)
    user_css.write_text("/* mia regola */\nwindow { padding: 2px; }\n")
    r = FakeRunner()
    sea = themes.from_description("stile marino")
    themeapply.apply(sea, r)
    text = user_css.read_text()
    assert "mia regola" in text and f"@define-color accent_bg_color {sea.light.accent};" in text
    assert ["gsettings", "set", "org.gnome.desktop.interface", "accent-color", "blue"] in r.ran
    assert any(c[3] == "picture-uri" and c[4].endswith("wallpaper.svg") for c in r.ran if len(c) > 4)
    autumn = themes.from_description("autunno")
    themeapply.apply(autumn, r)
    assert user_css.read_text().count(themeapply.START) == 1  # il blocco si sostituisce, non si accumula
    assert themeapply.previous().id == sea.id
    themeapply.reset(r)
    assert user_css.read_text().strip() == "/* mia regola */\nwindow { padding: 2px; }"
    assert "Tema SoIA: SoIA" in themeapply.current_css()


def test_kde_scheme():
    r = FakeRunner(("plasma-apply-colorscheme", "plasma-apply-wallpaperimage"))
    t = themes.from_description("bosco")
    assert "KDE" in themeapply.apply(t, r)
    assert ["plasma-apply-colorscheme", f"AIOS{t.id}"] in r.ran


# --- market --------------------------------------------------------------------------------------------


def package(doc: dict, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.json", json.dumps(doc))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def theme_doc(**changes) -> dict:
    doc = json.loads(themes.from_description("stile marino").to_json())
    doc.update(changes)
    return doc


def test_market_package_validation():
    t = thememarket.install_package(package(theme_doc(id="mare-calmo", name="Mare calmo")))
    assert t.origin == "market" and themes.load("mare-calmo").name == "Mare calmo"
    bad = {
        "svg": package(theme_doc(id="a"), {"wallpaper.svg": b"<svg onload='x'/>"}),
        "codice": package(theme_doc(id="b"), {"script.py": b"import os"}),
        "percorso": package(theme_doc(id="c"), {"../../.bashrc": b"rm -rf ~"}),
        "colore": package(theme_doc(id="d", light={**theme_doc()["light"], "accent": "red; background:url(x)"})),
        "id": package(theme_doc(id="../evil")),
        "finta immagine": package(theme_doc(id="e", wallpaper={"kind": "immagine", "file": "wallpaper.png"}),
                                  {"wallpaper.png": b"<html>"}),
    }
    for why, data in bad.items():
        with pytest.raises(thememarket.ThemeError):
            thememarket.install_package(data)
    data = package(theme_doc(id="f"))
    with pytest.raises(thememarket.ThemeError, match="impronta"):
        thememarket.install_package(data, "0" * 64)


def test_export_and_market_rules(tmp_path):
    personal = themes.from_description("tema dragon ball")
    themes.save(personal)
    with pytest.raises(thememarket.ThemeError, match="uso personale"):
        thememarket.export_package(personal, for_market=True)
    data = thememarket.export_package(personal)  # per un amico sì
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["theme.json"]
    again = thememarket.install_package(data, origin="amico")
    assert again.personal_only


def test_signed_market_index(tmp_path):
    pem = tmp_path / "k.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(pem)], check=True, capture_output=True)
    pub = base64.b64decode(modelcatalog.public_key(pem))
    pkg = package(theme_doc(id="mare-calmo", name="Mare calmo"))
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"themes": [{"id": "mare-calmo", "name": "Mare calmo", "author": "Anna", "description": "blu e sabbia",
                                             "tags": ["mare", "estate"], "url": "https://m/mare.aiostheme",
                                             "sha256": hashlib.sha256(pkg).hexdigest()}]}))
    sig = modelcatalog.sign_file(index, pem).read_bytes()
    served = {"https://m/i.json": index.read_bytes(), "https://m/i.json.sig": sig, "https://m/mare.aiostheme": pkg}
    assert thememarket.update_index("https://m/i.json", served.__getitem__, keys=[pub]) == 1
    with pytest.raises(thememarket.ThemeError):
        thememarket.update_index("https://m/i.json", served.__getitem__, keys=[bytes(32)])
    assert thememarket.search("mare")[0].author == "Anna"
    assert thememarket.install_listing("mare-calmo", served.__getitem__).name == "Mare calmo"


# --- dal copilota ----------------------------------------------------------------------------------------


def test_copilot_creates_and_retouches_themes_instantly():
    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM per le frasi sui temi")

    r = FakeRunner()
    tools = theme_tools.make_tools(runner=r)
    agent = Agent(NoModel(), tools, confirm=lambda *a, **k: True, routers=[theme_tools.ThemesRouter()])
    assert agent.ask("crea un tema in stile marino").startswith("Tema «Marino» applicato")
    assert agent.ask("più scuro").startswith("Tema «Marino · più scuro» applicato")
    assert agent.ask("torna al tema di prima") == "Ripristinato il tema «Marino»."
    assert "● Marino" in agent.ask("i miei temi")
    assert "uso personale" in agent.ask("fammi un tema dragon ball")


def test_apps_receive_the_theme(tmp_path):
    import http.client

    from aios_copilot.localapp import LocalApp, serve

    themeapply.apply(themes.from_description("stile marino"), FakeRunner())

    class App(LocalApp):
        page = tmp_path / "p.html"

    (tmp_path / "p.html").write_text("<html></html>")
    server, _ = serve(App())
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("GET", "/theme.css", headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    assert resp.status == 200 and b"Tema SoIA: Marino" in resp.read()
    server.shutdown()
