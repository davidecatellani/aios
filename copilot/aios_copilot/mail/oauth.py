"""Accesso con Google/Microsoft (OAuth 2.0, flusso "loopback" con PKCE).

Si apre il browser sulla pagina di accesso del provider; la risposta torna a un
piccolo server su 127.0.0.1 che vive solo per il tempo dell'accesso. La password
non passa mai da SoIA: si conservano solo i token, nel portachiavi.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from .providers import OAuthConfig

PAGE_OK = "<html><body style='font-family:sans-serif;text-align:center;padding:60px'><h2>Fatto ✓</h2><p>Puoi chiudere questa pagina e tornare a SoIA.</p></body></html>"


class OAuthError(RuntimeError):
    pass


def client_credentials(cfg: OAuthConfig) -> tuple[str, str | None]:
    """Client id (e secret, per Google) registrati da SoIA presso il provider."""
    cid = os.environ.get(f"{cfg.env_prefix}_CLIENT_ID")
    secret = os.environ.get(f"{cfg.env_prefix}_CLIENT_SECRET")
    if not cid:
        path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "oauth.json"
        try:
            data = json.loads(path.read_text()).get(cfg.env_prefix.lower().removeprefix("aios_"), {})
            cid, secret = data.get("client_id"), data.get("client_secret", secret)
        except (OSError, ValueError, AttributeError):
            pass
    if not cid:
        raise OAuthError("Manca il client id OAuth di SoIA per questo provider (vedi docs: oauth.json).")
    return cid, secret


def _post(url: str, data: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"Il provider ha rifiutato la richiesta: {exc.read().decode(errors='replace')[:300]}") from exc


def _tokens(payload: dict[str, Any], old_refresh: str | None = None) -> dict[str, Any]:
    if "access_token" not in payload:
        raise OAuthError(f"Risposta inattesa dal provider: {payload}")
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or old_refresh,
        "expires_at": time.time() + int(payload.get("expires_in", 3600)) - 60,
    }


def authorize(cfg: OAuthConfig, login_hint: str = "", open_browser: Callable[[str], Any] = webbrowser.open,
              timeout: float = 300) -> dict[str, Any]:
    client_id, client_secret = client_credentials(cfg)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if query.get("state") != state:  # risposta non nostra: ignorata
                self.send_response(400)
                self.end_headers()
                return
            result.update(query)
            body = PAGE_OK.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect,
                  "scope": " ".join(cfg.scopes), "state": state, "code_challenge": challenge,
                  "code_challenge_method": "S256", **dict(cfg.extra_params)}
        if login_hint:
            params["login_hint"] = login_hint
        open_browser(f"{cfg.auth_url}?{urllib.parse.urlencode(params)}")
        if not done.wait(timeout):
            raise OAuthError("Accesso non completato in tempo.")
    finally:
        server.shutdown()
    if "code" not in result:
        raise OAuthError(f"Accesso negato: {result.get('error', 'motivo sconosciuto')}")
    data = {"grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect,
            "client_id": client_id, "code_verifier": verifier}
    if client_secret:
        data["client_secret"] = client_secret
    return _tokens(_post(cfg.token_url, data))


def refresh(cfg: OAuthConfig, tokens: dict[str, Any]) -> dict[str, Any]:
    if tokens.get("expires_at", 0) > time.time():
        return tokens
    if not tokens.get("refresh_token"):
        raise OAuthError("Accesso scaduto: serve accedere di nuovo.")
    client_id, client_secret = client_credentials(cfg)
    data = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
    if client_secret:
        data["client_secret"] = client_secret
    return _tokens(_post(cfg.token_url, data), tokens["refresh_token"])


def xoauth2(user: str, access_token: str) -> str:
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"
