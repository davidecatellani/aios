#!/usr/bin/env python3
"""Prepara il contenuto offline del prototipo. Metadati HTTPS, blob verificati e lock persistente."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile


MAX_METADATA = 128 * 1024**2


class HttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise ValueError("Redirect non HTTPS rifiutato")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(HttpsRedirect())


def metadata(url):
    with OPENER.open(url, timeout=60) as response:
        data = response.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise ValueError("Metadati troppo grandi")
    return json.loads(data)


def artifact(name, kind, url, sha256, size, **extra):
    if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Nome artefatto non valido")
    if urllib.parse.urlparse(url).scheme != "https" or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Ogni artefatto richiede HTTPS e SHA-256")
    if type(size) is not int or not 0 < size <= 3 * 1024**3:
        raise ValueError("Dimensione artefatto non valida")
    return {"name": name, "kind": kind, "url": url, "sha256": sha256, "size": size, **extra}


def ollama_model(doc):
    layers = [layer for layer in doc.get("layers", [])
              if layer.get("mediaType") == "application/vnd.ollama.image.model"]
    if len(layers) != 1:
        raise ValueError("Il manifest Ollama deve contenere un solo modello")
    layer = layers[0]
    digest = layer["digest"]
    if not digest.startswith("sha256:"):
        raise ValueError("Digest Ollama non supportato")
    return artifact("nova.gguf", "gguf", "https://registry.ollama.ai/v2/library/qwen2.5/blobs/" + digest,
                    digest[7:], layer["size"], model="Qwen2.5 1.5B Instruct", license="Apache-2.0")


def whisper_model(doc):
    revision = doc.get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Revisione del modello Whisper non valida")
    entries = [entry for entry in doc.get("siblings", []) if entry.get("rfilename") == "ggml-tiny.bin"]
    if len(entries) != 1:
        raise ValueError("Modello Whisper tiny non trovato")
    lfs = entries[0]["lfs"]
    return artifact("whisper.bin", "whisper",
                    f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{revision}/ggml-tiny.bin",
                    lfs["sha256"], lfs["size"], revision=revision, model="Whisper tiny multilingual", license="MIT")


def fdroid_app(doc, package, name):
    versions = doc["packages"][package]["versions"].values()
    compatible = [v for v in versions if int(v.get("manifest", {}).get("usesSdk", {}).get("minSdkVersion", 1)) <= 36
                  and not v.get("releaseChannels") and not v.get("antiFeatures")]
    if not compatible:
        raise ValueError(f"Nessuna versione stabile compatibile per {package}")
    version = max(compatible, key=lambda v: int(v["manifest"]["versionCode"]))
    file = version["file"]
    path = str(file["name"])
    if not re.fullmatch(r"/?[A-Za-z0-9_.-]+\.apk", path):
        raise ValueError("Percorso APK non valido")
    return artifact(name, "apk", "https://f-droid.org/repo/" + path.lstrip("/"), file["sha256"], file["size"],
                    package=package, version=version["manifest"].get("versionName", ""))


def validate_file(path, spec):
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False
    with path.open("rb") as stream:
        magic = stream.read(4)
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != spec["sha256"]:
        return False
    if spec["kind"] == "gguf" and magic != b"GGUF":
        return False
    if spec["kind"] == "whisper" and magic != b"lmgg":
        return False
    if spec["kind"] == "apk":
        try:
            with zipfile.ZipFile(path) as apk:
                return "AndroidManifest.xml" in apk.namelist() and "classes.dex" in apk.namelist()
        except zipfile.BadZipFile:
            return False
    return True


def download(spec, cache):
    dest = cache / spec["name"]
    if validate_file(dest, spec):
        return dest
    temp = cache / (spec["name"] + ".download")
    try:
        with OPENER.open(spec["url"], timeout=120) as response, temp.open("wb") as stream:
            remaining = spec["size"]
            while chunk := response.read(min(1024**2, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("Il download supera la dimensione dichiarata")
                stream.write(chunk)
        if not validate_file(temp, spec):
            raise ValueError(f"Impronta, formato o dimensione non validi: {spec['name']}")
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)
    return dest


def prepare(cache, output, refresh=False):
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    lock = cache / "prototype-lock.json"
    if lock.exists() and not refresh:
        doc = json.loads(lock.read_text())
        if doc.get("version") != 1 or not isinstance(doc.get("artifacts"), list):
            raise ValueError("Lock del prototipo non valido")
        specs = [artifact(**spec) for spec in doc["artifacts"]]
    else:
        text = ollama_model(metadata("https://registry.ollama.ai/v2/library/qwen2.5/manifests/1.5b-instruct"))
        voice = whisper_model(metadata("https://huggingface.co/api/models/ggerganov/whisper.cpp?blobs=true"))
        apps = metadata("https://f-droid.org/repo/index-v2.json")
        specs = [text, voice, fdroid_app(apps, "org.fdroid.fdroid", "FDroid.apk"),
                 fdroid_app(apps, "org.kde.kdeconnect_tp", "KDEConnect.apk")]
        # Il lock viene scritto solo dopo aver validato tutti i metadati.
        temp = lock.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "artifacts": specs}, indent=2))
        temp.replace(lock)
    for spec in specs:
        print(f"Preparo {spec['name']} ({spec['size'] / 1024**2:.0f} MiB)", flush=True)
        source = download(spec, cache)
        dest = output / spec["name"]
        if not validate_file(dest, spec):
            fd, temp = tempfile.mkstemp(prefix=".bundle-", dir=output)
            os.close(fd)
            try:
                shutil.copyfile(source, temp)
                os.replace(temp, dest)
            finally:
                Path(temp).unlink(missing_ok=True)
    manifest = output / "manifest.json"
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    local = [spec for spec in previous.get("artifacts", []) if spec.get("kind") == "voice-data"]
    manifest.write_text(json.dumps({"version": 1, "artifacts": specs + local}, indent=2))
    print("Contenuto offline verificato e pronto.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "packages/apps/Nova/bundle")
    parser.add_argument("--refresh", action="store_true", help="Sceglie nuove revisioni dai fornitori ufficiali")
    args = parser.parse_args()
    try:
        prepare(args.cache, args.output, args.refresh)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Preparazione prototipo interrotta: {exc}\n")


if __name__ == "__main__":
    main()
