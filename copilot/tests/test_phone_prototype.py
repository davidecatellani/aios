"""Bundle verificato e pipeline unica; le fixture non sono modelli o APK installabili."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("prototype", ROOT / "phone/scripts/prepara-prototipo.py")
prototype = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prototype)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def apk():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"synthetic manifest")
        archive.writestr("classes.dex", b"synthetic dex")
    return output.getvalue()


@pytest.fixture
def provider(monkeypatch):
    text, voice, app = b"GGUFsynthetic fixture", b"lmggsynthetic fixture", apk()
    blobs = {
        "https://registry.ollama.ai/v2/library/qwen2.5/blobs/sha256:" + digest(text): text,
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/" + "a" * 40 + "/ggml-tiny.bin": voice,
        "https://f-droid.org/repo/app.apk": app,
    }
    model = {"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:" + digest(text), "size": len(text)}]}
    speech = {"sha": "a" * 40, "siblings": [{"rfilename": "ggml-tiny.bin", "lfs": {"sha256": digest(voice), "size": len(voice)}}]}
    version = {"manifest": {"versionCode": 123, "versionName": "1", "usesSdk": {"minSdkVersion": 23}},
               "file": {"name": "/app.apk", "size": len(app), "sha256": digest(app)}}
    apps = {"packages": {pkg: {"versions": {"stable": version}} for pkg in ("org.fdroid.fdroid", "org.kde.kdeconnect_tp")}}
    requests = []
    class FakeProvider:
        def open(self, url, timeout):
            requests.append(url)
            return io.BytesIO(blobs[url])
    def metadata(url):
        if "manifests/" in url:
            return model
        if "api/models/" in url:
            return speech
        return apps
    monkeypatch.setattr(prototype, "OPENER", FakeProvider())
    monkeypatch.setattr(prototype, "metadata", metadata)
    return blobs, requests, apps


def test_complete_bundle_is_locked_and_can_resume_offline(tmp_path, provider, monkeypatch):
    blobs, requests, _ = provider
    cache, output = tmp_path / "cache", tmp_path / "bundle"
    prototype.prepare(cache, output)
    manifest = json.loads((output / "manifest.json").read_text())
    assert {a["name"] for a in manifest["artifacts"]} == {"nova.gguf", "whisper.bin", "FDroid.apk", "KDEConnect.apk"}
    for item in manifest["artifacts"]:
        assert prototype.validate_file(output / item["name"], item)
    lock = (cache / "prototype-lock.json").read_bytes()
    monkeypatch.setattr(prototype, "metadata", lambda _: pytest.fail("Le revisioni sono già fissate"))
    requests.clear()
    prototype.prepare(cache, output)
    assert not requests
    assert (cache / "prototype-lock.json").read_bytes() == lock


def test_corrupt_download_does_not_replace_a_previous_file(tmp_path, provider):
    blobs, _, _ = provider
    url = next(iter(blobs))
    original = blobs[url]
    item = prototype.artifact("nova.gguf", "gguf", url, digest(original), len(original))
    target = tmp_path / "nova.gguf"
    target.write_bytes(b"previous file")
    blobs[url] = b"X" * len(original)
    with pytest.raises(ValueError, match="Impronta"):
        prototype.download(item, tmp_path)
    assert target.read_bytes() == b"previous file"
    assert not (tmp_path / "nova.gguf.download").exists()


def test_unsigned_formats_wrong_sdk_and_unsafe_paths_are_rejected(tmp_path, provider):
    _, _, apps = provider
    with pytest.raises(ValueError):
        prototype.artifact("../model", "gguf", "https://example.org/a", "a" * 64, 1)
    with pytest.raises(ValueError):
        prototype.artifact("model", "gguf", "http://example.org/a", "a" * 64, 1)
    version = apps["packages"]["org.fdroid.fdroid"]["versions"]["stable"]
    version["manifest"]["usesSdk"]["minSdkVersion"] = 37
    with pytest.raises(ValueError, match="compatibile"):
        prototype.fdroid_app(apps, "org.fdroid.fdroid", "app.apk")
    file = tmp_path / "app.apk"
    file.write_bytes(b"not an apk")
    item = prototype.artifact("app.apk", "apk", "https://example.org/a", digest(file.read_bytes()), file.stat().st_size)
    assert not prototype.validate_file(file, item)


def test_metadata_redirect_cannot_downgrade_tls():
    with pytest.raises(ValueError, match="HTTPS"):
        prototype.HttpsRedirect().redirect_request(None, None, 302, "redirect", {}, "http://example.org/a")


def test_single_command_runs_preparation_and_build_in_order():
    script = (ROOT / "phone/scripts/container-build.sh").read_text()
    branch = script.split("prototype)", 1)[1].split(";;", 1)[0]
    assert branch.index('bash "$0" prepare') < branch.index('bash "$0" build')
    assert "google-android.sh" in script and "voce-android.sh" in script and "prepara-prototipo.py" in script


def test_prototype_export_preserves_previous_images_on_failed_validation(tmp_path):
    module_spec = importlib.util.spec_from_file_location("export", ROOT / "phone/scripts/esporta-prototipo.py")
    exporter = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(exporter)
    output = tmp_path / "out"
    output.mkdir()
    (output / "system.img").write_bytes(b"previous image")
    source = tmp_path / "broken.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("IMAGES/system.img", b"incomplete image")
    with pytest.raises(ValueError, match="incompleto"):
        exporter.export(source, output)
    assert (output / "system.img").read_bytes() == b"previous image"
    assert not (output / "prototipo.json").exists()
