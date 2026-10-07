import json
import os

import pytest

from aios_copilot import anteprima
from aios_copilot.cambiamenti import color_name, describe, from_reports


def el(k, label, box, par=-1, text="", bg="", c="", fs=0, r=0, ol="", path=None):
    return {"k": k, "p": path or k, "l": label, "t": text, "b": list(box), "bg": bg, "c": c, "fs": fs, "r": r,
            "ol": ol, "bd": "", "op": 1, "par": par}


def page(*items, fondo="#ffffff"):
    return {"fondo": fondo, "scuro": False, "elementi": list(items)}


def test_color_names():
    names = {"#d62828": "rosso", "#2a9d43": "verde", "#1d4ed8": "blu", "#f4c430": "giallo", "#f77f00": "arancione",
             "#7b2cbf": "viola", "#ff5d8f": "rosa", "#111111": "nero", "#8a8f98": "grigio", "#4cc9f0": "azzurro",
             "#7f4f24": "marrone", "#ffffff": "bianco", "#071d29": "blu scuro", "#e63946": "rosso"}
    assert {h: color_name(h) for h in names} == names
    assert color_name("") == "trasparente"


CARD = el("DIV.card|il riquadro|Meteo|", "il riquadro «Meteo»", (20, 40, 300, 120), bg="#ffffff", r=12)
TITLE = el("SPAN|la scritta|Meteo|Meteo", "la scritta «Meteo»", (36, 52, 80, 18), par=0, text="Meteo", c="#10232e", fs=14)
NOTE = el("DIV.card|il riquadro|Nota|", "il riquadro «Nota»", (20, 180, 300, 120), bg="#ffffff", r=12)


def test_color_size_and_corners():
    after = [dict(CARD, bg="#2a9d43", r=0), TITLE, NOTE]
    lines = describe(page(CARD, TITLE, NOTE), page(*after))
    assert "il riquadro «Meteo»: sfondo da bianco (#ffffff) a verde (#2a9d43)" in lines
    assert "il riquadro «Meteo»: angoli meno tondi (12 → 0 px) (squadrati)" in lines
    assert len(lines) == 2
    bigger = [dict(CARD, b=[20, 40, 465, 186]), dict(TITLE, b=[36, 52, 124, 28]), dict(NOTE, b=[20, 246, 300, 120])]
    lines = describe(page(CARD, TITLE, NOTE), page(*bigger))
    assert lines[0] == "il riquadro «Meteo»: più grande (300×120 → 465×186 px)"  # i figli non si ripetono
    assert "il riquadro «Nota»: spostato di 66 px in basso" in lines


def test_moves_appear_disappear_and_text():
    moved = describe(page(CARD, TITLE, NOTE), page(dict(CARD, b=[140, 40, 300, 120]), dict(TITLE, b=[156, 52, 80, 18]), NOTE))
    assert moved == ["il riquadro «Meteo»: spostato di 120 px a destra"]
    gone = describe(page(CARD, TITLE, NOTE), page(NOTE))
    assert gone == ["sparito: il riquadro «Meteo»"]  # la scritta dentro non si ripete
    extra = el("DIV|la scritta|Spesa|Spesa", "la scritta «Spesa»", (20, 165, 120, 14), text="Spesa")
    added = describe(page(CARD, TITLE, NOTE), page(CARD, TITLE, extra, NOTE))
    assert added == ["nuovo: la scritta «Spesa» (sotto il riquadro «Meteo», sopra il riquadro «Nota»)"]
    twice = describe(page(CARD, TITLE, NOTE), page(CARD, TITLE, extra, dict(extra, b=[20, 300, 120, 14]), NOTE))
    assert any("ora ce ne sono 2 uguali" in x for x in twice)
    renamed = dict(TITLE, k="SPAN|la scritta|Pioggia|Pioggia", l="la scritta «Pioggia»", t="Pioggia")
    assert describe(page(CARD, TITLE, NOTE), page(CARD, renamed, NOTE)) == ["testo cambiato: «Meteo» → «Pioggia»"]


