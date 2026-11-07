import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addestramento"))


def test_dataset_matches_nova_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import dati

    rows = dati.build(per_template=2)
    tasks = {r["compito"] for r in rows["smistamento"]}
    assert tasks == {"ambito", "azione"}
    for r in rows["smistamento"]:
        assert r["risposta"] in r["opzioni"]  # la grammatica deve poter dare la risposta giusta
    assert any(r["parte"] == "prova" for r in rows["campi"]) and rows["campi"]
