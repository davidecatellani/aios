import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aios_copilot import precedenza


@pytest.fixture(autouse=True)
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))


def test_user_turn_marks_and_grace():
    assert not precedenza.busy()
    with precedenza.user_turn():
        assert precedenza.busy()
        assert not precedenza.busy(ignore_own=True, grace=0)  # il turno di questo thread non blocca se stesso
        seen = []
        t = threading.Thread(target=lambda: seen.append(precedenza.busy(ignore_own=True, grace=0)))
        t.start()
        t.join()
        assert seen == [True]  # un altro thread invece aspetta
    assert precedenza.busy()  # subito dopo: c'è ancora il margine (si continua spesso a parlare)
    assert not precedenza.busy(grace=0)
    assert not precedenza.busy(now=time.time() + precedenza.GRACE + 1)


def test_stale_marks_are_removed():
    old = precedenza.marks_dir() / "999-1-0"
    old.write_text("x")
    import os

    os.utime(old, (time.time() - precedenza.STALE - 5,) * 2)
    assert not precedenza.busy(grace=0) and not old.exists()


def test_wait_turn_with_limit():
    with precedenza.user_turn():
        assert not precedenza.wait_turn(limit=0.0, ignore_own=False, sleep=lambda s: None)


class FakeLlama(BaseHTTPRequestHandler):
    """Un llama-server finto: scrive una parola ogni 50 ms; ripete il prefill dell'assistente, come quello vero."""
    calls: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLlama.calls.append(body)
        msgs = body["messages"]
        prefill = msgs[-1]["content"] if msgs[-1]["role"] == "assistant" else ""
        words = ["lunedì ", "martedì ", "mercoledì ", "giovedì ", "venerdì "]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            if prefill:
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': prefill}}]})}\n\n".encode())
            for w in words:
                if w in prefill:
                    continue
                time.sleep(0.2)
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': w}}]})}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def test_background_chat_pauses_and_resumes():
    FakeLlama.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
    stops = iter([False] + [True] + [False] * 1000)  # a metà arriva l'utente
    waited = []
    try:
        text = precedenza.background_chat(url, {"messages": [{"role": "user", "content": "i giorni"}]},
                                          should_stop=lambda: next(stops), wait=lambda: waited.append(1) or True)
    finally:
        server.shutdown()
    assert text == "lunedì martedì mercoledì giovedì venerdì "
    assert len(FakeLlama.calls) == 2 and len(waited) == 2
    resumed = FakeLlama.calls[1]["messages"][-1]
    assert resumed["role"] == "assistant" and resumed["content"] and "venerdì" not in resumed["content"]


def test_scheduler_waits_for_nova(monkeypatch):
    from aios_copilot import learning

    monkeypatch.setattr("aios_copilot.giochi.active", lambda: False)
    s = learning.Scheduler(tasks=[])
    with precedenza.user_turn():
        assert s.can_run() == (False, "stai usando Nova")
