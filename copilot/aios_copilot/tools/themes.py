"""Temi: creare, ritoccare, applicare, market. Strumenti per il copilota e frasi riconosciute all'istante."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .. import themeapply, thememarket, themes
from ..fastpath import Intent, normalize
from ..xdg import resolve_folder
from .base import Runner, Tool, params


def describe(theme: themes.Theme) -> str:
    wp = theme.wallpaper.get("style") or ("la tua immagine" if theme.wallpaper.get("kind") == "immagine" else "nessuno")
    note = " Resta per uso personale: è ispirato a un'opera o a un marchio." if theme.personal_only else ""
    return (f"Tema «{theme.name}» applicato: accento {theme.light.accent}, sfondo «{wp}», carattere {theme.font}, "
            f"angoli da {theme.radius}px. Testi sempre leggibili (contrasto verificato).{note} "
            "Puoi dirmi «più scuro», «più caldo», «più vivace»… oppure «torna al tema di prima».")


def make_tools(ask_llm: Callable[[str], str] | None = None, runner: Runner | None = None,
               fetch: Callable[[str], bytes] | None = None) -> list[Tool]:
    def apply_and_tell(theme: themes.Theme, image: Path | None = None) -> str:
        themes.save(theme, image)
        themeapply.apply(theme, runner)
        return describe(theme)

    def create_theme(description: str) -> str:
        return apply_and_tell(themes.from_description(description, ask_llm))

    def create_theme_from_image(path: str, name: str = "") -> str:
        image = Path(path).expanduser()
        if not image.is_file():
            return f"Non trovo l'immagine {image}."
        try:
            theme = themes.from_image(image, name)
        except ValueError as exc:
            return str(exc)
        return apply_and_tell(theme, image)

    def remix_theme(change: str) -> str:
        current = themeapply.find(themeapply.current_id()) or themes.default_theme()
        try:
            return apply_and_tell(themes.remix(current, change))
        except ValueError as exc:
            return str(exc)

    def apply_theme(name: str) -> str:
        theme = themeapply.find(name)
        if theme is None:
            return f"Non ho un tema «{name}». «I miei temi» per l'elenco, o chiedimi di crearlo."
        themeapply.apply(theme, runner)
        return f"Tema «{theme.name}» applicato."

    def previous_theme() -> str:
        theme = themeapply.previous()
        themeapply.apply(theme, runner)
        return f"Ripristinato il tema «{theme.name}»."

    def list_themes() -> str:
        current = themeapply.current_id()
        names = [f"{'● ' if t.id == current else '  '}{t.name}" for t in [themes.default_theme(), *themes.installed()]]
        return "I tuoi temi:\n" + "\n".join(names)

    def market_search(query: str = "") -> str:
        found = thememarket.search(query)
        if not found:
            return "Nel market non ho trovato temi (o l'indice non è ancora stato scaricato)."
        return "Dal market:\n" + "\n".join(f"  {l.name} [{l.id}] di {l.author or 'anonimo'} — {l.description}" for l in found[:10])

    def market_install(theme_id: str) -> str:
        try:
            theme = thememarket.install_listing(theme_id, fetch or thememarket._get)
        except (thememarket.ThemeError, OSError) as exc:
            return f"Installazione non riuscita: {exc}"
        themeapply.apply(theme, runner)
        return f"Installato e applicato «{theme.name}»."

    def export_theme(for_market: str = "no") -> str:
        theme = themeapply.find(themeapply.current_id()) or themes.default_theme()
        try:
            data = thememarket.export_package(theme, for_market.lower() in ("sì", "si", "yes", "true"))
        except thememarket.ThemeError as exc:
            return str(exc)
        out = resolve_folder("DOCUMENTS") / f"{theme.id}.aiostheme"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return f"Tema esportato in {out}: puoi condividerlo" + (" o proporlo al market." if for_market else ".")

    return [
        Tool("create_theme", "Crea e applica un tema da una descrizione (es. «stile marino», «autunnale», «alto contrasto»).",
             params(description="Descrizione"), create_theme),
        Tool("create_theme_from_image", "Crea e applica un tema partendo da un disegno o una foto (colori e sfondo).",
             params(["path"], path="Percorso dell'immagine", name="Nome del tema"), create_theme_from_image, reads_private=True),
        Tool("remix_theme", "Ritocca il tema attuale: più scuro, più chiaro, più vivace, più sobrio, più caldo, più freddo.",
             params(change="Ritocco"), remix_theme),
        Tool("apply_theme", "Applica un tema già installato.", params(name="Nome del tema"), apply_theme),
        Tool("previous_theme", "Torna al tema usato prima.", params(), previous_theme),
        Tool("list_themes", "Elenca i temi installati.", params(), list_themes),
        Tool("market_search", "Cerca temi nel market.", params([], query="Cosa cercare"), market_search),
        Tool("market_install", "Scarica, controlla e applica un tema dal market.", params(theme_id="Identificativo"),
             market_install, requires_confirmation=True, sends_out=True),
        Tool("export_theme", "Esporta il tema attuale in un file da condividere (o da proporre al market).",
             params([], for_market=("sì per il market", ["sì", "no"])), export_theme),
    ]


RE_CREATE = re.compile(r"^(?:(?:crea|creami|fammi|fai|fa|genera|disegna)(?:mi)?\s+)?(?:un\s+|il\s+)?(?:nuovo\s+)?tema\s+(?P<d>.+)$")
RE_FROM_IMAGE = re.compile(r"tema\s+(?:partendo\s+)?(?:da|dal|dalla|con)\s+(?:quest[oa]\s+)?(?:disegno|immagine|foto|quadro)\s*(?P<p>[/~]\S+)?")
RE_REMIX = re.compile(r"^(?:rendi\s+(?:il\s+)?tema\s+|tema\s+|lo\s+vorrei\s+|fallo\s+|come\s+questo\s+ma\s+)?(?P<c>più\s+(?:scuro|chiaro|vivace|sobrio|caldo|freddo))$")
RE_PREVIOUS = re.compile(r"^(?:torna|ritorna|rimetti)\s+(?:al|il)\s+tema\s+(?:di\s+prima|precedente)$")
RE_LIST = re.compile(r"^(?:i\s+)?miei\s+temi$|^che\s+temi\s+ho")
RE_APPLY = re.compile(r"^(?:metti|applica|usa|attiva)\s+(?:il\s+)?tema\s+(?P<n>.+)$")
RE_MARKET = re.compile(r"^(?:cerca|mostra(?:mi)?)\s+(?:dei\s+)?temi\s*(?P<q>.*?)\s*(?:nel|sul|dal)\s+market$|^market\s+(?:dei\s+)?temi$")


class ThemesRouter:
    def match(self, text: str) -> Intent | None:
        low = normalize(text)
        m = RE_FROM_IMAGE.search(low)
        if m:
            path = re.search(r"[/~]\S+\.(?:png|jpe?g|webp|gif|bmp)", text, re.I)
            return Intent("create_theme_from_image", {"path": path.group(0)}) if path else None
        if RE_PREVIOUS.match(low):
            return Intent("previous_theme", {})
        if RE_LIST.match(low):
            return Intent("list_themes", {})
        m = RE_REMIX.match(low)
        if m:
            return Intent("remix_theme", {"change": m.group("c")})
        m = RE_MARKET.match(low)
        if m:
            return Intent("market_search", {"query": (m.group("q") or "").strip()})
        m = RE_APPLY.match(low)
        if m:
            return Intent("apply_theme", {"name": m.group("n")})
        m = RE_CREATE.match(low)
        if m:
            return Intent("create_theme", {"description": text.strip()})
        return None
