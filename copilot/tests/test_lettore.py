from pathlib import Path

from aios_copilot import lettore


def fake_run(pages):
    calls = []

    def run(cmd, timeout=0, env=None):
        calls.append(cmd)
        if cmd[0] == "pdftotext":
            return 0, "\f".join(pages) + "\f"
        if cmd[0] == "pdftoppm":
            Path(cmd[-1] + ".png").write_bytes(b"png")
            return 0, ""
        return 1, ""
    return run, calls


def test_pdf_uses_its_text_and_ocr_only_for_image_pages(tmp_path):
    pdf = tmp_path / "bolletta.pdf"
    pdf.write_bytes(b"%PDF")
    run, calls = fake_run(["Totale da pagare 90,38 € entro il 29/07/2025, Enel Energia " * 2, "   ", "Pagina tre con testo vero abbastanza lungo da non sembrare una immagine"])
    seen = []
    out = lettore.read(pdf, lambda p: seen.append(p.name) or "TESTO DALL'OCR", run)
    assert "90,38" in out and "TESTO DALL'OCR" in out and "Pagina 3" in out
    assert seen == ["p2.png"] and [c[0] for c in calls].count("pdftoppm") == 1
    out = lettore.read(pdf, None, run)
    assert "pagina 2: è un'immagine" in out


def test_images_need_the_reader(tmp_path):
    img = tmp_path / "scontrino.jpg"
    img.write_bytes(b"jpg")
    assert lettore.read(img, lambda p: "letto") == "letto"
    assert "modello di lettura" in lettore.read(img, None)
    assert lettore.read(tmp_path / "manca.pdf", None).startswith("Non trovo")


def test_ovis_runs_llama_cpp(tmp_path, monkeypatch):
    cli = tmp_path / "llama-mtmd-cli"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setattr(lettore, "LLAMA_DIR", tmp_path)
    seen = []
    out = lettore.ovis(tmp_path / "a.png", [tmp_path / "ovis.gguf", tmp_path / "mmproj-ovis.gguf"],
                       run=lambda cmd, timeout=0, env=None: seen.append((cmd, env)) or (0, "<think>\n\n</think>\n\n# Bolletta"))
    assert out == "# Bolletta"
    cmd, env = seen[0]
    assert cmd[cmd.index("-m") + 1].endswith("ovis.gguf") and cmd[cmd.index("--mmproj") + 1].endswith("mmproj-ovis.gguf")
    assert str(tmp_path) in env["LD_LIBRARY_PATH"]
