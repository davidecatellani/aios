"""Nuove versioni di AIOS da GitHub (accesso dell'utente) o da chiavetta, senza formattare."""

import hashlib
import io
import json
import urllib.error

import pytest

from aios_copilot import imageupdate as iu
from aios_copilot import updates as up
from aios_copilot.agent import Agent
from aios_copilot.tools import updates as update_tools

IMAGE = b"immagine-oci-" * 1000


def split(data, n=3):
    size = -(-len(data) // n)
    return [data[i:i + size] for i in range(0, len(data), size)]


def manifest(version="2026.10.04.7", data=IMAGE):
    parts = split(data)
    return {"versione": version, "fedora": "44", "sha256": hashlib.sha256(data).hexdigest(),
            "parti": [{"nome": f"{iu.ARCHIVE}.parte{i}", "sha256": hashlib.sha256(p).hexdigest(), "dimensione": len(p)}
                      for i, p in enumerate(parts)]}, parts


def stick(tmp_path, version="2026.10.04.7", data=IMAGE):
    folder = tmp_path / "media" / "CHIAVETTA"
    folder.mkdir(parents=True)
    m, parts = manifest(version, data)
    (folder / iu.MANIFEST).write_text(json.dumps(m))
    for spec, part in zip(m["parti"], parts):
        (folder / spec["nome"]).write_bytes(part)
    return folder


def test_manifest_is_validated():
    m, _ = manifest()
    assert iu.parse_manifest(m)[0] == "2026.10.04.7"
    for bad in ("../../etc/passwd", ".nascosto", "altro.bin"):
        m2 = json.loads(json.dumps(m))
        m2["parti"][0]["nome"] = bad
        with pytest.raises(ValueError):
            iu.parse_manifest(m2)
    with pytest.raises(ValueError):
        iu.parse_manifest({"versione": "1", "sha256": "x", "parti": []})


def test_versions_compare_as_numbers():
    assert iu.newer("2026.10.10.12", "2026.10.4.9") and not iu.newer("2026.10.04.7", "2026.10.04.7")
    assert iu.newer("2026.10.04.7", "") and not iu.newer("", "2026.1.1.1")


def test_usb_package_assembles_and_checks(tmp_path):
    folder = stick(tmp_path)
    pkg = iu.find_on_media([tmp_path / "media"])
    assert pkg and pkg.origin == "chiavetta" and pkg.version == "2026.10.04.7"
    archive = iu.assemble(pkg, tmp_path / "lavoro")
    assert archive.read_bytes() == IMAGE and not list((tmp_path / "lavoro").glob("*.parte*"))
    good = (folder / f"{iu.ARCHIVE}.parte1").read_bytes()
    (folder / f"{iu.ARCHIVE}.parte1").write_bytes(b"X" + good[1:])  # un byte cambiato
    pkg = iu.find_on_media([tmp_path / "media"])
    with pytest.raises(ValueError, match="rovinato"):
        iu.assemble(pkg, tmp_path / "lavoro2")


def test_stage_commands():
    assert iu.stage_command("rpm-ostree", iu.Path("/var/tmp/a.ociarchive")) == [
        "rpm-ostree", "rebase", "ostree-unverified-image:oci-archive:/var/tmp/a.ociarchive"]
    assert iu.stage_command("bootc", iu.Path("/x")) == ["bootc", "switch", "--transport", "oci-archive", "/x"]


class FakeGithub:
    """Le API di GitHub per un repository privato: senza il token giusto, 404."""

    def __init__(self, m, parts, token="giusto"):
        self.m, self.parts, self.token, self.requests = m, parts, token, []
        self.cut = True  # il primo scaricamento di un pezzo si interrompe a metà

    def __call__(self, req):
        self.requests.append(req)
        auth = req.unredirected_hdrs.get("Authorization", "")
        if auth != f"Bearer {self.token}":
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        assert "Authorization" not in req.headers  # mai inoltrato al server dei file
        url = req.full_url
        if url.endswith("/releases?per_page=10"):
            assets = [{"name": iu.MANIFEST, "url": "https://api.github.com/a/m"}] + [
                {"name": p["nome"], "url": f"https://api.github.com/a/{i}"} for i, p in enumerate(self.m["parti"])]
            return Resp(json.dumps([{"draft": True, "assets": []},
                                    {"tag_name": "anteprima-7", "draft": False, "assets": assets}]).encode())
        if url.endswith("/a/m"):
            return Resp(json.dumps(self.m).encode())
        if "/a/" in url:
            data = self.parts[int(url.rsplit("/", 1)[1])]
            rng = req.headers.get("Range")
            if rng:
                return Resp(data[int(rng.split("=")[1].rstrip("-")):], 206)
            if self.cut:
                self.cut = False
                return Resp(data[: len(data) // 2])
            return Resp(data)
        return Resp(b"{}")


class Resp(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_github_download_resumes_and_keeps_token_private(tmp_path):
    m, parts = manifest()
    gh = FakeGithub(m, parts)
    assert iu.GithubSource("davide/aios", "sbagliato", gh).check_access().startswith("il token non vede")
    src = iu.GithubSource("davide/aios", "giusto", gh)
    assert src.check_access() == ""
    pkg = src.latest()
    assert pkg.origin == "GitHub" and pkg.extra["release"] == "anteprima-7"
    with pytest.raises(OSError, match="interrotto"):
        iu.assemble(pkg, tmp_path / "w")
    assert iu.assemble(pkg, tmp_path / "w").read_bytes() == IMAGE
    assert any("Range" in r.headers for r in gh.requests)  # ripreso da metà, non da capo


class Runner:
    def __init__(self):
        self.ran = []

    def has(self, prog):
        return prog == "rpm-ostree"

    def run(self, cmd):
        self.ran.append(cmd)
        return 0, ""


def test_updates_prefers_usb_and_stages_without_formatting(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("AIOS_AGGIORNAMENTI_DIR", str(tmp_path / "lavoro"))
    stick(tmp_path, "2026.10.04.7")
    m, parts = manifest("2026.10.05.8")
    r = Runner()
    u = up.Updates(r, github=lambda: iu.GithubSource("davide/aios", "giusto", FakeGithub(m, parts)),
                   media=lambda: iu.find_on_media([tmp_path / "media"]), version=lambda: "2026.10.03.3")
    found = u.check()
    system = next(x for x in found if x.kind == "sistema")
    assert "2026.10.05.8" in system.summary and "GitHub" in system.summary  # la più recente vince
    assert u.check(("chiavetta",))[0].summary.startswith("AIOS 2026.10.04.7 (dalla chiavetta")
    report = u.prepare([u.check(("chiavetta",))[0]])
    assert "pronto per il prossimo riavvio" in report[0]
    staged = [c for c in r.ran if c[:2] == ["rpm-ostree", "rebase"]]
    assert staged and staged[0][2].endswith("aios-aggiornamento.ociarchive")
    assert not (tmp_path / "lavoro" / iu.ARCHIVE).exists()  # il file temporaneo non resta
    u2 = up.Updates(r, github=lambda: None, media=lambda: iu.find_on_media([tmp_path / "media"]),
                    version=lambda: "2026.10.04.7")
    assert not [x for x in u2.check() if x.kind == "sistema"]  # stessa versione: niente da fare


def test_connect_github_keeps_token_in_keyring(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("AIOS_NO_KEYRING", "1")
    m, parts = manifest()
    monkeypatch.setattr(iu.GithubSource, "__init__", lambda self, repo, token, opener=None: (
        setattr(self, "repo", repo), setattr(self, "token", token.strip()), setattr(self, "open", FakeGithub(m, parts)))
        and None)
    assert up.connect_github("sbagliato", "davide/aios").startswith("Non ha funzionato")
    assert up.connect_github(" giusto\n", "davide/aios").startswith("Collegato")
    from aios_copilot import vault

    assert vault.load(iu.TOKEN_KEY) == "giusto" and up.github_source().repo == "davide/aios"


def test_copilot_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("AIOS_AGGIORNAMENTI_DIR", str(tmp_path / "lavoro"))

    class NoModel:
        def chat(self, *a):
            raise AssertionError("niente LLM")

    stick(tmp_path)
    connected = []
    u = up.Updates(Runner(), github=lambda: None, media=lambda: iu.find_on_media([tmp_path / "media"]),
                   version=lambda: "2026.10.03.3")
    tools = update_tools.make_tools(Runner(), updates=u, secret=lambda title, text: "il-token",
                                    connect=lambda token: connected.append(token) or "Collegato.")
    agent = Agent(NoModel(), tools, confirm=lambda *a, **k: True, routers=[update_tools.UpdatesRouter()])
    assert agent.ask("collega github per gli aggiornamenti") == "Collegato." and connected == ["il-token"]
    monkeypatch.setattr("aios_copilot.agenda.notify", lambda title, body: None)
    assert "sottofondo" in agent.ask("aggiorna dalla chiavetta")
    up.Updates._worker.join(10)
    assert "pronto per il prossimo riavvio" in " ".join(up.load_state()["ultimo_esito"])
    no_dialog = update_tools.make_tools(Runner(), updates=u, secret=lambda t, x: None)
    agent = Agent(NoModel(), no_dialog, confirm=lambda *a, **k: True, routers=[update_tools.UpdatesRouter()])
    assert "qui in chat" in agent.ask("collega github")
    token = "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"
    connected.clear()
    pasted = update_tools.make_tools(Runner(), updates=u, secret=lambda t, x: None,
                                     connect=lambda tok: connected.append(tok) or "Collegato.")
    agent = Agent(NoModel(), pasted, confirm=lambda *a, **k: True, routers=[update_tools.UpdatesRouter()])
    assert agent.ask(f"ecco il token: {token}") == "Collegato." and connected == [token]
    assert token not in json.dumps(agent.messages)  # il modello non lo vedrà mai
    assert update_tools.UpdatesRouter().match("puoi collegare github per scaricare gli aggiornamenti?")


def test_secret_without_router_never_reaches_the_model():
    agent = Agent(type("M", (), {"chat": lambda *a: (_ for _ in ()).throw(AssertionError("niente LLM"))})(), [],
                  confirm=lambda *a, **k: True)
    answer = agent.ask("ghp_" + "a1" * 18)
    assert "token segreto" in answer and "ghp_" not in json.dumps(agent.messages)
