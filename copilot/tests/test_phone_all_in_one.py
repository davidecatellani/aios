"""compila-telefono.sh: il comando unico per il PC. Compilazione, spazio e telefono simulati."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


PHONE = Path(__file__).resolve().parents[2] / "phone"
GIB_KIB = 1024 * 1024


def executable(path, code):
    path.write_text(code)
    path.chmod(0o755)


FAKE_PC_BUILD = """#!/usr/bin/env bash
set -eu
echo "$AIOS_SESSION_HOURS $*" >> "$FAKE_CALLS"
mkdir -p "$AIOS_PC_BUILD_DIR/logs/run-1"
case "$FAKE_OUTCOME" in
  completed)
    out="$(dirname "$0")/../uscita/gsi/prototipo"
    mkdir -p "$out"
    echo '{"images": [{"name": "system.img", "size": 3221225472, "sha256": "abc123"}]}' > "$out/prototipo.json"
    echo '{"state": "completed"}' > "$AIOS_PC_BUILD_DIR/logs/run-1/session.json" ;;
  paused)
    echo '{"state": "paused", "reason": "time_limit", "exit_code": 75}' > "$AIOS_PC_BUILD_DIR/logs/run-1/session.json" ;;
  failed)
    echo '{"state": "failed"}' > "$AIOS_PC_BUILD_DIR/logs/run-1/session.json"
    exit 17 ;;
  interrupted)
    echo '{"state": "paused", "reason": "interrupted", "exit_code": 143}' > "$AIOS_PC_BUILD_DIR/logs/run-1/session.json"
    exit 143 ;;
esac
"""

FAKE_PHONE_CHECK = """import json
print(json.dumps({"proprieta": {"ro.product.model": "motorola edge 50 neo", "ro.build.version.release": "16",
    "ro.build.version.sdk": "36"}, "bootloader": {"sbloccato": True}, "prerequisiti_gsi_rilevati": True,
    "blocchi_installazione": []}))
