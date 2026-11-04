"""I conti sulle tabelle fatti dal codice, per le procedure (procedure.py, azione «tabella»): il modello classifica e
scrive, i numeri li somma il computer. Un modello piccolo che deve ricopiare trenta importi in un'espressione ne
sbaglia qualcuno; qui non ricopia niente.

    action: tabella ../01_righe/output/righe.md: calcola totale = quantità * prezzo unitario; somma totale -> righe_totali.md
    action: tabella ../01_classifica/output/movimenti.md: somma importo per categoria -> totali.md

Le tabelle sono quelle in Markdown (| a | b |) o CSV (; , o tabulazione). I numeri si leggono all'italiana
(1.234,50 €) o all'inglese (1234.50) e si scrivono all'italiana.
"""

from __future__ import annotations

import ast
import csv
import io
import operator
import re
import unicodedata
from pathlib import Path


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def parse_number(text: str) -> float | None:
    s = re.sub(r"[€$\s]|eur(o)?", "", str(text).strip().lower())
    s = s.replace("−", "-").replace("–", "-")
    if not re.fullmatch(r"[-+]?[\d.,]*\d[\d.,]*", s or "x"):
        return None
    if "," in s and ("." not in s or s.rfind(",") > s.rfind(".")):
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


NUMBER = re.compile(r"(?<![\d,.])(\d{1,3}(?:\.\d{3})+,\d{1,2}|\d+,\d{1,2}|\d{1,3}(?:,\d{3})+\.\d{1,2}|\d+\.\d{1,2}|\d+)(?![\d,])")


def numbers_in(text: str) -> list[float]:
    """Tutti i numeri di un testo (senza segno), letti all'italiana o all'inglese."""
    return [v for v in (parse_number(raw) for raw in NUMBER.findall(text)) if v is not None]


def fmt(x: float) -> str:
    """1234.5 → «1.234,50»."""
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_table(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [l.strip() for l in text.splitlines()]
    pipe = [l for l in lines if l.startswith("|") and l.count("|") >= 2]
    if pipe:
        rows = [[c.strip() for c in l.strip("|").split("|")] for l in pipe]
        rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]
        return rows[0], [r for r in rows[1:] if any(r)]
    body = "\n".join(l for l in lines if l)
    sample = body.splitlines()[0] if body else ""
    delim = max(";,\t", key=sample.count)
    rows = [r for r in csv.reader(io.StringIO(body), delimiter=delim) if any(c.strip() for c in r)]
    if not rows or len(rows[0]) < 2:
        raise ValueError("non trovo una tabella (né in Markdown né CSV)")
    return [c.strip() for c in rows[0]], [[c.strip() for c in r] for r in rows[1:]]


def column(headers: list[str], name: str) -> int:
    want = _norm(name)
    names = [_norm(h) for h in headers]
    for test in (lambda h: h == want, lambda h: h.startswith(want), lambda h: want in h):
        hits = [i for i, h in enumerate(names) if h and test(h)]
        if hits:
            return hits[0]
    raise ValueError(f"nella tabella non c'è la colonna «{name}» (ci sono: {', '.join(headers)})")


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand)
    raise ValueError("espressione non valida")


def _compute(headers: list[str], rows: list[list[str]], expr: str) -> list[float]:
    parts = re.split(r"([*/+×÷()]|\s-\s)", expr)
    out = []
    for row in rows:
        code = []
        for p in parts:
            token = p.strip()
            if not token:
                continue
            if token in "*/+×÷()" or token == "-":
                code.append({"×": "*", "÷": "/"}.get(token, token))
                continue
            value = parse_number(token)
            if value is None:
                cell = row[column(headers, token)] if column(headers, token) < len(row) else ""
                value = parse_number(cell)
                if value is None:
                    raise ValueError(f"nella riga «{' | '.join(row)}» la colonna «{token}» non è un numero")
            code.append(repr(value))
        out.append(round(_eval(ast.parse(" ".join(code), mode="eval")), 2))
    return out


def run(spec: str, base: Path, root: Path) -> str:
    """«file: operazione; operazione» → il testo del risultato (tabella e somme)."""
    path_text, _, ops = spec.partition(":")
    path = (base / path_text.strip()).resolve()
    if not str(path).startswith(str(root.resolve())):
        raise ValueError("la tabella deve stare nella cartella della procedura")
    headers, rows = parse_table(path.read_text(errors="replace"))
    out, changed = [], False
    for op in [o.strip() for o in ops.split(";") if o.strip()]:
        m = re.match(r"^(?:calcola|compute)\s+(.+?)\s*=\s*(.+)$", op, re.I)
        if m:
            values = _compute(headers, rows, m.group(2))
            headers = headers + [m.group(1).strip()]
            rows = [r + [fmt(v)] for r, v in zip(rows, values)]
            changed = True
            continue
        m = re.match(r"^(?:somma|sum)\s+(.+?)(?:\s+(?:per|by)\s+(.+))?$", op, re.I)
        if not m:
            raise ValueError(f"operazione sconosciuta «{op}» (si può: calcola nome = espressione; somma colonna; somma colonna per colonna)")
        col = column(headers, m.group(1))
        values = []
        for r in rows:
            v = parse_number(r[col]) if col < len(r) else None
            if v is None:
                raise ValueError(f"nella riga «{' | '.join(r)}» «{headers[col]}» non è un numero")
            values.append(v)
        if m.group(2):
            g = column(headers, m.group(2))
            groups: dict[str, float] = {}
            for r, v in zip(rows, values):
                key = r[g].strip() if g < len(r) else ""
                groups[key] = round(groups.get(key, 0.0) + v, 2)
            out.append(f"| {headers[g]} | {headers[col]} |\n|---|---|\n"
                       + "\n".join(f"| {k} | {fmt(v)} |" for k, v in groups.items())
                       + f"\n| Totale | {fmt(sum(groups.values()))} |")
        else:
            out.append(f"Somma di {headers[col]}: {fmt(round(sum(values), 2))}")
    table = ""
    if changed:
        table = ("| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n"
                 + "\n".join("| " + " | ".join(r) + " |" for r in rows))
    return "\n\n".join(x for x in [table, *out] if x) + "\n"
