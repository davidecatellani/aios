import signal
from pathlib import Path

from aios_copilot import attivita as A
from aios_copilot.tools.attivita import ActivityRouter, make_tools


def proc(root: Path, pid: int, comm: str, cmd: str, ticks: int, pages: int, uid: int = 1000, ppid: int = 1):
    d = root / "proc" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    rest = [str(ppid)] + ["0"] * 9 + [str(ticks), "0"] + ["0"] * 8 + [str(pages)]
    (d / "stat").write_text(f"{pid} ({comm}) S " + " ".join(rest) + " 0 0 0\n")
    (d / "cmdline").write_text(cmd.replace(" ", "\0"))
    (d / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")


def machine(tmp: Path, busy: int = 0) -> Path:
    (tmp / "proc").mkdir(exist_ok=True)
    (tmp / "proc/stat").write_text(f"cpu  {100 + busy} 0 0 {900 + (100 - busy)} 0 0 0 0\ncpu0 {100 + busy} 0 0 {900 + 100 - busy} 0 0 0 0\n")
    (tmp / "proc/meminfo").write_text("MemTotal: 16000000 kB\nMemAvailable: 1000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    (tmp / "proc/uptime").write_text("3700.5 1000\n")
    (tmp / "proc/loadavg").write_text("0.5 0.4 0.3 1/100 999\n")
    hw = tmp / "sys/class/hwmon/hwmon0"
    hw.mkdir(parents=True, exist_ok=True)
    (hw / "name").write_text("coretemp\n")
    (hw / "temp1_input").write_text("93000\n")
    (hw / "temp1_label").write_text("Package id 0\n")
    (hw / "temp2_input").write_text("70000\n")
    nct = tmp / "sys/class/hwmon/hwmon1"
    nct.mkdir(parents=True, exist_ok=True)
    (nct / "name").write_text("nct6793\n")
    (nct / "fan2_input").write_text("850\n")
    return tmp


def test_snapshot_groups_programs_and_reads_sensors(tmp_path):
    root = machine(tmp_path)
    for pid, ticks in ((10, 0), (11, 0)):
        proc(root, pid, "Isolated Web Co", "/usr/lib/firefox/firefox -contentproc", ticks, 25600)
    proc(root, 20, "ollama", "/usr/bin/ollama runner", 0, 256000, uid=980)
    proc(root, 30, "python3", "python3 -m aios_copilot.shell", 0, 5000)
    proc(root, 2, "kthreadd", "", 0, 0, uid=0, ppid=0)
    t = [0.0]
    ps = {"models": [{"name": "qwen3.5:9b", "size": 6 * 2**30, "size_vram": 3 * 2**30}]}
    gpu = "NVIDIA GeForce GTX 1070, 40, 65, 3000, 8192, 30, 90.5\n"
    tm = A.TaskManager(root=root, run=lambda cmd: gpu if cmd[0] == "nvidia-smi" else "", http=lambda url, p=None: ps,
                       clock=lambda: t[0], me=1000)
    tm.snapshot(wait=0)
    proc(root, 10, "Isolated Web Co", "/usr/lib/firefox/firefox -contentproc", 100, 25600)  # 1 s di processore
    machine(root, busy=50)
    t[0] = 2.0
    s = tm.snapshot(wait=0)
    ff = next(p for p in s["programmi"] if p["nome"] == "Firefox")
    assert ff["processi"] == 2 and ff["memoria_mb"] == 200 and ff["cpu"] == 50.0 and ff["chiudibile"]
    assert next(p for p in s["programmi"] if p["nome"] == "Ollama (modelli AI)")["ai"]
    assert not next(p for p in s["programmi"] if p["nome"].startswith("AIOS"))["chiudibile"]
    assert s["processore"]["gradi"] == 93.0 and s["sensori"]["ventole"] == [{"nome": "Ventola del case", "giri": 850}]
    assert s["schede_video"][0]["memoria_totale"] == 8192 and s["ai"]["modelli"][0]["in_gpu"] == 50
    text = " ".join(s["consigli"])
    assert "scotta" in text and "50% nella scheda video" in text and "memoria è quasi piena" in text
    assert "GTX 1070" in A.describe(s) and s["acceso_da"] == 3700


def test_force_quit_only_user_programs(tmp_path):
    root = machine(tmp_path)
    proc(root, 10, "steam", "/usr/bin/steam", 0, 100)
    proc(root, 11, "steamwebhelper", "steamwebhelper", 0, 100)
    proc(root, 20, "Hyprland", "Hyprland", 0, 100)
    sent = []

    def kill(pid, sig):
        sent.append((pid, sig))
        if sig == signal.SIGTERM and pid == 10:
            import shutil
            shutil.rmtree(root / "proc" / "10")

    tm = A.TaskManager(root=root, me=1000, kill=kill)
    ok, msg = tm.end("Steam", wait=0.3)
    assert ok and (10, signal.SIGTERM) in sent and (11, signal.SIGKILL) in sent and "autorità" in msg
    ok, msg = tm.end("hyprland")
    assert not ok and "sistema" in msg


def test_router_and_tools():
    r = ActivityRouter()
    assert r.match("cosa sta rallentando il pc?").tool == "system_activity"
    assert r.match("quanto è caldo il processore").tool == "system_activity"
    assert r.match("chiudi a forza steam").args == {"programma": "steam"}
    assert r.match("togli il modello dalla memoria").tool == "unload_ai_model"
    assert r.match("chiudi firefox") is None
    names = {t.name for t in make_tools()}
    assert names == {"system_activity", "force_quit", "unload_ai_model"}
