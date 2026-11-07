"""Cercare le foto dell'utente: «le foto di Aurora», «le foto di agosto», «foto del mare dell'anno scorso».

Si cerca nei nomi delle cartelle e dei file (album come «Compleanno Aurora») e nelle date; se il
riconoscimento delle foto è acceso (galleria.py), anche in cosa c'è nella foto, nelle scritte e nelle
persone riconosciute dal volto. Le foto trovate compaiono come miniature accanto alla risposta.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .base import Tool, attach, params

IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".heic"}
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre"]
SKIP_WORDS = set("""foto fotografie immagini immagine mostrami mostra fammi vedere trova cerca le la lo il i gli di del
della dei delle con in a al alla da su sul sulla che ho fatto fatte scattate mie miei mio mia tutte quelle quella
dove anno""".split())


def picture_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    return [p for p in (home / "Immagini", home / "Pictures", home / "Scaricati", home / "Downloads",
                        home / "Telefono", home / "DCIM") if p.is_dir()]


def _plain(text: str) -> str:
    return (text.lower().replace("à", "a").replace("è", "e").replace("é", "e").replace("ì", "i")
            .replace("ò", "o").replace("ù", "u"))


def parse_query(query: str, today: date | None = None) -> tuple[list[str], tuple[int, int | None] | None]:
    """Parole da cercare e periodo (anno, mese) se la frase ne indica uno."""
    today = today or date.today()
    q = _plain(query)
    period: tuple[int, int | None] | None = None
    year = re.search(r"\b(19|20)\d{2}\b", q)
    if "anno scorso" in q:
        period = (today.year - 1, None)
    elif year:
        period = (int(year.group(0)), None)
    for i, m in enumerate(MONTHS, 1):
        if re.search(rf"\b{m}\b", q):
            y = period[0] if period else (today.year if i <= today.month else today.year - 1)
            period = (y, i)
    words = [w for w in re.findall(r"[a-z0-9]+", q)
             if len(w) > 2 and w not in SKIP_WORDS and w not in MONTHS and not re.fullmatch(r"(19|20)\d{2}", w)
             and w not in ("anno", "scorso", "scorsa")]
    return words, period


def find_photos(query: str, roots: list[Path] | None = None, today: date | None = None,
                limit: int = 24) -> list[Path]:
    words, period = parse_query(query, today)
    found: list[tuple[int, float, Path]] = []
    for root in roots if roots is not None else picture_roots():
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel = _plain(str(Path(dirpath).relative_to(root)))
            for f in files:
                p = Path(dirpath) / f
                if p.suffix.lower() not in IMAGES or f.startswith("."):
                    continue
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if period:
                    when = datetime.fromtimestamp(mtime)
                    if when.year != period[0] or (period[1] and when.month != period[1]):
                        continue
                hay = rel + " " + _plain(f)
                score = sum(1 for w in words if w in hay)
                if words and not score:
                    continue
                found.append((score, mtime, p))
    found.sort(key=lambda t: (-t[0], -t[1]))
    return [p for _, _, p in found[:limit]]


def gallery_matches(query: str) -> list[Path]:
    """Le foto riconosciute per contenuto o persona (se il riconoscimento ha già lavorato)."""
    from .. import galleria

    try:
        db = galleria.Gallery() if galleria.enabled() or galleria_has_data() else None
    except Exception:
        return []
    if db is None:
        return []
    words, period = parse_query(query)
    found = [p for _, p in db.search(query)]
    if len(found) < 6 and words:  # poche per nome o descrizione: anche per significato (impronte SigLIP2)
        try:
            found += [p for p in galleria.meaning_matches(db, " ".join(words)) if p not in found]
        except Exception:
            pass
    out = []
    for p in found:
        path = Path(p)
        try:
            when = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if period and (when.year != period[0] or (period[1] and when.month != period[1])):
            continue
        out.append(path)
    return out


def galleria_has_data() -> bool:
    from ..agenda import data_dir

    return (data_dir() / "galleria.db").exists()


LAST_CUTOUT: dict[str, str] = {}  # l'ultima foto senza sfondo fatta da Nova (per «annulla»)


def make_tools(roots: Callable[[], list[Path]] = picture_roots,
               recognized: Callable[[str], list[Path]] = gallery_matches) -> list[Tool]:
    def show_photos(query: str) -> str:
        by_content = recognized(query)
        photos = list(dict.fromkeys(by_content + find_photos(query, roots())))[:36]
        if not photos:
            words, _ = parse_query(query)
            hint = (f" Per ora le riconosco dai nomi delle cartelle e dei file: se le foto di {words[0].capitalize()} "
                    f"sono in un album con un altro nome, dimmelo.") if words else ""
            from .. import galleria

            if not galleria.enabled():
                hint += " Se accendi il riconoscimento delle foto le trovo anche da cosa c'è dentro e dai volti."
            return f"Non ho trovato foto per «{query}».{hint}"
        home = Path.home()
        items: list[dict[str, Any]] = []
        for p in photos:
            items.append({"titolo": p.name, "percorso": str(p), "sottotitolo":
                          datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y"),
                          "cartella": str(p.parent).replace(str(home), "~")})
        attach("foto", items, f"Foto: {query}")
        folders = sorted({i["cartella"] for i in items})
        return (f"Ho trovato {len(photos)} foto" + (f" (in {', '.join(folders[:3])})" if folders else "") +
                ": sono qui accanto, toccane una per vederla grande.")

    def photo_recognition(attiva: str = "si") -> str:
        from .. import galleria

        on = str(attiva).lower() in ("si", "sì", "true", "acceso", "attiva", "on")
        galleria.set_enabled(on)
        if not on:
            return "Riconoscimento delle foto spento. Quello già fatto resta finché non lo cancelli (Impostazioni › Privacy)."
        extra = ""
        if galleria.vision_model() is None:
            try:
                from ..models import Queue, find_model

                model = find_model(galleria.VISION_MODEL)
                if model is not None and Queue().add(model):
                    extra = (f" Prima scarico il modello per guardare le foto ({model.size_gb:.1f} GB), quando il computer è a "
                             "riposo e in carica.")
            except Exception:
                pass
        faces = "" if galleria.faces_available() else " (i volti arrivano con il prossimo aggiornamento di SoIA)"
        return ("Acceso: quando il computer è a riposo e in carica guardo le tue foto una alla volta: cosa c'è, le "
                f"scritte e le persone{faces}. Resta tutto qui sul computer." + extra +
                " Quando trovo delle persone ti chiedo chi sono: le vedi in Foto › Persone.")

    def photo_recognition_status() -> str:
        from .. import galleria

        if not galleria.enabled():
            return "Il riconoscimento delle foto è spento: dimmi «riconosci le mie foto» per accenderlo."
        st = galleria.Gallery().stats()
        if not st["foto"]:
            return "Il riconoscimento è acceso: comincio appena il computer è a riposo e in carica."
        unnamed = [p for p in galleria.Gallery().people() if not p["nome"]]
        return (f"Ho guardato {st['descritte']} foto su {st['foto']} e cercato i volti in {st['volti_fatti']}. "
                f"Persone con un nome: {st['persone_con_nome']}" +
                (f"; {len(unnamed)} da riconoscere in Foto › Persone." if unnamed else "."))

    def duplicate_photos() -> str:
        from .. import galleria

        if not galleria.prints_ready():
            return "Per trovare le foto doppie mi serve il riconoscimento delle foto (arriva con il prossimo aggiornamento)."
        db = galleria.Gallery()
        groups = db.duplicates()
        if not groups:
            st = db.stats()
            return ("Non ho trovato foto doppie." if st["impronte"] else
                    "Non ho ancora guardato le foto: lo faccio quando il computer è a riposo, poi riprova.")
        home = Path.home()
        items: list[dict[str, Any]] = []
        for n, group in enumerate(groups[:12], 1):
            for p in group[:6]:
                path = Path(p)
                if path.exists():
                    items.append({"titolo": path.name, "percorso": p, "sottotitolo": f"gruppo {n}",
                                  "cartella": str(path.parent).replace(str(home), "~")})
        attach("foto", items, "Foto doppie o quasi uguali")
        extra = sum(len(g) - 1 for g in groups)
        return (f"Ho trovato {len(groups)} gruppi di foto quasi uguali ({extra} in più dell'originale): sono qui accanto. "
                "Non cancello niente da solo: dimmi quali tenere.")

    def remove_background(percorso: str = "", sfondo: str = "", ritaglia: bool = False) -> str:
        from .. import sfondo as S

        home = Path.home()
        if not S.available():
            return "Per togliere lo sfondo serve il modello BiRefNet, che non è installato in questa versione di SoIA."
        target = Path(percorso).expanduser() if percorso else latest_picture(home)
        if target is not None and not target.is_absolute():
            target = next((p for p in (home / target, *(r / target for r in picture_roots(home))) if p.exists()), target)
        if target is None or not target.is_file():
            return f"Non trovo l'immagine «{percorso}»." if percorso else "Non trovo un'immagine recente: dimmi quale."
        try:
            out = S.cut(S.shared(), target, background=sfondo, crop=bool(ritaglia))
        except (ValueError, OSError) as exc:
            return f"Non ci riesco: {exc}."
        LAST_CUTOUT["percorso"] = str(out)  # per «annulla» (azioni.py)
        attach("foto", [{"titolo": out.name, "percorso": str(out), "sottotitolo": "senza sfondo"}], "Senza sfondo")
        bg = f"sfondo {sfondo}" if sfondo and sfondo != "trasparente" else "sfondo trasparente"
        return f"Fatto: «{out.name}» ({bg}), accanto all'originale «{target.name}», che resta com'era."

    return [Tool("remove_background", "Toglie lo sfondo da una foto o immagine: resta il soggetto (persona, oggetto, animale) "
                 "su sfondo trasparente o di un colore (es. bianco per una fototessera). Senza percorso: l'immagine più recente.",
                 params(percorso="Il file (facoltativo)", sfondo="trasparente, bianco, nero, azzurro… (facoltativo)",
                        ritaglia="true per tenere solo il riquadro del soggetto"), remove_background),
            Tool("duplicate_photos", "Trova le foto doppie o quasi uguali (scatti in sequenza) tra le foto dell'utente.",
                 params(), duplicate_photos, reads_private=True),
            Tool("photo_recognition", "Accende o spegne il riconoscimento delle foto (cosa c'è, scritte, persone dal volto).",
                 params(attiva=("Acceso o spento", ["si", "no"])), photo_recognition),
            Tool("photo_recognition_status", "Dice a che punto è il riconoscimento delle foto.", params(),
                 photo_recognition_status),
            Tool("show_photos", "Cerca e mostra le foto dell'utente per persona, luogo, evento o periodo "
                 "(es. «le foto di Aurora», «le foto di agosto», «il mare dell'anno scorso»).",
                 params(query="Cosa o chi cercare nelle foto, con il periodo se c'è"), show_photos, reads_private=True)]


def latest_picture(home: Path) -> Path | None:
    """L'immagine più recente tra screenshot, scaricati e foto (per «togli lo sfondo» senza dire quale)."""
    best: tuple[float, Path] | None = None
    for root in picture_roots(home):
        for p in [*root.glob("*"), *root.glob("*/*")][:3000]:
            if p.suffix.lower() in IMAGES and p.is_file() and "senza sfondo" not in p.name:
                m = p.stat().st_mtime
                if best is None or m > best[0]:
                    best = (m, p)
    return best[1] if best else None


