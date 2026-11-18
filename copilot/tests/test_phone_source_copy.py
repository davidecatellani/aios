"""copia_in_sorgenti: SoIA copiato nei sorgenti Android, perché Soong ignora le cartelle collegate."""

from pathlib import Path
import subprocess


COMUNE = Path(__file__).resolve().parents[2] / "phone/scripts/comune.sh"


def copy(origin, destination):
    return subprocess.run(["bash", "-c", f'source "{COMUNE}"; copia_in_sorgenti "$1" "$2"', "copia",
                           str(origin), str(destination)], capture_output=True, text=True, timeout=10)


def test_old_symlink_becomes_a_real_copy_that_follows_changes(tmp_path):
    origin = tmp_path / "phone/vendor/aios"
    (origin / "products").mkdir(parents=True)
    (origin / "AndroidProducts.mk").write_text("PRODUCT_MAKEFILES := ...")
    (origin / "products/vecchio.mk").write_text("old")
    (origin / ".git").mkdir()
    destination = tmp_path / "aosp/vendor/aios"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(origin)

    result = copy(origin, destination)
    assert result.returncode == 0, result.stderr
    assert not destination.is_symlink()
    assert (destination / "AndroidProducts.mk").read_text() == "PRODUCT_MAKEFILES := ..."
    assert not (destination / ".git").exists()

    (origin / "products/vecchio.mk").unlink()
    (origin / "AndroidProducts.mk").write_text("aggiornato")
    result = copy(origin, destination)
    assert result.returncode == 0, result.stderr
    assert (destination / "AndroidProducts.mk").read_text() == "aggiornato"
    assert not (destination / "products/vecchio.mk").exists()


def test_existing_folder_that_is_not_ours_is_left_alone(tmp_path):
    origin = tmp_path / "gapps"
    origin.mkdir()
    (origin / "Android.bp").write_text("nuovo")
    destination = tmp_path / "aosp/vendor/gapps"
    destination.mkdir(parents=True)
    (destination / "Android.bp").write_text("di qualcun altro")

    result = copy(origin, destination)
    assert result.returncode == 1
    assert "non è una copia di SoIA" in result.stderr
    assert (destination / "Android.bp").read_text() == "di qualcun altro"
