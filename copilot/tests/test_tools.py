from pathlib import Path

from aios_copilot.tools import apps, web
from aios_copilot.tools.base import Runner

DDG_HTML = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.trenitalia.com%2F&amp;rut=abc">Trenitalia &amp; orari</a>
  <a class="result__snippet" href="x">Orari dei <b>treni</b> per Milano</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://example.org/">Esempio</a>
</div>
"""


def test_parse_duckduckgo():
    results = web.parse_duckduckgo(DDG_HTML)
    assert results[0] == {
        "title": "Trenitalia & orari",
        "url": "https://www.trenitalia.com/",
        "snippet": "Orari dei treni per Milano",
    }
    assert results[1]["url"] == "https://example.org/"


def test_search_web_tool_uses_fetch_and_handles_errors():
    search_web, read_webpage = web.make_tools(fetch=lambda url: DDG_HTML)
    assert "https://www.trenitalia.com/" in search_web.func(query="treni milano")

    def broken(url):
        raise OSError("rete assente")

    search_web, _ = web.make_tools(fetch=broken)
    assert "rete assente" in search_web.func(query="x")


def test_search_falls_back_to_other_engines():
    bing = ('<ol><li class="b_algo"><h2><a href="https://www.ansa.it/">ANSA &amp; notizie</a></h2>'
            '<div><p>Le ultime <strong>notizie</strong></p></div></li></ol>')
    rss = ("<rss><channel><item><title><![CDATA[Il governo approva la manovra]]></title>"
           "<link>https://news.google.com/a/1</link><pubDate>Sun, 04 Oct 2026 20:00:00 GMT</pubDate>"
           '<source url="https://www.corriere.it">Corriere della Sera</source></item></channel></rss>')
    seen = []

    def fetch(url):
        seen.append(url.split("/")[2])
        if "bing" in url:
            return bing
        if "news.google" in url:
            return rss
        return "<html>verifica anti-robot</html>"  # DuckDuckGo che non dà risultati

    found = web.search("ricetta carbonara", fetch)
    assert found[0] == {"title": "ANSA & notizie", "url": "https://www.ansa.it/", "snippet": "Le ultime notizie"}
    assert seen == ["html.duckduckgo.com", "lite.duckduckgo.com", "www.bing.com"]
    news = web.search("notizie del giorno", fetch)
    assert news[0]["title"] == "Il governo approva la manovra" and "Corriere della Sera" in news[0]["snippet"]
    lite = ("<table><tr><td><a rel=\"nofollow\" href=\"https://www.meteo.it/\" class='result-link'>Meteo</a></td></tr>"
            "<tr><td class='result-snippet'>Previsioni per <b>Milano</b></td></tr></table>")
    assert web.parse_duckduckgo_lite(lite) == [{"title": "Meteo", "url": "https://www.meteo.it/", "snippet": "Previsioni per Milano"}]
    mojeek = '<ul><li><a class="title" href="https://example.it/">Esempio</a><p class="s">Testo</p></li></ul>'
    assert web.parse_mojeek(mojeek)[0]["url"] == "https://example.it/"


def test_html_to_text_skips_scripts_and_truncates():
    html = "<html><script>var x=1;</script><h1>Titolo</h1><p>Testo   utile</p></html>"
    assert web.html_to_text(html) == "Titolo Testo utile"
    assert web.html_to_text("<p>" + "a" * 50 + "</p>", max_chars=10).endswith("[…]")


def test_read_webpage_rejects_non_http():
    _, read_webpage = web.make_tools(fetch=lambda url: "")
    assert "non valido" in read_webpage.func(url="file:///etc/passwd")


def test_parse_package_searches():
    flatpak = "Name\tApplication ID\tDescription\nVLC\torg.videolan.VLC\tMedia player\n"
    assert apps.parse_flatpak_search(flatpak) == [
        {"name": "VLC", "id": "org.videolan.VLC", "description": "Media player", "source": "flatpak"}
    ]
    assert apps.parse_apt_search("vlc - multimedia player\nnot a line\n")[0]["id"] == "vlc"


class FakeRunner(Runner):
    def __init__(self, programs=("flatpak", "apt-get", "apt-cache"), outputs=None):
        super().__init__(which=lambda p: p if p in programs else None)
        self.outputs = outputs or {}
        self.ran, self.spawned = [], []

    def run(self, cmd):
        self.ran.append(cmd)
        return self.outputs.get(cmd[0], (0, ""))

    def spawn(self, cmd):
        self.spawned.append(cmd)


def tools_by_name(runner):
    return {t.name: t for t in apps.make_tools(runner)}


def test_install_commands_and_validation():
    runner = FakeRunner()
    tools = tools_by_name(runner)
    assert tools["install_app"].requires_confirmation
    assert tools["install_app"].func(app_id="org.videolan.VLC", source="flatpak") == "Installato org.videolan.VLC."
    # per l'utente (niente password di amministratore), con Flathub aggiunto se manca
    assert runner.ran[-1] == ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", "org.videolan.VLC"]
    assert ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", apps.FLATHUB] in runner.ran
    tools["install_app"].func(app_id="vlc", source="system")
    assert runner.ran[-1] == ["pkexec", "apt-get", "install", "-y", "vlc"]

    before = len(runner.ran)
    assert "non valido" in tools["install_app"].func(app_id="vlc; rm -rf /", source="system")
    assert "non valido" in tools["install_app"].func(app_id="--option", source="flatpak")
    assert "Sorgente non valida" in tools["install_app"].func(app_id="vlc", source="snap")
    assert len(runner.ran) == before


def test_install_failure_reports_output():
    runner = FakeRunner(outputs={"flatpak": (1, "error: No remote refs found")})
    result = tools_by_name(runner)["install_app"].func(app_id="org.foo.Bar", source="flatpak")
    assert "fallita" in result and "No remote refs" in result


def test_launch_app_via_desktop_entry(tmp_path: Path, monkeypatch):
    (tmp_path / "applications").mkdir()
    (tmp_path / "applications" / "firefox.desktop").write_text(
        "[Desktop Entry]\nName=Firefox\nExec=firefox %u\n"
    )
    monkeypatch.setattr(apps, "desktop_dirs", lambda: [tmp_path / "applications"])

    assert apps.exec_command(tmp_path / "applications" / "firefox.desktop") == ["firefox"]
    runner = FakeRunner(programs=("gtk-launch",))
    assert tools_by_name(runner)["launch_app"].func(name="firefox") == "Avviato firefox."
    assert runner.spawned == [["gtk-launch", "firefox"]]
    assert "Non trovo" in tools_by_name(runner)["launch_app"].func(name="inesistente")
