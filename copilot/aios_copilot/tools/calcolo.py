"""La calcolatrice di Nova: i conti li fa il codice, non il modello «a occhio».

I modelli piccoli sbagliano somme, medie e differenze, e ancora di più i giorni tra due date. Qui un'espressione
in italiano o in notazione normale («84,20 + 91,10 + 78,50», «(32+28,4+41,6)/3», «giorni tra 2026-10-05 e
2026-11-15») si calcola esattamente, con i decimali giusti (Decimal, niente 0,30000000000000004).
Niente eval: solo numeri, + - * / % ^, parentesi e qualche funzione (somma, media, min, max, arrotonda, giorni).
"""

from __future__ import annotations

import ast
import operator
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable

from .base import Tool, params

MAX_LEN = 500
MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
        "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}


class CalcError(ValueError):
    pass


def parse_date(text: str, today: date | None = None) -> date:
    t = text.strip().lower()
    today = today or date.today()
    if t in ("oggi", "adesso", "ora"):
        return today
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[t ].*)?", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t)
    if m:
        y = int(m.group(3))
        return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(?:\w+\s+)?(\d{1,2})(?:°)?\s+([a-z]+)(?:\s+(\d{4}))?", t)
    if m and m.group(2) in MESI:
        return date(int(m.group(3) or today.year), MESI[m.group(2)], int(m.group(1)))
    raise CalcError(f"data non riconosciuta: «{text}» (usa 2026-11-15 o 15 novembre 2026)")


def _days(a: str, b: str, today: date | None = None) -> Decimal:
    return Decimal((parse_date(b, today) - parse_date(a, today)).days)


_OPS: dict[type, Callable[[Any, Any], Any]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                                              ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_FUNCS: dict[str, Callable[..., Decimal]] = {
    "somma": lambda *x: sum(x, Decimal(0)), "sum": lambda *x: sum(x, Decimal(0)),
    "media": lambda *x: sum(x, Decimal(0)) / len(x), "avg": lambda *x: sum(x, Decimal(0)) / len(x),
    "min": lambda *x: min(x), "max": lambda *x: max(x), "abs": lambda x: abs(x),
    "arrotonda": lambda x, n=Decimal(2): x.quantize(Decimal(1).scaleb(-int(n)), rounding=ROUND_HALF_UP),
    "round": lambda x, n=Decimal(2): x.quantize(Decimal(1).scaleb(-int(n)), rounding=ROUND_HALF_UP),
}


def _normalize(expr: str) -> tuple[str, list[str]]:
    """Virgole decimali, €, «x» per moltiplicare; le date tra virgolette diventano segnaposto."""
    dates: list[str] = []

    def keep(m: re.Match[str]) -> str:
        dates.append(m.group(1))
        return f"__d{len(dates) - 1}"

    e = re.sub(r"[\"'«»]([^\"'«»]+)[\"'«»]", keep, expr)
    e = re.sub(r"\b(\d{4}-\d{1,2}-\d{1,2})\b", keep, e)
    e = e.replace("€", "").replace("euro", "").replace("×", "*").replace("÷", "/").replace("^", "**")
    e = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "*", e)
    e = re.sub(r"(?<=\d)\.(?=\d{3}(?!\d))(?=\d{3},)", "", e)  # 1.234,56 → 1234,56
    e = re.sub(r"(?<=\d),(?=\d)", ".", e).replace(";", ",")
    e = re.sub(r"\bgiorni\s+(?:tra|fra|da)\s+(__d\d+|oggi)\s+(?:e|a)\s+(__d\d+|oggi)", r"giorni(\1, \2)", e)
    return e, dates


def evaluate(expr: str, today: date | None = None) -> Decimal:
    if len(expr) > MAX_LEN:
        raise CalcError("espressione troppo lunga")
    whole = re.fullmatch(r"\s*(?:quanti\s+)?giorni\s+(?:tra|fra|da|dal|dall')\s*(.+?)\s+(?:e|a|al|all')\s*(.+?)\s*\??", expr, re.I)
    if whole:  # «giorni tra oggi e 15 novembre 2026»: le date scritte a parole
        return _days(whole.group(1).strip("'\"«» "), whole.group(2).strip("'\"«» "), today)
    text, dates = _normalize(expr)
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"non capisco «{expr}»") from exc

    def date_arg(node: ast.AST) -> str:
        if isinstance(node, ast.Name) and node.id.startswith("__d"):
            return dates[int(node.id[3:])]
        if isinstance(node, ast.Name) and node.id == "oggi":
            return "oggi"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        raise CalcError("giorni() vuole due date, es. giorni('2026-10-05', '2026-11-15')")

    def ev(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise CalcError("esponente troppo grande")
            try:
                return _OPS[type(node.op)](left, right)
            except (ZeroDivisionError, InvalidOperation) as exc:
                raise CalcError("divisione per zero") from exc
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            if name in ("giorni", "days"):
                if len(node.args) != 2:
                    raise CalcError("giorni() vuole due date")
                return _days(date_arg(node.args[0]), date_arg(node.args[1]), today)
            if name in _FUNCS and node.args:
                return _FUNCS[name](*[ev(a) for a in node.args])
        if isinstance(node, (ast.List, ast.Tuple)):
            raise CalcError("usa somma(…) o media(…) per un elenco di numeri")
        raise CalcError(f"non so calcolare «{ast.unparse(node) if hasattr(ast, 'unparse') else expr}»")

    return ev(tree)


def fmt(value: Decimal) -> str:
    """All'italiana: virgola decimale, al massimo 2 decimali (4 se il numero è piccolo)."""
    places = Decimal("0.01") if abs(value) >= 1 or value == 0 else Decimal("0.0001")
    v = value.quantize(places, rounding=ROUND_HALF_UP).normalize()
    text = f"{v:f}"
    if "." in text:
        whole, dec = text.split(".")
        dec = dec.ljust(2, "0") if places == Decimal("0.01") else dec
        text = f"{whole},{dec}"
    return text


def calculate(espressione: str, today: date | None = None) -> str:
    try:
        return f"{espressione.strip()} = {fmt(evaluate(espressione, today))}"
    except CalcError as exc:
        return f"Errore nel calcolo: {exc}"


def make_tools(today: Callable[[], date] = date.today) -> list[Tool]:
    def calc(espressione: str) -> str:
        return calculate(espressione, today())

    return [Tool(
        "calculate",
        "Calcola un'espressione esatta: somme, differenze, medie, percentuali, e i giorni tra due date. Usala SEMPRE "
        "per i conti, non farli a mente. Esempi: «84,20 + 91,10 + 78,50», «media(84,20; 91,10; 78,50)» si scrive "
        "«media(84.20, 91.10, 78.50)», «(91,10 - 78,50) / 78,50 * 100», «giorni tra 2026-10-05 e 2026-11-15».",
        params(espressione="L'espressione da calcolare", required=["espressione"]), calc)]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="minutes")
