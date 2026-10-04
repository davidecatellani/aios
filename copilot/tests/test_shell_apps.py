"""Le app HTML di AIOS: file solo nella cartella personale, documenti in un riquadro, impostazioni."""

import zipfile

import pytest

from aios_copilot.shell import apps as A


@pytest.fixture
def casa(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOS_CASA", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    (tmp_path / "Documenti").mkdir()
    return tmp_path


def test_paths_stay_in_home(casa):
    assert A.safe_path("Documenti") == casa / "Documenti"
    for bad in ("../../etc/passwd", "/etc/passwd", "Documenti/../../x"):
        with pytest.raises(ValueError):
            A.safe_path(bad)
    data = A.list_folder(casa)
    assert data["nome"] == "Casa" and [v["nome"] for v in data["voci"]] == ["Documenti"]  # niente file nascosti


def test_documents_become_safe_pages(casa):
    doc = casa / "Documenti" / "contratto.docx"
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   "<w:body><w:p><w:r><w:t>Canone 750 euro</w:t></w:r></w:p></w:body></w:document>")
    page = A.document_page(doc, "chiave")
    assert b"Canone 750 euro" in page.body and "script-src" not in page.csp
    (casa / "nota.txt").write_text("<script>alert(1)</script>")
    assert b"&lt;script&gt;" in A.document_page(casa / "nota.txt", "k").body
    assert "sandbox" in A.file_response(casa / "nota.txt").csp
    assert A._clean_html('<p onclick="x()">a</p><script>b</script><a href="javascript:c">d</a>') == '<p>a</p><a href="#">d</a>'


def test_wifi_list_from_nmcli():
    def run(cmd):
        j = " ".join(cmd)
        if j.startswith("nmcli radio"):
            return 0, "enabled\n"
        if "wifi list" in j:
            return 0, "*:Casa:80:WPA2\n:Casa:60:WPA2\n:Bar\\:Ospiti:40:--\n"
        return 0, "wifi\n"
    state = A.wifi_state(run)
    assert state["acceso"] and state["scheda"]
    assert state["reti"] == [{"nome": "Casa", "segnale": 80, "protetta": True, "attiva": True},
                             {"nome": "Bar:Ospiti", "segnale": 40, "protetta": False, "attiva": False}]
    assert A.wifi_connect("Casa", "sbagliata", lambda c: (4, "Secrets were required, but not provided"))[1] == "Password sbagliata, riprova."
