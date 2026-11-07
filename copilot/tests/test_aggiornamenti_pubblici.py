import io
import json
import urllib.error

from aios_copilot.imageupdate import GithubSource
from aios_copilot.registro import Access


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_public_repo_needs_no_token():
    sent = []

    def opener(req):
        sent.append(req)
        return _Resp(json.dumps({"full_name": "x/aios"}).encode())

    assert GithubSource("x/aios", opener=opener).check_access() == ""
    assert not sent[0].has_header("Authorization") and not sent[0].unredirected_hdrs
    GithubSource("x/aios", "tok", opener=opener).check_access()
    assert sent[1].unredirected_hdrs.get("Authorization") == "Bearer tok"  # privato: col token


def test_private_without_token_says_what_to_do():
    def opener(req):
        raise urllib.error.HTTPError(req.full_url, 404, "", {}, None)

    assert "non è pubblico" in GithubSource("x/aios", opener=opener).check_access()

    def registry(req):
        if "/token" in req.full_url:
            assert not req.has_header("Authorization")  # richiesta anonima
            raise urllib.error.HTTPError(req.full_url, 401, "", {}, None)
        raise AssertionError(req.full_url)

    assert "rendila pubblica" in Access("x/aios", opener=registry).check("44")