def test_theme_problems_and_errors():
    items = [el(f"P{i}|la scritta|r{i}|r{i}", f"la scritta «r{i}»", (0, 20 * i, 100, 16), text=f"r{i}", c="#10232e") for i in range(12)]
    dark = [dict(x, c="#eaf4f4") for x in items]
    lines = describe(page(*items), page(*dark, fondo="#071d29"))
    assert lines[0] == "sfondo della pagina: da bianco (#ffffff) a blu scuro (#071d29)"
    assert "… e altri 8 cambi di colore (quasi tutta la pagina)" in lines
    before = {"inventario": page(CARD), "problemi": ["vecchio"], "errori": []}
    after = {"inventario": page(CARD), "problemi": ["vecchio", "testo tagliato: «Meteo»"], "errori": ["x is not defined"]}
    assert from_reports(before, after) == ["errore di JavaScript: x is not defined", "problema nella pagina: testo tagliato: «Meteo»"]
    assert from_reports({"inventario": page(CARD, TITLE, NOTE)}, {"inventario": page(CARD, TITLE, NOTE)}) == []
    empty = describe(page(*items), page(items[0]))
    assert empty == ["la pagina è quasi vuota: sono spariti 11 elementi su 12"]


HTML = """<!doctype html><html><body style="margin:0;font:15px sans-serif;background:#fff">
<div id="meteo" style="margin:20px;width:300px;height:120px;border-radius:12px;background:#f2f2f2"><b>Meteo</b> sole</div>
<div style="margin:20px;width:300px;height:120px;border-radius:12px;background:#f2f2f2"><span>Nota del giorno</span></div>
</body></html>"""


@pytest.mark.skipif(os.environ.get("AIOS_SENZA_BROWSER") == "1", reason="senza browser")
def test_inventory_in_a_real_page():
    playwright = pytest.importorskip("playwright.sync_api")
    anteprima.keep_browsers()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("AIOS_CHROMIUM") or None)
            page_ = browser.new_page(viewport={"width": 800, "height": 600})
            page_.set_content(HTML)
            before = json.loads(page_.evaluate(anteprima.REPORT_JS))
            page_.evaluate("document.getElementById('meteo').style.background = '#d62828'")
            after = json.loads(page_.evaluate(anteprima.REPORT_JS))
            browser.close()
    except Exception as exc:  # niente Chromium su questo PC
        pytest.skip(str(exc)[:80])
    labels = [e["l"] for e in before["inventario"]["elementi"]]
    assert "il riquadro «Meteo»" in labels and "il riquadro «Nota del giorno»" in labels
    lines = from_reports(before, after)
    assert lines[0] == "il riquadro «Meteo»: sfondo da bianco (#f2f2f2) a rosso (#d62828)"


def test_the_programmer_reads_what_changed(tmp_path):
    def shot(name, inventory):
        def take(page_name):
            img = tmp_path / f"{name}.png"
            img.write_bytes(b"png")
            img.with_suffix(".json").write_text(json.dumps({"errori": [], "problemi": [], "inventario": inventory}))
            return img, ""
        return take

    eyes = anteprima.Eyes(tmp_path, "colora il meteo di rosso", see=lambda img, prompt: "",
                          shoot=shot("dopo", page(dict(CARD, bg="#d62828"), TITLE, NOTE)),
                          shoot_base=shot("prima", page(CARD, TITLE, NOTE)))
    text = eyes("casa")
    assert "Cosa è cambiato rispetto a prima" in text
    assert "- il riquadro «Meteo»: sfondo da bianco (#ffffff) a rosso (#d62828)" in text
    same = anteprima.Eyes(tmp_path, "x", see=lambda img, prompt: "", shoot=shot("dopo", page(CARD)),
                          shoot_base=shot("prima", page(CARD)))
    assert "La pagina è uguale a prima della modifica" in same("casa")
