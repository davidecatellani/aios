import io
import json
import urllib.error

from aios_copilot import registro, updates
from aios_copilot.tools.base import Runner


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(fail_registry=None):
    seen = []

    def open_(req):
        seen.append((req.full_url, dict(req.header_items())))
        if "api.github.com/user" in req.full_url:
            return Resp(json.dumps({"login": "davide"}).encode())
        if fail_registry and "ghcr.io" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, fail_registry, "no", {}, None)
        if "/token?" in req.full_url:
            return Resp(json.dumps({"token": "bearer123"}).encode())
        return Resp(b"{}")
    return open_, seen


def test_access_ok_and_handover(tmp_path):
    open_, seen = opener()
    a = registro.Access("DavideCatellani/aios", "ghp_x", open_)
    assert a.check("44") == ""
    assert seen[-1][0] == "https://ghcr.io/v2/davidecatellani/aios/manifests/44"
    drop = tmp_path / "auth.json"

    def service(_):  # il servizio di sistema prende il file
        assert json.loads(drop.read_text())["auths"]["ghcr.io"]["auth"]
        drop.unlink()
    assert a.hand_over(drop, sleep=service)


def test_fine_grained_token_is_explained():
    open_, _ = opener(fail_registry=403)
    assert "classico" in registro.Access("d/aios", "github_pat_x", open_).check("44")
    open_, _ = opener(fail_registry=404)
    assert "prossima anteprima" in registro.Access("d/aios", "x", open_).check("44")


def test_on_registry():
    st = json.dumps({"deployments": [{"booted": True, "container-image-reference":
                                      "ostree-unverified-registry:ghcr.io/d/aios:44"}]})
    assert registro.on_registry(st, "ghcr.io/d/aios:44")
    assert not registro.on_registry(json.dumps({"deployments": [{"booted": True, "container-image-reference":
                                                                  "ostree-unverified-image:oci-archive:/var/tmp/x"}]}),
                                    "ghcr.io/d/aios:44")
    assert registro.rebase_command("rpm-ostree", "ghcr.io/d/aios:44") == \
        ["rpm-ostree", "rebase", "ostree-unverified-registry:ghcr.io/d/aios:44"]


class FakeRunner(Runner):
    def __init__(self, outputs):
        super().__init__(which=lambda name: f"/usr/bin/{name}" if name == "rpm-ostree" else None)
        self.outputs, self.calls = outputs, []

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        for key, value in self.outputs.items():
            if " ".join(cmd).startswith(key):
                return value
        return 1, ""


class OkAccess:
    def check(self):
        return ""

    def hand_over(self):
        return True


def test_first_switch_then_incremental(monkeypatch):
    monkeypatch.setattr("aios_copilot.vault.load", lambda key: "ghp_x")
    updates.save_state({"repo": "d/aios"})
    monkeypatch.setattr("aios_copilot.registro.fedora_version", lambda *a: "44")
    status_old = json.dumps({"deployments": [{"booted": True, "container-image-reference": "oci-archive:/x"}]})
    runner = FakeRunner({"rpm-ostree status": (0, status_old), "rpm-ostree rebase": (0, "ok")})
    up = updates.Updates(runner, version=lambda: "2026.10.04.20", media=lambda: None, github=lambda: None)
    monkeypatch.setattr(up, "registry_access", lambda repo, token: OkAccess())
    found = [u for u in up.check() if u.kind == "sistema"]
    assert found[0].version == updates.REGISTRY_SWITCH
    assert "solo le differenze" in up.prepare(found)[0]
    assert ["rpm-ostree", "rebase", "ostree-unverified-registry:ghcr.io/d/aios:44"] in runner.calls

    status_new = json.dumps({"deployments": [{"booted": True, "container-image-reference":
                                              "ostree-unverified-registry:ghcr.io/d/aios:44"}]})
    runner.outputs = {"rpm-ostree status": (0, status_new), "rpm-ostree upgrade --check": (0, "Version: 2026.10.05.21")}
    found = [u for u in up.check() if u.kind == "sistema"]
    assert found[0].version == "2026.10.05.21" and "differenze" in found[0].summary