RE_BACKGROUND = re.compile(r"^(?:togli|rimuovi|elimina|cancella|leva)\s+lo\s+sfondo(?:\s+(?:dalla|alla|dall'|all'|dal|al|da|a)\s*(?P<f>.+?))?"
                           r"(?:\s+(?:e\s+)?(?:mettilo|mettici|con\s+lo\s+sfondo|con\s+sfondo)\s+(?P<c>bianco|nero|azzurro|grigio|blu|verde|rosso|trasparente))?$"
                           r"|^scontorna\s*(?P<f2>.+)?$")
RE_PHOTOS = re.compile(r"^(?:mostra(?:mi)?|fammi\s+vedere|trova(?:mi)?|cerca(?:mi)?|apri)\s+(?:le\s+|tutte\s+le\s+)?"
                       r"(?:mie\s+)?(?:foto|fotografie|immagini)\s+(?P<q>(?:di|del|della|dei|delle|con|in|a|al|alla|da|su|sul|sulla|che)\b.+)$")


RE_RECOGNITION = re.compile(r"^(?P<v>riconosci|analizza|guarda|attiva\s+il\s+riconoscimento\s+del(?:le)?|accendi\s+il\s+riconoscimento\s+del(?:le)?"
                            r"|spegni\s+il\s+riconoscimento\s+del(?:le)?|disattiva\s+il\s+riconoscimento\s+del(?:le)?)\s+(?:le\s+)?(?:mie\s+)?foto$")
