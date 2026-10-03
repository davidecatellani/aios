"""Temi di AIOS: colori, forme, sfondo e copilota. Un tema è SOLO DATI, mai codice.

Si creano parlando: «crea un tema in stile marino», «un tema autunnale», «un tema
partendo da questo disegno», «come questo ma più scuro». La leggibilità è garantita:
ogni coppia testo/sfondo rispetta il contrasto WCAG (≥ 4,5:1) e viene corretta se serve.

Lo sfondo è un'immagine (da un disegno o dal modello di immagini) oppure una «ricetta»
(onde, montagne, stelle, energia...) che AIOS disegna da sé: funziona su ogni dispositivo
e nei pacchetti del market non viaggia mai codice (niente SVG altrui).
"""

from __future__ import annotations

import colorsys
import json
import math
import os
import random
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .privacy import private_dir

FONTS = ("Inter", "Cantarell", "Noto Sans", "Atkinson Hyperlegible", "Comic Neue", "Lexend", "Fira Sans", "Ubuntu")
STYLES = ("onde", "montagne", "stelle", "energia", "bolle", "righe", "gradiente", "foglie")
MIN_CONTRAST = 4.5


# --- colori -------------------------------------------------------------------------------------


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def luminance(h: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in hex_to_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def hls(h: str) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(*hex_to_rgb(h))


def from_hls(hue: float, light: float, sat: float) -> str:
    return rgb_to_hex(colorsys.hls_to_rgb(hue % 1, max(0, min(1, light)), max(0, min(1, sat))))


def ensure_contrast(fg: str, bg: str, minimum: float = MIN_CONTRAST) -> str:
    """Schiarisce o scurisce `fg` (stessa tinta) finché non si legge bene su `bg`."""
    if contrast(fg, bg) >= minimum:
        return fg
    hue, light, sat = hls(fg)
    darker = luminance(bg) > 0.18
    for _ in range(100):
        light += -0.01 if darker else 0.01
        fg = from_hls(hue, light, sat)
        if contrast(fg, bg) >= minimum or light <= 0 or light >= 1:
            break
    return fg if contrast(fg, bg) >= minimum else ("#000000" if darker else "#ffffff")


# --- il tema --------------------------------------------------------------------------------------


@dataclass
class Palette:
    bg: str
    surface: str
    text: str
    muted: str
    accent: str
    accent2: str
    on_accent: str  # testo sopra il colore d'accento
    line: str


@dataclass
class Theme:
    id: str
    name: str
    description: str
    light: Palette
    dark: Palette
    radius: int = 14
    font: str = "Inter"
    wallpaper: dict = field(default_factory=dict)  # {"kind": "ricetta", "style": "onde", "colors": [...], "seed": 1} | {"kind": "immagine", "file": "..."}
    orb: list[str] = field(default_factory=list)
    origin: str = "ai"  # ai | immagine | market | integrato
    personal_only: bool = False  # ispirato a marchi o personaggi: non pubblicabile sul market
    author: str = ""
    license: str = "cc-by-4.0"
    version: int = 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    @staticmethod
    def from_dict(d: dict) -> "Theme":
        d = dict(d)
        d["light"], d["dark"] = Palette(**d["light"]), Palette(**d["dark"])
        known = set(Theme.__dataclass_fields__)
        return Theme(**{k: v for k, v in d.items() if k in known})

    def css(self) -> str:
        """Variabili CSS per le app di AIOS (stessi nomi usati da benvenuto e posta)."""
        def block(p: Palette) -> str:
            return (f"--bg: {p.bg}; --surface: color-mix(in srgb, {p.surface} 88%, transparent); --surface-strong: {p.surface};"
                    f" --solid: {p.surface}; --soft: color-mix(in srgb, {p.accent} 7%, {p.surface}); --side: color-mix(in srgb, {p.accent} 6%, {p.bg});"
                    f" --text: {p.text}; --muted: {p.muted}; --line: color-mix(in srgb, {p.text} 12%, transparent);"
                    f" --accent: {p.accent}; --accent-2: {p.accent2}; --accent2: {p.accent2}; --accent-text: {p.on_accent};"
                    f" --user: {p.accent}; --user-text: {p.on_accent};"
                    f" --bg-glow-1: color-mix(in srgb, {p.accent} 35%, {p.bg}); --bg-glow-2: color-mix(in srgb, {p.accent2} 30%, {p.bg});"
                    f" --glow1: color-mix(in srgb, {p.accent} 35%, {p.bg}); --glow2: color-mix(in srgb, {p.accent2} 30%, {p.bg});"
                    f" --radius: {self.radius}px; font-family: '{self.font}', Inter, Cantarell, system-ui, sans-serif;")
        return (f"/* Tema AIOS: {self.name} */\n:root {{ {block(self.light)} }}\n"
                f"@media (prefers-color-scheme: dark) {{ :root {{ {block(self.dark)} }} }}\n")


def build_palettes(base: str, second: str, warm_bg: bool = False, high_contrast: bool = False) -> tuple[Palette, Palette]:
    """Da due colori (principale e secondario) a palette chiara e scura, sempre leggibili."""
    hue, _, sat = hls(base)
    hue2, _, sat2 = hls(second)
    tint = min(sat, 0.5)
    light_bg = from_hls(hue2 if warm_bg else hue, 0.965, tint * 0.6)
    light_surface = from_hls(hue, 0.995, tint * 0.3)
    dark_bg = from_hls(hue, 0.07, tint * 0.6)
    dark_surface = from_hls(hue, 0.13, tint * 0.5)
    minimum = 7.0 if high_contrast else MIN_CONTRAST

    def palette(bg: str, surface: str, dark: bool) -> Palette:
        text = from_hls(hue, 0.95 if dark else 0.10, 0.15)
        text = "#ffffff" if high_contrast and dark else "#000000" if high_contrast else text
        muted = ensure_contrast(from_hls(hue, 0.72 if dark else 0.38, 0.12), surface, minimum)
        accent = ensure_contrast(from_hls(hue, 0.66 if dark else 0.42, max(sat, 0.45)), surface, 3.0 if not high_contrast else minimum)
        accent2 = ensure_contrast(from_hls(hue2, 0.62 if dark else 0.45, max(sat2, 0.4)), surface, 3.0 if not high_contrast else minimum)
        on_accent = "#ffffff" if contrast("#ffffff", accent) >= contrast("#111111", accent) else "#111111"
        line = from_hls(hue, 0.25 if dark else 0.85, tint * 0.4)
        return Palette(bg, surface, ensure_contrast(text, surface, minimum), muted, accent, accent2, on_accent, line)

    return palette(light_bg, light_surface, False), palette(dark_bg, dark_surface, True)


# --- atmosfere conosciute (subito, senza modello AI) ------------------------------------------------

MOODS: list[tuple[re.Pattern[str], dict]] = [
    (re.compile(r"mar[ei]n|mare|ocean|spiaggi|onde|acqua|lago|estate al mare", re.I),
     {"name": "Marino", "base": "#0b6e99", "second": "#e9c46a", "style": "onde", "radius": 18, "font": "Lexend",
      "extra": ["#2ec4b6", "#a8dadc"]}),
    (re.compile(r"autunn|foglie|castagn|ottobre", re.I),
     {"name": "Autunno", "base": "#b5542d", "second": "#d9a441", "style": "foglie", "radius": 12, "font": "Fira Sans",
      "extra": ["#7f4f24", "#e76f51"], "warm": True}),
    (re.compile(r"bosc|forest|natur|verde|montagn|alpin", re.I),
     {"name": "Bosco", "base": "#2d6a4f", "second": "#b7a26b", "style": "montagne", "radius": 12, "font": "Noto Sans",
      "extra": ["#52b788", "#1b4332"]}),
    (re.compile(r"spazio|stell|galass|notte|cosmo|universo", re.I),
     {"name": "Spazio", "base": "#5a4fcf", "second": "#ff9f1c", "style": "stelle", "radius": 16, "font": "Inter",
      "extra": ["#1b1446", "#c77dff"]}),
    (re.compile(r"invern|neve|ghiacc|natale|dicembre", re.I),
     {"name": "Inverno", "base": "#3a86ff", "second": "#c1121f", "style": "stelle", "radius": 16, "font": "Cantarell",
      "extra": ["#e0fbfc", "#98c1d9"]}),
    (re.compile(r"tramont|desert|sabbi|sole|caldo", re.I),
     {"name": "Tramonto", "base": "#e76f51", "second": "#f4a261", "style": "gradiente", "radius": 20, "font": "Lexend",
      "extra": ["#264653", "#e9c46a"], "warm": True}),
    (re.compile(r"primaver|fiori|pastell|caramell|rosa", re.I),
     {"name": "Primavera", "base": "#e05780", "second": "#7bc47f", "style": "bolle", "radius": 22, "font": "Comic Neue",
      "extra": ["#ffc8dd", "#bde0fe"]}),
    (re.compile(r"retr[oò]|anni.?80|synthwave|neon|arcade|cyberpunk", re.I),
     {"name": "Neon", "base": "#ff2e88", "second": "#00e5ff", "style": "righe", "radius": 6, "font": "Fira Sans",
      "extra": ["#2b0a3d", "#ffd400"]}),
    (re.compile(r"dragon.?ball|goku|saiyan|anime.*energ|energia", re.I),
     {"name": "Energia", "base": "#f28c28", "second": "#1e4fa3", "style": "energia", "radius": 10, "font": "Lexend",
      "extra": ["#ffd400", "#e63946"], "personal": True}),
    (re.compile(r"alto contrasto|ipovedent|si legg[ae] meglio|leggibil|nonn[ao]", re.I),
     {"name": "Alto contrasto", "base": "#0043ce", "second": "#b8860b", "style": "gradiente", "radius": 10,
      "font": "Atkinson Hyperlegible", "extra": [], "high": True}),
    (re.compile(r"minimal|sobrio|elegante|grigio|lavoro|ufficio", re.I),
     {"name": "Sobrio", "base": "#4a5568", "second": "#718096", "style": "gradiente", "radius": 8, "font": "Inter",
      "extra": []}),
]
# Nomi di opere e marchi: il tema è «ispirato a», per uso personale, mai pubblicato sul market.
FRANCHISE = re.compile(r"dragon.?ball|pok[eé]mon|disney|marvel|star wars|harry potter|nintendo|mario|naruto|one piece|"
                       r"barbie|lego|ferrari|juventus|inter\b|milan\b|roma\b|batman|spider.?man|frozen|minecraft|fortnite", re.I)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "tema"


def subject(description: str) -> str:
    """«crea un tema in stile Dragon Ball» → «Dragon Ball»."""
    text = re.sub(r"^(?:(?:crea|fammi|fai|voglio|vorrei)\s+)?(?:(?:un|il)\s+)?(?:nuovo\s+)?tema\s+(?:(?:in\s+)?stile\s+|a\s+tema\s+|di\s+|del\s+|della\s+|ispirato\s+a\s+)?",
                  "", description.strip(), flags=re.I)
    return " ".join(w if w[:1].isupper() else w.capitalize() for w in text.split()) or description


def theme_from_spec(spec: dict, description: str, seed: int = 1) -> Theme:
    light, dark = build_palettes(spec["base"], spec["second"], spec.get("warm", False), spec.get("high", False))
    personal = bool(spec.get("personal") or FRANCHISE.search(description))
    name = spec["name"]
    colors = [spec["base"], spec["second"], *spec.get("extra", [])]
    about = subject(description)
    return Theme(_slug(name if not personal else about), name if not personal else f"{name} (ispirato a {about})",
                 description, light, dark, int(spec.get("radius", 14)),
                 spec.get("font") if spec.get("font") in FONTS else "Inter",
                 {"kind": "ricetta", "style": spec.get("style", "gradiente"), "colors": colors, "seed": seed},
                 [light.accent, light.accent2, colors[-1]], "ai", personal)


def from_description(description: str, ask_llm: Callable[[str], str] | None = None, seed: int = 1) -> Theme:
    """Tema da una descrizione: atmosfere conosciute subito, le altre chiedendo una palette al modello."""
    for pattern, spec in MOODS:
        if pattern.search(description):
            return theme_from_spec(spec, description, seed)
    if ask_llm is not None:
        reply = ask_llm(
            "Proponi un tema grafico per un sistema operativo ispirato a: " + description +
            '. Rispondi SOLO con JSON: {"name": "...", "base": "#rrggbb", "second": "#rrggbb", "extra": ["#rrggbb", "#rrggbb"], '
            f'"style": uno tra {list(STYLES)}, "radius": 4-24, "font": uno tra {list(FONTS)}}}')
        spec = _parse_llm_spec(reply)
        if spec:
            return theme_from_spec(spec, description, seed)
    # Ripiego: un colore dalla descrizione stessa, così il risultato è stabile.
    hue = (sum(map(ord, description)) % 360) / 360
    spec = {"name": description.strip().capitalize()[:30] or "Personale", "base": from_hls(hue, 0.45, 0.6),
            "second": from_hls(hue + 0.4, 0.5, 0.55), "style": "gradiente"}
    return theme_from_spec(spec, description, seed)


def _parse_llm_spec(reply: str) -> dict | None:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return None
    try:
        spec = json.loads(m.group(0))
    except ValueError:
        return None
    hexrx = re.compile(r"^#[0-9a-fA-F]{6}$")
    if not all(isinstance(spec.get(k), str) and hexrx.match(spec[k]) for k in ("base", "second")):
        return None
    spec["extra"] = [c for c in spec.get("extra", []) if isinstance(c, str) and hexrx.match(c)][:3]
    spec["style"] = spec.get("style") if spec.get("style") in STYLES else "gradiente"
    try:
        spec["radius"] = max(4, min(24, int(spec.get("radius", 14))))
    except (TypeError, ValueError):
        spec["radius"] = 14
    spec["name"] = str(spec.get("name", "Personale"))[:30]
    return spec


# --- da un disegno o una foto ---------------------------------------------------------------------


def image_pixels(path: Path, run: Callable[[list[str]], bytes] | None = None) -> list[tuple[int, int, int]]:
    """Pixel di una versione 48×48 dell'immagine (ffmpeg legge qualsiasi formato)."""
    run = run or (lambda c: subprocess.run(c, capture_output=True, timeout=60).stdout)
    if not shutil.which("ffmpeg") and run is None:
        return []
    raw = run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", "scale=48:48", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw) - 2, 3)]  # type: ignore[misc]


def dominant_colors(pixels: list[tuple[int, int, int]], k: int = 6, seed: int = 7) -> list[tuple[str, float]]:
    """k-means sui pixel: i colori principali con la loro quota."""
    if not pixels:
        return []
    rnd = random.Random(seed)
    centers = [list(p) for p in rnd.sample(pixels, min(k, len(pixels)))]
    for _ in range(12):
        groups: list[list[tuple[int, int, int]]] = [[] for _ in centers]
        for p in pixels:
            i = min(range(len(centers)), key=lambda c: sum((p[j] - centers[c][j]) ** 2 for j in range(3)))
            groups[i].append(p)
        centers = [[sum(c[j] for c in g) / len(g) for j in range(3)] if g else centers[n] for n, g in enumerate(groups)]
    out = [(rgb_to_hex(tuple(c / 255 for c in center)), len(g) / len(pixels)) for center, g in zip(centers, groups) if g]
    return sorted(out, key=lambda x: -x[1])


def from_image(path: Path, name: str = "", pixels: list[tuple[int, int, int]] | None = None) -> Theme:
    colors = dominant_colors(pixels if pixels is not None else image_pixels(path))
    if not colors:
        raise ValueError("Non riesco a leggere i colori di questa immagine.")
    # Il colore principale è il più «vivo» tra quelli abbondanti; il secondo il più diverso da lui.
    vivid = sorted(colors, key=lambda c: -(hls(c[0])[2] * min(1, c[1] * 5) * (1 - abs(hls(c[0])[1] - 0.5))))
    base = vivid[0][0]
    second = max((c for c, _ in colors if c != base), key=lambda c: abs(hls(c)[0] - hls(base)[0]), default=base)
    light, dark = build_palettes(base, second)
    title = name or f"Da {path.stem}"
    return Theme(_slug(title), title, f"creato da {path.name}", light, dark, 16, "Inter",
                 {"kind": "immagine", "file": path.name, "source": str(path)}, [light.accent, light.accent2, base], "immagine")


# --- ritocchi («come questo ma più scuro») -----------------------------------------------------------

REMIX = {
    "più scuro": lambda h, l, s: (h, l - 0.08, s), "più chiaro": lambda h, l, s: (h, l + 0.08, s),
    "più vivace": lambda h, l, s: (h, l, s + 0.2), "più sobrio": lambda h, l, s: (h, l, s - 0.25),
    "più caldo": lambda h, l, s: (_warmer(h, 0.06), l, s),
    "più freddo": lambda h, l, s: (_cooler(h, 0.06), l, s),
}


def _warmer(h: float, step: float) -> float:
    """Più caldo: gialli e verdi verso l'arancio; blu e viola verso il rosso (un blu caldo tende al viola)."""
    if 0.08 < h <= 0.5:
        return max(0.08, h - step)
    if h > 0.5:
        return min(1.0, h + step) % 1
    return min(0.08, h + step)


def _cooler(h: float, step: float) -> float:
    """Più freddo: gialli e verdi verso il ciano; blu verso il ciano; rossi e magenta verso il viola."""
    if 0.1 <= h < 0.5:
        return min(0.5, h + step)
    if 0.5 <= h <= 0.66:
        return max(0.5, h - step)
    if h > 0.66:
        return max(0.66, h - step)
    return (h - step) % 1


def remix(theme: Theme, change: str) -> Theme:
    """Ritocca tutti i colori del tema, poi ricontrolla la leggibilità."""
    key = next((k for k in REMIX if k in change.lower()), None)
    if key is None:
        raise ValueError(f"Ritocchi possibili: {', '.join(REMIX)}.")

    def tf(c: str) -> str:
        return from_hls(*REMIX[key](*hls(c)))

    def retouch(p: Palette) -> Palette:
        surface = tf(p.surface)
        accent = ensure_contrast(tf(p.accent), surface, 3.0)
        on_accent = "#ffffff" if contrast("#ffffff", accent) >= contrast("#111111", accent) else "#111111"
        return Palette(tf(p.bg), surface, ensure_contrast(p.text, surface), ensure_contrast(tf(p.muted), surface),
                       accent, ensure_contrast(tf(p.accent2), surface, 3.0), on_accent, tf(p.line))

    new = Theme.from_dict(json.loads(theme.to_json()))
    new.light, new.dark = retouch(theme.light), retouch(theme.dark)
    new.id, new.name, new.version = f"{theme.id}-{_slug(key)}", f"{theme.name} · {key}", 1
    new.orb = [new.light.accent, new.light.accent2, *theme.orb[2:]]
    if theme.wallpaper.get("kind") == "ricetta":
        new.wallpaper = {**theme.wallpaper, "colors": [tf(c) for c in theme.wallpaper.get("colors", [])]}
    return new


# --- sfondi disegnati da AIOS ------------------------------------------------------------------------


def wallpaper_svg(recipe: dict, width: int = 1920, height: int = 1080, dark: bool = False) -> str:
    """Disegna lo sfondo da una ricetta: niente file esterni, niente script."""
    colors = [c for c in recipe.get("colors", []) if re.fullmatch(r"#[0-9a-fA-F]{6}", c)] or ["#5b4cf0", "#18a7e0"]
    rnd = random.Random(recipe.get("seed", 1))
    style = recipe.get("style", "gradiente")
    c0, c1 = colors[0], colors[1 % len(colors)]
    shade = (lambda c, f: from_hls(hls(c)[0], hls(c)[1] * f, hls(c)[2])) if dark else (lambda c, f: c)
    parts = [f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{shade(c0, .45)}"/>'
             f'<stop offset="1" stop-color="{shade(c1, .45)}"/></linearGradient></defs>',
             f'<rect width="{width}" height="{height}" fill="url(#g)"/>']
    if style == "onde":
        for i in range(6):
            y = height * (0.45 + i * 0.1)
            amp, phase = 40 + i * 12, rnd.random() * 6.28
            pts = " ".join(f"L{x} {y + amp * math.sin(x / 260 + phase):.0f}" for x in range(0, width + 80, 80))
            col = colors[(i + 2) % len(colors)]
            parts.append(f'<path d="M0 {height} L0 {y:.0f} {pts} L{width} {height} Z" fill="{shade(col, .6)}" opacity="{0.25 + i * 0.1:.2f}"/>')
    elif style == "montagne":
        for i in range(4):
            base_y = height * (0.55 + i * 0.12)
            pts, x = [], 0
            while x <= width:
                pts.append(f"L{x} {base_y - rnd.randint(60, 260 - i * 40)}")
                x += rnd.randint(140, 260)
            col = colors[(i + 2) % len(colors)]
            parts.append(f'<path d="M0 {height} {" ".join(pts)} L{width} {height} Z" fill="{shade(col, .5 + i * .1)}" opacity="{0.5 + i * 0.15:.2f}"/>')
    elif style == "stelle":
        for _ in range(220):
            parts.append(f'<circle cx="{rnd.randint(0, width)}" cy="{rnd.randint(0, height)}" r="{rnd.choice([1, 1, 1.5, 2, 3])}" '
                         f'fill="#ffffff" opacity="{rnd.uniform(.3, .95):.2f}"/>')
        parts.append(f'<circle cx="{width * .78:.0f}" cy="{height * .28:.0f}" r="{height * .12:.0f}" fill="{colors[-1]}" opacity=".85"/>')
    elif style == "energia":
        cx, cy = width * 0.5, height * 0.55
        for i in range(36):
            a = i / 36 * 6.283
            r1, r2 = height * 0.12, height * rnd.uniform(0.6, 1.1)
            col = colors[i % len(colors)]
            parts.append(f'<path d="M{cx + r1 * math.cos(a):.0f} {cy + r1 * math.sin(a):.0f} L{cx + r2 * math.cos(a - .04):.0f} '
                         f'{cy + r2 * math.sin(a - .04):.0f} L{cx + r2 * math.cos(a + .04):.0f} {cy + r2 * math.sin(a + .04):.0f} Z" '
                         f'fill="{col}" opacity=".35"/>')
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{height * .14:.0f}" fill="{colors[2 % len(colors)]}" opacity=".9"/>')
    elif style == "bolle":
        for _ in range(40):
            parts.append(f'<circle cx="{rnd.randint(0, width)}" cy="{rnd.randint(0, height)}" r="{rnd.randint(20, 160)}" '
                         f'fill="{rnd.choice(colors)}" opacity="{rnd.uniform(.12, .35):.2f}"/>')
    elif style == "righe":
        for i in range(0, height, 36):
            parts.append(f'<rect y="{i}" width="{width}" height="3" fill="{colors[(i // 36) % len(colors)]}" opacity=".35"/>')
        parts.append(f'<circle cx="{width / 2:.0f}" cy="{height * .62:.0f}" r="{height * .25:.0f}" fill="{c1}" opacity=".55"/>')
    elif style == "foglie":
        for _ in range(70):
            x, y, s, a = rnd.randint(0, width), rnd.randint(0, height), rnd.randint(18, 60), rnd.randint(0, 360)
            parts.append(f'<ellipse cx="{x}" cy="{y}" rx="{s}" ry="{s // 2}" transform="rotate({a} {x} {y})" '
                         f'fill="{rnd.choice(colors)}" opacity="{rnd.uniform(.35, .8):.2f}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">{"".join(parts)}</svg>'


# --- archivio dei temi ---------------------------------------------------------------------------------


def themes_dir() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "themes")


def save(theme: Theme, image: Path | None = None) -> Path:
    folder = themes_dir() / theme.id
    folder.mkdir(parents=True, exist_ok=True)
    if theme.wallpaper.get("kind") == "immagine":
        src = image or Path(theme.wallpaper.get("source", ""))
        if src.is_file():
            dest = folder / ("wallpaper" + src.suffix.lower())
            shutil.copyfile(src, dest)
            theme.wallpaper = {"kind": "immagine", "file": dest.name}
    (folder / "theme.json").write_text(theme.to_json())
    if theme.wallpaper.get("kind") == "ricetta":  # versioni pronte dello sfondo, chiara e scura
        (folder / "wallpaper.svg").write_text(wallpaper_svg(theme.wallpaper))
        (folder / "wallpaper-dark.svg").write_text(wallpaper_svg(theme.wallpaper, dark=True))
    return folder


def load(theme_id: str) -> Theme | None:
    try:
        return Theme.from_dict(json.loads((themes_dir() / theme_id / "theme.json").read_text()))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def installed() -> list[Theme]:
    return [t for t in (load(p.name) for p in sorted(themes_dir().iterdir()) if p.is_dir()) if t is not None]


def wallpaper_files(theme: Theme) -> tuple[Path | None, Path | None]:
    folder = themes_dir() / theme.id
    if theme.wallpaper.get("kind") == "ricetta":
        return folder / "wallpaper.svg", folder / "wallpaper-dark.svg"
    if theme.wallpaper.get("kind") == "immagine":
        f = folder / theme.wallpaper.get("file", "")
        return (f, f) if f.is_file() else (None, None)
    return None, None


def default_theme() -> Theme:
    light, dark = build_palettes("#5b4cf0", "#18a7e0")
    return Theme("aios", "AIOS", "Il tema predefinito", light, dark, 14, "Inter",
                 {"kind": "ricetta", "style": "gradiente", "colors": ["#5b4cf0", "#18a7e0", "#f06bd0"], "seed": 1},
                 ["#5b4cf0", "#18a7e0", "#f06bd0"], "integrato")
