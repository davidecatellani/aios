"""Loghi di SoIA (sistema e copilota), serviti alle pagine locali e alla pagina del telefono."""

from __future__ import annotations

from pathlib import Path

BRAND_DIR = Path(__file__).resolve().parent / "brand"
TYPES = {".png": "image/png", ".svg": "image/svg+xml"}


def brand_file(name: str) -> tuple[bytes, str] | None:
    """Solo i file della cartella dei loghi, per nome esatto (nessun percorso)."""
    files = {p.name: p for p in BRAND_DIR.glob("*") if p.suffix in TYPES} if BRAND_DIR.is_dir() else {}
    path = files.get(name)
    return (path.read_bytes(), TYPES[path.suffix]) if path else None