"""


def make_repo(root):
    (root / "phone/scripts").mkdir(parents=True)
    shutil.copyfile(PHONE / "compila-telefono.sh", root / "phone/compila-telefono.sh")
    executable(root / "phone/scripts/pc-build.sh", FAKE_PC_BUILD)
    (root / "phone/scripts/verifica-telefono.py").write_text(FAKE_PHONE_CHECK)


@pytest.fixture
def pc(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo)
    tools = tmp_path / "bin"
    tools.mkdir()
    executable(tools / "df", '#!/bin/sh\nfree="$FAKE_FREE_KIB"\n'
               '[ "$2" = "$XDG_RUNTIME_DIR" ] && free="$FAKE_RUN_FREE_KIB"\n'
               'echo "Filesystem 1024-blocks Used Available Capacity Mounted"\n'
               'echo "/dev/fake 1 1 $free 1% /"\n')
    executable(tools / "du", '#!/bin/sh\nprintf "%s\\t%s\\n" "$FAKE_USED_KIB" "$3"\n')
    # Prende il blocco della sospensione e lancia il comando che segue le opzioni.
    executable(tools / "systemd-inhibit", '#!/bin/sh\nwhile [ "${1#--}" != "$1" ]; do shift; done\nexec "$@"\n')
    calls = tmp_path / "calls.txt"
    state = tmp_path / "state"
    runtime = tmp_path / "run"
    runtime.mkdir()
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "HOME": str(tmp_path),
           "AIOS_PC_BUILD_DIR": str(state), "FAKE_CALLS": str(calls), "FAKE_OUTCOME": "completed",
           "FAKE_FREE_KIB": str(500 * GIB_KIB), "FAKE_USED_KIB": "0",
           "XDG_RUNTIME_DIR": str(runtime), "FAKE_RUN_FREE_KIB": str(1024 * 1024)}
    env.pop("AIOS_SESSION_HOURS", None)

    def run(*args, script=repo / "phone/compila-telefono.sh", **extra):
        result = subprocess.run(["bash", str(script), *args], env={**env, **extra},
                                capture_output=True, text=True, timeout=30)
        recorded = calls.read_text().splitlines() if calls.exists() else []
        return result, recorded

    return run, repo, state, tools


def test_completed_build_lists_images_and_keeps_a_log(pc):
    run, repo, state, _ = pc
    result, calls = run("--senza-aggiornare")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["8 prototype"]
    assert "FATTO" in result.stdout and "abc123" in result.stdout and "NON installarle" in result.stdout
    logs = list((state / "logs").glob("compila-telefono-*.log"))
    assert len(logs) == 1 and "FATTO" in logs[0].read_text()


def test_paused_session_says_image_is_not_ready_and_how_to_continue(pc):
    run, *_ = pc
    result, _ = run("--senza-aggiornare", "--ore", "4", FAKE_OUTCOME="paused")
    assert result.returncode == 0
    assert "NON è ancora pronta" in result.stdout and "bash phone/compila-telefono.sh" in result.stdout


def test_build_error_is_an_error_and_points_to_the_logs(pc):
    run, _, state, _ = pc
    result, _ = run("--senza-aggiornare", FAKE_OUTCOME="failed")
    assert result.returncode == 17
    assert "ERRORE" in result.stdout and str(state / "logs/run-1/build.log") in result.stdout


def test_outside_stop_is_reported_as_interruption_not_as_error(pc):
    run, *_ = pc
    result, _ = run("--senza-aggiornare", FAKE_OUTCOME="interrupted")
    assert result.returncode == 143
    assert "INTERROTTA" in result.stdout and "ERRORE" not in result.stdout


def test_session_hours_are_passed_and_validated(pc):
    run, *_ = pc
    result, calls = run("--senza-aggiornare", "--ore", "0")
    assert result.returncode == 0 and calls == ["0 prototype"]
    result, calls = run("--senza-aggiornare", "--ore", "25")
    assert result.returncode == 2 and calls == ["0 prototype"]


def test_not_enough_space_for_a_first_download_stops_before_building(pc):
    run, *_ = pc
    result, calls = run("--senza-aggiornare", FAKE_FREE_KIB=str(200 * GIB_KIB))
    assert result.returncode == 1 and calls == []
    assert "liberane altri 100" in result.stdout


def test_started_checkout_counts_what_is_already_on_disk(pc):
    run, _, state, _ = pc
    (state / "sources/aosp-android-16.0.0_r3/.repo").mkdir(parents=True)
    # Il controllo di pc-build.sh qui chiederebbe solo 30 GiB: non basta per finire.
    result, calls = run("--senza-aggiornare", FAKE_FREE_KIB=str(100 * GIB_KIB), FAKE_USED_KIB=str(150 * GIB_KIB))
    assert result.returncode == 1 and calls == []
    assert "liberane altri 50" in result.stdout
    result, calls = run("--senza-aggiornare", FAKE_FREE_KIB=str(100 * GIB_KIB), FAKE_USED_KIB=str(250 * GIB_KIB))
    assert result.returncode == 0 and calls == ["8 prototype"]


def test_full_runtime_dir_stops_before_podman_with_a_clear_fix(pc):
    # crun scrive lì la configurazione del contenitore: piena, «No space left on device».
    run, *_ = pc
    result, calls = run("--senza-aggiornare", FAKE_RUN_FREE_KIB="0")
    assert result.returncode == 1 and calls == []
    assert "Riavvia il PC" in result.stdout


def test_space_check_can_be_skipped_on_purpose(pc):
    run, *_ = pc
    result, calls = run("--senza-aggiornare", "--ignora-spazio", FAKE_FREE_KIB=str(10 * GIB_KIB))
    assert result.returncode == 0 and calls == ["8 prototype"]


def test_connected_phone_is_read_and_report_saved(pc):
    run, repo, _, tools = pc
    executable(tools / "adb", "#!/bin/sh\nexit 0\n")
    result, _ = run("--senza-aggiornare")
    assert result.returncode == 0
    assert "Bootloader sbloccato: sì" in result.stdout
    assert json.loads((repo / "phone/uscita/verifica-telefono.json").read_text())["bootloader"]["sbloccato"] is True


def test_update_restarts_with_the_new_version_of_the_script(pc, tmp_path):
    run, *_ = pc
    git = ["git", "-c", "user.name=prova", "-c", "user.email=prova@localhost", "-c", "init.defaultBranch=main"]
    origin = tmp_path / "origin"
    make_repo(origin)
    subprocess.run([*git, "init", "-q", str(origin)], check=True)
    subprocess.run([*git, "-C", str(origin), "add", "."], check=True)
    subprocess.run([*git, "-C", str(origin), "commit", "-qm", "prima"], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    script = origin / "phone/compila-telefono.sh"
    script.write_text(script.read_text().replace("set -euo pipefail\n", "set -euo pipefail\necho NUOVA-VERSIONE\n", 1))
    subprocess.run([*git, "-C", str(origin), "commit", "-qam", "correzione"], check=True)

    result, calls = run(script=clone / "phone/compila-telefono.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NUOVA-VERSIONE" in result.stdout and calls == ["8 prototype"]
