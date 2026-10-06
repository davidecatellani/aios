import pytest


@pytest.fixture(autouse=True)
def cartelle_isolate(tmp_path, monkeypatch):
    """Nessuna prova scrive nella cartella vera dell'utente (diario, impostazioni, indici)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    run = tmp_path.parent / (tmp_path.name + "-run")  # fuori dalla «casa» delle prove
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))  # i turni di Nova (precedenza.py)
    run.mkdir(exist_ok=True)
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setenv("AIOS_NUCLEO", "spento")  # nessuna prova usa i modelli veri presenti sulla macchina
    monkeypatch.setenv("AIOS_PARAKEET", "spento")
    monkeypatch.setenv("AIOS_PARLANTI", "spento")
    monkeypatch.setenv("AIOS_KOKORO", "spento")
    monkeypatch.setenv("AIOS_DECISORE", "spento")