RE_RECOGNITION_STATUS = re.compile(r"^(?:a\s+che\s+punto\s+(?:è|e)|come\s+va)\s+(?:il\s+)?riconoscimento\s+delle\s+foto\??$")


RE_DUPLICATES = re.compile(r"^(?:trova(?:mi)?|cerca(?:mi)?|mostra(?:mi)?|ci\s+sono|ho)\s+(?:delle\s+|le\s+|i\s+)?(?:foto\s+(?:doppie|uguali|duplicate|ripetute)|doppioni(?:\s+(?:delle|tra\s+le)\s+foto)?)$")


class PhotosRouter:
    def match(self, text: str) -> Any:
        from ..fastpath import Intent, normalize

        low = normalize(text).strip(" .!?")
        m = RE_BACKGROUND.match(low)
        if m:
            f = (m.group("f") or m.group("f2") or "").strip()
            if re.fullmatch(r"(?:questa|quella|l'ultima|ultima)?\s*(?:foto|immagine|screenshot|schermata)?", f):
                f = ""
            return Intent("remove_background", {"percorso": f, "sfondo": m.group("c") or ""})
        m = RE_RECOGNITION.match(low)
        if m:
            return Intent("photo_recognition", {"attiva": "no" if m.group("v").startswith(("spegni", "disattiva")) else "si"})
        if RE_DUPLICATES.match(low):
            return Intent("duplicate_photos", {})
        if RE_RECOGNITION_STATUS.match(low):
            return Intent("photo_recognition_status", {})
        m = RE_PHOTOS.match(low)
        return Intent("show_photos", {"query": m.group("q")}) if m else None
