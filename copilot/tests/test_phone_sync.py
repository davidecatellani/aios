"""sincronizza.sh: i progetti che non arrivano si riprovano da soli. repo è simulato."""

import json
import os
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[2] / "phone/scripts/sincronizza.sh"
CLANG = "platform/prebuilts/clang/host/linux-x86"
RESET = (f"error: Cannot fetch {CLANG} from https://android.googlesource.com/{CLANG}\n"
         "stdout: error: RPC failed; curl 56 Recv failure: Connection reset by peer\n")


def run(tmp_path, plan):
    """plan: per ogni chiamata a repo, [codice di uscita, output]."""
    tools = tmp_path / "bin"
    tools.mkdir()
    fake = tools / "repo"
    fake.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
calls = Path(os.environ['FAKE_CALLS'])
done = calls.read_text().splitlines() if calls.exists() else []
calls.write_text('\\n'.join(done + [' '.join(sys.argv[1:])]) + '\\n')
code, output = json.loads(os.environ['FAKE_PLAN'])[len(done)]
sys.stdout.write(output)
sys.exit(code)
""")
    fake.chmod(0o755)
    calls = tmp_path / "calls"
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "FAKE_CALLS": str(calls),
           "FAKE_PLAN": json.dumps(plan), "AIOS_SYNC_JOBS": "4"}
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20)
    return result, calls.read_text().splitlines() if calls.exists() else []


def test_complete_sync_needs_nothing_else(tmp_path):
    result, calls = run(tmp_path, [[0, "ok\n"]])
    assert result.returncode == 0
    assert calls == ["sync -c --no-tags --optimized-fetch --retry-fetches=4 -j4"]


def test_project_reset_by_the_network_is_retried_alone_then_files_are_extracted(tmp_path):
    result, calls = run(tmp_path, [[1, RESET], [0, "ok\n"], [0, ""]])
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[1] == f"sync -c --no-tags --optimized-fetch --retry-fetches=4 -j1 {CLANG}"
    assert calls[2] == "sync --local-only -j4"
    assert f"Mancano 1 progetti ({CLANG})" in result.stdout


def test_gives_up_after_the_rounds_without_hiding_the_error(tmp_path):
    result, calls = run(tmp_path, [[1, RESET]] * 4)
    assert result.returncode == 1
    assert len(calls) == 4 and all(call.endswith(f"-j1 {CLANG}") for call in calls[1:])
    assert "non completato dopo 3 giri" in result.stderr


def test_failure_without_a_missing_project_is_not_retried_blindly(tmp_path):
    result, calls = run(tmp_path, [[1, "error: disco pieno\n"]])
    assert result.returncode == 1 and len(calls) == 1
    assert "non riuscito" in result.stderr
