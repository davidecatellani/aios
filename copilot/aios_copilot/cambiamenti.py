"""Cosa è cambiato in una pagina, misurato: dall'inventario di prima e di dopo (anteprima.INVENTORY_JS) alcune righe
in italiano che dicono cosa è comparso o sparito, cosa ha cambiato colore, misura, posizione, testo, angoli o bordo.

Le legge il programmatore dopo ogni modifica e le legge il nucleo (adattatore «verifica») per dire se la modifica fa
quello che l'utente ha chiesto: confrontare «voglio il meteo rosso» con «il riquadro «Meteo»: sfondo da bianco a
rosso» è alla portata di un modello piccolo; vedere la differenza in due immagini no.
"""

from __future__ import annotations

import colorsys
from collections import Counter, defaultdict
from typing import Any

MAX_LINES = 16
MOVE = 6  # px: sotto non è uno spostamento


def color_name(hex_color: str) -> str:
    """#d62828 → «rosso»: il nome che direbbe una persona (con «scuro»/«chiaro» quando serve)."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return "trasparente"
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    hue *= 360
    if sat < 0.18 or (light > 0.94 and sat < 0.5) or light < 0.08:
        if light > 0.9:
            return "bianco"
        if light < 0.16:
            return "nero"
        return "grigio chiaro" if light > 0.7 else "grigio scuro" if light < 0.35 else "grigio"
    if 15 <= hue < 48 and light < 0.36:
        return "marrone"
    names = ((15, "rosso"), (40, "arancione"), (68, "giallo"), (165, "verde"), (200, "azzurro"), (250, "blu"),
             (290, "viola"), (345, "rosa"), (361, "rosso"))
    name = next(n for limit, n in names if hue < limit)
    if light < 0.25:
        return name + " scuro"
    if light > 0.8:
        return name + " chiaro"
    return name


def _color(c: str) -> str:
    return f"{color_name(c)} ({c})" if c else "nessuno"


def _keyed(items: list[dict[str, Any]]) -> dict[tuple[str, int], int]:
    seen: Counter = Counter()
    out = {}
    for i, e in enumerate(items):
        out[(e["k"], seen[e["k"]])] = i
        seen[e["k"]] += 1
    return out


def match(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[int, int]:
    """Quale elemento di dopo è quale di prima: per com'è fatto (tipo, classe, testo), poi per il posto nella pagina."""
    ka, kb = _keyed(a), _keyed(b)
    pairs = {ia: kb[key] for key, ia in ka.items() if key in kb}
    taken = set(pairs.values())
    by_path = {e["p"]: j for j, e in enumerate(b) if j not in taken}
    for i, e in enumerate(a):
        j = by_path.get(e["p"])
        if i not in pairs and j is not None and j not in taken and b[j]["k"].split("|")[0] == e["k"].split("|")[0]:
            pairs[i] = j
            taken.add(j)
    return pairs


def _direction(dx: int, dy: int) -> str:
    parts = []
    if abs(dy) >= MOVE:
        parts.append(f"{abs(dy)} px {'in basso' if dy > 0 else 'in alto'}")
    if abs(dx) >= MOVE:
        parts.append(f"{abs(dx)} px {'a destra' if dx > 0 else 'a sinistra'}")
    return " e ".join(parts)


def _line(x: str) -> str:
    """«4 #d62828» → «verde (4 px)»."""
    width, color = x.split(" ", 1)
    return f"{color_name(color)} ({width} px)"


def _where(e: dict[str, Any], others: list[dict[str, Any]]) -> str:
    """Dove sta un elemento nuovo: sotto quale elemento che c'era già e sopra quale (i più vicini, in colonna)."""
    x, y, w, h = e["b"]
    above: tuple[float, str] | None = None
    below: tuple[float, str] | None = None
    for o in others:
        ox, oy, ow, oh = o["b"]
        if min(x + w, ox + ow) - max(x, ox) < 8 or o["l"].startswith("il blocco"):
            continue
        if oy + oh <= y + 4 and (above is None or y - (oy + oh) < above[0]):
            above = (y - (oy + oh), o["l"])
        elif oy >= y + h - 4 and (below is None or oy - (y + h) < below[0]):
            below = (oy - (y + h), o["l"])
    parts = [f"sotto {above[1]}"] if above and above[0] < 120 else []
    parts += [f"sopra {below[1]}"] if below and below[0] < 120 else []
    return f" ({', '.join(parts)})" if parts else ""


