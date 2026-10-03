import time
from pathlib import Path

import pytest

from aios_copilot.agent import Agent
from aios_copilot.fastpath import FastPath, Intent, normalize, resolve_folder
from aios_copilot.tools import Tool
from aios_copilot.tools.base import params

INSTALLED = {"firefox", "vlc", "libreoffice writer"}
CATALOG = [
    {"name": "VLC", "id": "org.videolan.VLC", "description": "", "source": "flatpak"},
    {"name": "vlc", "id": "vlc", "description": "", "source": "system"},
    {"name": "GIMP", "id": "org.gimp.GIMP", "description": "", "source": "flatpak"},
]


@pytest.fixture
def fp(tmp_path: Path) -> FastPath:
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config/user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Scaricati"\n')
    return FastPath(
        find_desktop=lambda name: Path(name) if name in INSTALLED else None,
        find_apps=lambda q: [a for a in CATALOG if q in a["name"].lower()],
        home=tmp_path,
    )


def test_normalize_removes_politeness():
    assert normalize("Ehi, puoi aprire Firefox per favore?") == "aprire firefox"
    assert normalize("Mi apri  VLC!") == "apri vlc"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("apri firefox", Intent("launch_app", {"name": "firefox"})),
        ("Puoi avviare VLC?", Intent("launch_app", {"name": "vlc"})),
        ("open the firefox app", Intent("launch_app", {"name": "firefox"})),
        ("apri il sito repubblica.it", Intent("open_location", {"target": "https://repubblica.it"})),
        ("vai su repubblica.it", Intent("open_location", {"target": "https://repubblica.it"})),
        ("apri https://example.org/a?b=1", Intent("open_location", {"target": "https://example.org/a?b=1"})),
        ("installa VLC", Intent("install_app", {"app_id": "org.videolan.VLC", "source": "flatpak"})),
        ("vorrei installare gimp per favore", Intent("install_app", {"app_id": "org.gimp.GIMP", "source": "flatpak"})),
        ("cerca su internet orari treni Milano", Intent("search_web", {"query": "orari treni milano"})),
        ("cerca un programma per montare video", Intent("search_apps", {"query": "montare video"})),
        ("quanta memoria ho libera?", Intent("system_info", {})),
        ("informazioni sul sistema", Intent("system_info", {})),
    ],
)
def test_matches(fp, text, expected):
    assert fp.match(text) == expected


def test_open_folder_uses_localized_xdg_dir(fp, tmp_path):
    assert fp.match("apri la cartella dei download") == Intent("open_location", {"target": str(tmp_path / "Scaricati")})
    assert fp.match("apri documenti") == Intent("open_location", {"target": str(tmp_path / "Documents")})
    assert resolve_folder("HOME", tmp_path) == tmp_path


@pytest.mark.parametrize(
    "text",
    [
        "apri quel programma che usavo ieri",  # nessuna app con questo nome: decide l'LLM
        "installa un editor video",  # nessuna corrispondenza esatta nel catalogo
        "che tempo fa domani a Torino?",
        "info treni per Milano",
        "scrivi una mail a Marco",
        "apri appflowy",  # "app" non va tolto dall'inizio di una parola
    ],
)
def test_uncertain_requests_go_to_llm(fp, text):
    assert fp.match(text) is None


def test_fast_path_is_microseconds(fp):
    requests = ["apri firefox", "cerca meteo roma", "scrivi una mail a Marco", "apri la cartella download"] * 250
    start = time.perf_counter()
    for r in requests:
        fp.match(r)
    per_request = (time.perf_counter() - start) / len(requests)
    assert per_request < 0.001  # in pratica qualche microsecondo


class NoModel:
    def chat(self, messages, tools):
        raise AssertionError("il motore veloce non deve interpellare l'LLM")


def test_agent_answers_without_llm(fp):
    launched = []
    tools = [Tool("launch_app", "", params(name="n"), lambda name: launched.append(name) or f"Avviato {name}.")]
    agent = Agent(NoModel(), tools, confirm=lambda t, a: True, fastpath=fp)
    assert agent.ask("apri firefox") == "Avviato firefox."
    assert launched == ["firefox"]
    assert [m["role"] for m in agent.messages] == ["system", "user", "assistant"]


def test_agent_fast_path_still_asks_confirmation(fp):
    tools = [Tool("install_app", "", params(app_id="i", source="s"), lambda **a: "Installato.", requires_confirmation=True)]
    agent = Agent(NoModel(), tools, confirm=lambda t, a: False, fastpath=fp)
    assert agent.ask("installa vlc") == "Va bene, annullato."
