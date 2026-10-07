"""Controlli e orchestrazione del PC: i processi Podman sono simulati, nessuna build AOSP."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PHONE = Path(__file__).resolve().parents[2] / "phone"


def executable(path, code):
    path.write_text(f"#!{sys.executable}\n" + code)
    path.chmod(0o755)


@pytest.fixture
def pc(tmp_path):
    repo = tmp_path / "repo"
    scripts = repo / "phone/scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(PHONE / "scripts/pc-build.sh", scripts / "pc-build.sh")
    tools = tmp_path / "bin"
    tools.mkdir()
    calls = tmp_path / "calls.jsonl"
    executable(tools / "podman", """import os, sys, json
with open(os.environ['FAKE_CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1] == os.environ.get('FAKE_FAIL_COMMAND'): sys.exit(17)
print('x86_64' if sys.argv[1] == 'info' else 'simulated container')
""")
    executable(tools / "python3", """import sys, os, shutil
from collections import namedtuple
usage = namedtuple('usage', 'total used free')
shutil.disk_usage = lambda _: usage(1000*1024**3, 0, int(os.environ.get('FAKE_FREE_GIB', '512'))*1024**3)
sys.argv = sys.argv[1:]
exec(sys.stdin.read())
""")
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "FAKE_CALLS": str(calls),
           "AIOS_PC_BUILD_DIR": str(tmp_path / "state"), "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}
    env.pop("AIOS_BUILD_JOBS", None)
    env.pop("AIOS_SYNC_JOBS", None)

    def run(action, **extra):
        result = subprocess.run(["bash", str(scripts / "pc-build.sh"), action], env={**env, **extra},
                                capture_output=True, text=True, timeout=10)
        recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, recorded

    return run, env


def test_check_does_not_download_or_start_a_container(pc):
    run, _ = pc
    result, calls = run("check", FAKE_FREE_GIB="29")
    assert result.returncode == 0, result.stderr
    assert [call[0] for call in calls] == ["info"]


@pytest.mark.parametrize("action", ["prepare", "build", "prototype"])
def test_builder_uses_isolated_mounts_and_four_jobs(pc, action):
    run, env = pc
    result, calls = run(action)
    assert result.returncode == 0, result.stderr
    assert [call[0] for call in calls] == ["info", "build", "run"]
    args = calls[-1]
    assert "--userns=keep-id" in args and "AIOS_BUILD_JOBS=4" in args
    assert args[-1] == action and "/work/aios/phone/scripts/container-build.sh" in args
    assert not any(word in str(args) for word in ("--privileged", "docker.sock", "/dev/bus/usb", "aios-chiavi"))
    assert (Path(env["AIOS_PC_BUILD_DIR"]) / "logs/123-1/build.log").exists()


def test_low_space_stops_before_container_build(pc):
    run, _ = pc
    result, calls = run("prepare", FAKE_FREE_GIB="29")
    assert result.returncode != 0 and "300 GiB" in result.stderr
    assert calls == []


def test_existing_checkout_can_resume_with_lower_disk_reserve(pc):
    run, env = pc
    repo_dir = Path(env["AIOS_PC_BUILD_DIR"]) / "sources/aosp-android-16.0.0_r3/.repo"
    repo_dir.mkdir(parents=True)
    result, calls = run("prepare", FAKE_FREE_GIB="50")
    assert result.returncode == 0, result.stderr
    assert [call[0] for call in calls] == ["info", "build", "run"]


@pytest.mark.parametrize("command", ["build", "run"])
def test_real_command_failure_is_not_hidden_by_log_capture(pc, command):
    run, _ = pc
    result, _ = run("build", FAKE_FAIL_COMMAND=command)
    assert result.returncode == 17


@pytest.mark.parametrize("jobs", ["0", "-1", "abc", "4; echo unsafe"])
def test_invalid_parallelism_is_rejected(pc, jobs):
    run, _ = pc
    result, calls = run("build", AIOS_BUILD_JOBS=jobs)
    assert result.returncode != 0 and not calls


def test_ndk_rejects_invalid_download_before_extraction(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    executable(tools / "curl", """import sys
from pathlib import Path
Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(b'corrupt archive')
""")
    root = tmp_path / "sdk"
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "AIOS_NDK_ROOT": str(root)}
    result = subprocess.run(["bash", str(PHONE / "scripts/install-ndk.sh")], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert not (root / "android-ndk-r28c").exists()
    assert not list(root.glob(".ndk-download.*"))


def test_ndk_existing_version_is_not_replaced(tmp_path):
    root = tmp_path / "sdk"
    dest = root / "android-ndk-r28c"
    dest.mkdir(parents=True)
    (dest / "source.properties").write_text("Pkg.Revision = 27.0.0\n")
    env = {**os.environ, "AIOS_NDK_ROOT": str(root)}
    result = subprocess.run(["bash", str(PHONE / "scripts/install-ndk.sh")], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and "non lo sovrascrivo" in result.stderr
    assert (dest / "source.properties").read_text() == "Pkg.Revision = 27.0.0\n"