def describe(before: dict[str, Any] | None, after: dict[str, Any] | None, problems_before: list[str] = (),
             problems_after: list[str] = (), errors: list[str] = ()) -> list[str]:
    """Le righe che descrivono il cambiamento (vuoto se la pagina è uguale)."""
    out: list[str] = []
    if errors:
        out += [f"errore di JavaScript: {e}" for e in errors[:3]]
    if not before or not after:
        return out + [f"problema nella pagina: {p}" for p in problems_after if p not in problems_before]
    a, b = before.get("elementi") or [], after.get("elementi") or []
    if before.get("fondo") != after.get("fondo"):
        out.append(f"sfondo della pagina: da {_color(before.get('fondo', ''))} a {_color(after.get('fondo', ''))}")
    pairs = match(a, b)
    back = {j: i for i, j in pairs.items()}
    removed = [i for i in range(len(a)) if i not in pairs]
    added = [j for j in range(len(b)) if j not in back]

    if len(removed) >= 8 and len(removed) > 0.6 * len(a):
        out.append(f"la pagina è quasi vuota: sono spariti {len(removed)} elementi su {len(a)}")
    else:
        gone = [i for i in removed if a[i]["par"] not in removed]
        for i in gone[:5]:
            out.append(f"sparito: {a[i]['l']}")
        if len(gone) > 5:
            out.append(f"… e altri {len(gone) - 5} elementi spariti")
    labels = Counter(e["l"] for e in b)
    new = [j for j in added if b[j]["par"] not in added]
    kept = [b[j] for j in back]
    for j in new[:5]:
        e = b[j]
        twin = f", ora ce ne sono {labels[e['l']]} uguali" if labels[e["l"]] > 1 and not e["l"].startswith("il blocco") else ""
        out.append(f"nuovo: {e['l']}{_where(e, kept)}{twin}")
    if len(new) > 5:
        out.append(f"… e altri {len(new) - 5} elementi nuovi")

    geometry: set[int] = set()
    moves: list[tuple[int, int, str]] = []
    colors: list[str] = []
    other: list[str] = []
    changed_props: dict[int, set[tuple[str, str, str]]] = defaultdict(set)
    for i in range(len(a)):
        if i not in pairs:
            continue
        j = pairs[i]
        p, q = a[i], b[j]
        label = p["l"]  # come si chiamava prima: è così che l'utente l'ha chiamato
        parent = p["par"]
        if p["t"] != q["t"] and p["t"] and q["t"]:
            other.append(f"testo cambiato: «{p['t'][:50]}» → «{q['t'][:50]}»")
        (x0, y0, w0, h0), (x1, y1, w1, h1) = p["b"], q["b"]
        resized = (abs(w1 - w0) > 4 and abs(w1 - w0) > 0.08 * w0) or (abs(h1 - h0) > 4 and abs(h1 - h0) > 0.08 * h0)
        moved = abs(x1 - x0) >= MOVE or abs(y1 - y0) >= MOVE
        if resized or moved:
            geometry.add(i)
        top = parent not in geometry
        if top and p["fs"] and q["fs"] and abs(q["fs"] - p["fs"]) >= 2:
            other.append(f"{label}: testo più {'grande' if q['fs'] > p['fs'] else 'piccolo'} ({p['fs']} → {q['fs']} px)")
        elif top and resized and p["t"] == q["t"]:
            bigger = w1 * h1 > w0 * h0
            other.append(f"{label}: più {'grande' if bigger else 'piccolo'} ({w0}×{h0} → {w1}×{h1} px)")
        if top and moved and not resized:
            moves.append((x1 - x0, y1 - y0, label))
        elif top and moved and resized and (abs(x1 - x0) > 0.25 * w0 or abs(y1 - y0) > 0.25 * h0):
            moves.append((x1 - x0, y1 - y0, label))
        for prop, what in (("bg", "sfondo"), ("c", "colore del testo")):
            if p[prop] != q[prop] and (p[prop] or q[prop]):
                change = (prop, p[prop], q[prop])
                changed_props[i].add(change)
                if change not in changed_props.get(parent, set()):
                    colors.append(f"{label}: {what} da {_color(p[prop])} a {_color(q[prop])}")
        for prop in ("ol", "bd"):
            if p[prop] != q[prop]:
                if not p[prop]:
                    other.append(f"{label}: nuovo bordo {_line(q[prop])}")
                elif not q[prop]:
                    other.append(f"{label}: tolto il bordo {_line(p[prop])}")
                else:
                    other.append(f"{label}: bordo da {_line(p[prop])} a {_line(q[prop])}")
        if abs(q["r"] - p["r"]) >= 3:
            other.append(f"{label}: angoli {'più tondi' if q['r'] > p['r'] else 'meno tondi'} ({p['r']} → {q['r']} px)"
                         + (" (squadrati)" if q["r"] == 0 else ""))
        if abs(q["op"] - p["op"]) >= 0.15:
            other.append(f"{label}: trasparenza da {p['op']:.2f} a {q['op']:.2f}")

    groups: dict[tuple[int, int], list[tuple[int, int, str]]] = defaultdict(list)
    for m in moves:
        groups[(round(m[0] / 4), round(m[1] / 4))].append(m)
    move_lines, shifted = [], []
    for g in groups.values():
        if len(g) >= 3:  # tanti insieme dello stesso tanto: il resto della pagina che fa posto
            shifted.append(f"si sono spostati di {_direction(g[0][0], g[0][1])} {len(g)} elementi insieme (il resto della pagina)")
        else:
            move_lines += [f"{label}: spostato di {_direction(dx, dy)}" for dx, dy, label in g]
    if len(colors) > 8:
        colors = colors[:4] + [f"… e altri {len(colors) - 4} cambi di colore (quasi tutta la pagina)"]
    out += other + colors + move_lines + shifted
    new_problems = [p for p in problems_after if p not in problems_before]
    out += [f"problema nella pagina: {p}" for p in new_problems[:4]]
    out = list(dict.fromkeys(out))
    if len(out) > MAX_LINES:
        out = out[:MAX_LINES - 1] + [f"… e altri {len(out) - MAX_LINES + 1} cambiamenti"]
    return out


def from_reports(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Dai JSON salvati accanto alle foto (anteprima.REPORT_JS)."""
    old_errors = set(before.get("errori") or [])
    return describe(before.get("inventario"), after.get("inventario"), before.get("problemi") or [],
                    after.get("problemi") or [], [e for e in after.get("errori") or [] if e not in old_errors])
