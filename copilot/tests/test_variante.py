import io
import json

from aios_copilot import imageupdate
from aios_copilot.imageupdate import GithubSource, same_variant
from aios_copilot.registro import image_ref
from aios_copilot.tools.updates import UpdatesRouter


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def manifest(version, variante=None):
    m = {"versione": version, "fedora": "44", "sha256": "a" * 64,
         "parti": [{"nome": "aios-aggiornamento.ociarchive.parte0", "sha256": "b" * 64, "dimensione": 10}]}
    if variante:
        m["variante"] = variante
    return m


def test_updates_stay_on_the_installed_variant(monkeypatch):
    releases = [
        {"tag_name": "anteprima-nvidia-28", "assets": [{"name": "aios-aggiornamento.json", "url": "https://x/n"},
                                                       {"name": "aios-aggiornamento.ociarchive.parte0", "url": "https://x/p"}]},
        {"tag_name": "anteprima-27", "assets": [{"name": "aios-aggiornamento.json", "url": "https://x/s"},
                                                {"name": "aios-aggiornamento.ociarchive.parte0", "url": "https://x/p"}]},
    ]
    files = {"https://x/n": manifest("2026.10.06.28", "nvidia"), "https://x/s": manifest("2026.10.06.27")}

    def opener(req):
        url = req.full_url
        return _Resp(json.dumps(releases if "/releases" in url else files[url]).encode())

    monkeypatch.setattr(imageupdate, "installed_variant", lambda path=None: "standard")
    assert GithubSource("x/aios", opener=opener).latest().version == "2026.10.06.27"
    monkeypatch.setattr(imageupdate, "installed_variant", lambda path=None: "nvidia")
    assert GithubSource("x/aios", opener=opener).latest().version == "2026.10.06.28"
    assert same_variant({}, "standard") and not same_variant({"variante": "nvidia"}, "standard")
    # l'immagine universale va a tutti; chi ce l'ha non torna a quella senza driver
    assert all(same_variant({"variante": "universale"}, v) for v in ("standard", "nvidia", "universale"))
    assert same_variant({"variante": "nvidia"}, "universale") and not same_variant({"variante": "standard"}, "universale")


def test_registry_tag_and_voice():
    assert image_ref("Davide/AIOS", "44", "nvidia") == "ghcr.io/davide/aios:44-nvidia"
    assert image_ref("davide/aios", "44", "standard") == "ghcr.io/davide/aios:44"
    r = UpdatesRouter()
    assert r.match("passa alla versione nvidia").args == {"variante": "nvidia"}
    assert r.match("torna alla versione standard").args == {"variante": "standard"}
    assert r.match("installa i driver nvidia").tool == "switch_system_variant"
