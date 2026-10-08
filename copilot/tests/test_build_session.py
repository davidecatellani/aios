"""Processi reali con scadenze brevi: arresto, ripresa e propagazione degli errori."""

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'phone/scripts/build-session.py'


def command(tmp_path, code, seconds=0):
    return [sys.executable, str(SCRIPT), '--seconds', str(seconds),
            '--grace-seconds', '0.2', '--result', str(tmp_path / 'session.json'),
            '--', sys.executable, '-c', code]


@pytest.mark.parametrize('code', [0, 17, 75])
def test_command_result_replaces_old_pause_without_hiding_errors(tmp_path, code):
    output = tmp_path / 'session.json'
    output.write_text(json.dumps({'state': 'paused', 'reason': 'time_limit', 'exit_code': 75}))
    proc = subprocess.run(command(tmp_path, f'import sys; sys.exit({code})'), capture_output=True, timeout=5)
    assert proc.returncode == code
    doc = json.loads(output.read_text())
    assert doc['state'] == ('completed' if code == 0 else 'failed')
    assert doc['reason'] == 'command_exit'


def test_pause_closes_worker_and_reuses_its_completed_work(tmp_path):
    state = tmp_path / 'completed-object'
    stopped = tmp_path / 'stopped'
    code = f'''import signal, sys, time
from pathlib import Path
Path({str(state)!r}).write_text('compiled')
def stop(signum, frame):
    Path({str(stopped)!r}).write_text('closed')
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
while True: time.sleep(0.1)
'''
    proc = subprocess.run(command(tmp_path, code, seconds=1), capture_output=True, text=True, timeout=5)
    assert proc.returncode == 75, proc.stderr
    assert stopped.read_text() == 'closed'
    assert json.loads((tmp_path / 'session.json').read_text())['reason'] == 'time_limit'
    resume = f"from pathlib import Path; p=Path({str(state)!r}); assert p.read_text() == 'compiled'; p.write_text('reused')"
    proc = subprocess.run(command(tmp_path, resume), capture_output=True, timeout=5)
    assert proc.returncode == 0, proc.stderr
    assert state.read_text() == 'reused'


def test_unresponsive_worker_is_killed_before_session_returns(tmp_path):
    pid_file = tmp_path / 'pid'
    code = f'''import signal, time, os
from pathlib import Path
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path({str(pid_file)!r}).write_text(str(os.getpid()))
while True: time.sleep(0.1)
'''
    proc = subprocess.run(command(tmp_path, code, seconds=1), capture_output=True, timeout=6)
    assert proc.returncode == 75, proc.stderr
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_manual_stop_preserves_data_and_is_not_a_successful_build(tmp_path):
    state = tmp_path / 'ready'
    code = f"from pathlib import Path; import time; Path({str(state)!r}).write_text('cached'); time.sleep(30)"
    proc = subprocess.Popen(command(tmp_path, code), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 3
        while not state.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert state.exists()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=5)
        assert proc.returncode == 143
        doc = json.loads((tmp_path / 'session.json').read_text())
        assert doc['state'] == 'paused' and doc['reason'] == 'interrupted'
        assert state.read_text() == 'cached'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_real_ninja_build_resumes_without_rebuilding_completed_targets(tmp_path):
    ninja = shutil.which('ninja')
    if not ninja:
        pytest.skip('Ninja non disponibile')
    worker = tmp_path / 'worker.py'
    worker.write_text('''import sys, time
from pathlib import Path
name = sys.argv[1]
with Path('calls').open('a') as log: log.write(name + '\\n')
if name == 'second.o' and not Path('attempted').exists():
    Path('attempted').touch()
    time.sleep(30)
Path(name).write_text('compiled')
''')
    (tmp_path / 'build.ninja').write_text(
        f'rule compile\n  command = {sys.executable} worker.py $out\n'
        'build first.o: compile\nbuild second.o: compile first.o\ndefault second.o\n')
    args = command(tmp_path, '', seconds=1)[:-3] + [ninja, '-j1']
    first = subprocess.run(args, cwd=tmp_path, capture_output=True, timeout=6)
    assert first.returncode == 75, first.stderr
    assert (tmp_path / 'first.o').exists() and not (tmp_path / 'second.o').exists()
    args = command(tmp_path, '')[:-3] + [ninja, '-j1']
    resumed = subprocess.run(args, cwd=tmp_path, capture_output=True, timeout=6)
    assert resumed.returncode == 0, resumed.stderr
    assert (tmp_path / 'calls').read_text().splitlines() == ['first.o', 'second.o', 'second.o']
    assert (tmp_path / 'second.o').read_text() == 'compiled'
