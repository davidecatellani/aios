"""L'accesso a SoIA parla con greetd con il suo protocollo, senza GDM."""

import json
import socket
import struct
import threading
from types import SimpleNamespace

from aios_copilot import accesso


def fake_greetd(path, password="giusta"):
    """Un greetd finto: chiede la password, poi avvia la sessione o dà errore."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(5)
    seen = []

    def read(c):
        n = struct.unpack("=I", c.recv(4))[0]
        return json.loads(c.recv(n))

    def send(c, msg):
        data = json.dumps(msg).encode()
        c.sendall(struct.pack("=I", len(data)) + data)

    def serve():
        while True:
            c, _ = srv.accept()
            with c:
                while True:
                    try:
                        msg = read(c)
                    except (struct.error, OSError):
                        break
                    seen.append(msg)
                    if msg["type"] == "create_session":
                        send(c, {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password: "})
                    elif msg["type"] == "post_auth_message_response":
                        send(c, {"type": "success"} if msg["response"] == password
                             else {"type": "error", "error_type": "auth_error", "description": "pam"})
                    else:
                        send(c, {"type": "success"})

    threading.Thread(target=serve, daemon=True).start()
    return seen


def test_login_through_greetd(tmp_path):
    sock = tmp_path / "greetd.sock"
    seen = fake_greetd(sock)
    g = accesso.Greetd(str(sock))
    assert g.login("aios", "sbagliata") == (False, "Password sbagliata.")
    assert seen[-1] == {"type": "cancel_session"}  # si può riprovare
    assert g.login("aios", "giusta") == (True, "")
    start = next(m for m in seen if m["type"] == "start_session")
    assert start["cmd"] == ["/usr/bin/aios-sessione"] and "XDG_CURRENT_DESKTOP=AIOS" in start["env"]


def test_only_real_people_are_listed():
    entries = [SimpleNamespace(pw_name="aios", pw_uid=1000, pw_gecos="Davide Catellani,,,", pw_shell="/bin/bash"),
               SimpleNamespace(pw_name="greeter", pw_uid=980, pw_gecos="", pw_shell="/sbin/nologin"),
               SimpleNamespace(pw_name="ospite", pw_uid=1001, pw_gecos="", pw_shell="/sbin/nologin"),
               SimpleNamespace(pw_name="nobody", pw_uid=65534, pw_gecos="", pw_shell="/bin/sh")]
    assert accesso.people(lambda: entries) == [{"utente": "aios", "nome": "Davide Catellani"}]
