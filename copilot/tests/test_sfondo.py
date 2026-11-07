from pathlib import Path

import pytest

from aios_copilot.tools.foto import PhotosRouter

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")


class FakeRemover:
    def mask(self, rgb):
        m = np.zeros(rgb.shape[:2], dtype=np.float32)
        m[10:30, 20:40] = 1.0  # il «soggetto»
        return m


def test_cut_transparent_crop_and_white(tmp_path):
    from aios_copilot import sfondo as S

    img = np.full((50, 60, 3), 200, dtype=np.uint8)
    src = tmp_path / "foto.jpg"
    cv2.imwrite(str(src), img)
    out = S.cut(FakeRemover(), src)
    assert out.name == "foto senza sfondo.png"
    rgba = cv2.imread(str(out), cv2.IMREAD_UNCHANGED)
    assert rgba.shape == (50, 60, 4) and rgba[0, 0, 3] == 0 and rgba[20, 30, 3] == 255
    out2 = S.cut(FakeRemover(), src, background="bianco", crop=True)
    assert out2.name == "foto senza sfondo (2).png"
    white = cv2.imread(str(out2), cv2.IMREAD_UNCHANGED)
    assert white.ndim == 3 and white.shape[2] == 3 and white.shape[0] < 50
    with pytest.raises(ValueError):
        S.cut(FakeRemover(), tmp_path / "manca.jpg")


def test_router():
    r = PhotosRouter()
    assert r.match("togli lo sfondo").args == {"percorso": "", "sfondo": ""}
    assert r.match("togli lo sfondo a questa foto").args["percorso"] == ""
    i = r.match("rimuovi lo sfondo dalla fototessera.jpg e mettilo bianco")
    assert i.tool == "remove_background" and i.args == {"percorso": "fototessera.jpg", "sfondo": "bianco"}
