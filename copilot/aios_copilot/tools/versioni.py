"""Le versioni dei documenti a voce: «rimetti il contratto com'era ieri», «le versioni della tesi»,
«cosa è cambiato nel preventivo da stamattina?» (versioni.py)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .. import versioni
from ..fastpath import Intent, normalize
from .base import Tool, params


def make_tools(store: Callable[[], Any] = versioni.shared) -> list[Tool]:
    def pick(documento: str) -> tuple[Any, Path | None, str]:
        s = store()
        path = s.find(documento)
        if path is None:
            return s, None, (f"Non trovo versioni di «{documento}». Tengo le versioni dei documenti da quando "
                             "SoIA li vede cambiare (testi, Word, LibreOffice, fogli, presentazioni, PDF).")
        return s, path, ""

    def target(s: Any, path: Path, quando: str) -> tuple[dict[str, Any] | None, str]:
        q = (quando or "").lower()
        if not q or any(w in q for w in ("precedente", "prima", "ultima", "scorsa versione")):
            v = s.previous(path)
            return v, "" if v else f"Di «{path.name}» c'è solo la versione di adesso."
        t = versioni.moment(q)
        if t is None:
            return None, "Da quando? Per esempio «ieri», «stamattina», «due ore fa», «lunedì», «il 3 ottobre»."
        v = s.at(path, t)
        return v, "" if v else f"Non ho versioni di «{path.name}» così vecchie: la prima è {versioni.describe_time(s.versions(path)[-1]['t'])}."

    def document_versions(documento: str) -> str:
        s, path, problem = pick(documento)
        if path is None:
            return problem
        items = s.versions(path)
        lines = [f"• {versioni.describe_time(v['t'])}" for v in items[:12]]
        return f"Le versioni di «{path.name}» ({len(items)}):\n" + "\n".join(lines) + "\nPosso rimetterne una o dirti cosa è cambiato."

    def restore_document(documento: str, quando: str = "") -> str:
        s, path, problem = pick(documento)
        if path is None:
            return problem
        v, problem = target(s, path, quando)
        return s.restore(path, v["id"]) if v else problem

    def document_changes(documento: str, da: str = "") -> str:
        s, path, problem = pick(documento)
        if path is None:
            return problem
        v, problem = target(s, path, da)
        if not v:
            return problem
        return f"Cosa è cambiato in «{path.name}» rispetto a {versioni.describe_time(v['t'])}:\n" + s.changes(path, v["id"])

    return [
        Tool("document_versions", "Elenca le versioni salvate di un documento dell'utente (testi, Word, fogli, PDF…).",
             params(["documento"], documento="Nome o parole del documento (es. «contratto affitto»)"), document_versions,
             reads_private=True),
        Tool("restore_document", "Rimette un documento com'era in un momento passato o alla versione precedente "
             "(la versione di adesso resta salvata).",
             params(["documento"], documento="Nome o parole del documento",
                    quando="«ieri», «stamattina», «due ore fa», «lunedì», «il 3 ottobre»; vuoto = versione precedente"),
             restore_document, requires_confirmation=True),
        Tool("document_changes", "Dice cosa è cambiato in un documento rispetto a una versione passata.",
             params(["documento"], documento="Nome o parole del documento", da="Da quando (vuoto = versione precedente)"),
             document_changes, reads_private=True),
    ]


RE_RESTORE = re.compile(r"^(?:rimetti|riporta|ripristina)\s+(?P<f>.+?)\s+(?:com'era|come\s+era|alla\s+versione\s+di)\s+(?P<q>.+)$"
                        r"|^(?:rimetti|ripristina)\s+la\s+versione\s+(?:precedente|di\s+prima)\s+(?:del|della|dello|dei|delle|di)\s+(?P<f2>.+)$")
RE_LIST = re.compile(r"^(?:mostrami\s+|fammi\s+vedere\s+)?(?:le\s+)?versioni\s+(?:precedenti\s+)?(?:del|della|dello|dei|delle|di)\s+(?P<f>.+)$")
RE_DIFF = re.compile(r"^(?:cosa|che\s+cosa)\s+(?:è|e'|e)\s+cambiato\s+(?:nel|nella|nello|nei|nelle|in)\s+(?P<f>.+?)"
                     r"(?:\s+(?:da|rispetto\s+a)\s+(?P<q>.+))?$")


class VersionsRouter:
    def match(self, text: str) -> Any:
        low = normalize(text).strip(" .!?")
        m = RE_RESTORE.match(low)
        if m and (m.group("f") or "").strip() not in ("tutto", "tutte le cose"):
            if m.group("f2"):
                return Intent("restore_document", {"documento": m.group("f2").strip(), "quando": "precedente"})
            return Intent("restore_document", {"documento": m.group("f").strip(), "quando": m.group("q").strip()})
        m = RE_LIST.match(low)
        if m:
            return Intent("document_versions", {"documento": m.group("f").strip()})
        m = RE_DIFF.match(low)
        if m:
            return Intent("document_changes", {"documento": m.group("f").strip(), "da": (m.group("q") or "").strip()})
        return None
