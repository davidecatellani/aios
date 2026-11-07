"""Metadati dei file multimediali, letti senza dipendenze esterne.

- foto JPEG: data di scatto e fotocamera (EXIF);
- musica: titolo, artista, album (ID3v2 per MP3, commenti Vorbis per FLAC),
  con il nome del file come ripiego («Artista - Titolo.mp3»).
Si legge solo l'inizio del file: veloce anche su collezioni grandi.
"""

from __future__ import annotations

import re
import struct
from datetime import datetime
from pathlib import Path

HEAD_BYTES = 256 * 1024


def _read_head(path: Path, n: int = HEAD_BYTES) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


# --- EXIF -------------------------------------------------------------------------------------


def _ifd(tiff: bytes, offset: int, endian: str) -> dict[int, tuple[int, int, bytes]]:
    """Voci di una directory TIFF: {tag: (tipo, quantità, valore o puntatore)}."""
    entries = {}
    if offset + 2 > len(tiff):
        return entries
    (count,) = struct.unpack_from(endian + "H", tiff, offset)
    for i in range(min(count, 500)):
        pos = offset + 2 + 12 * i
        if pos + 12 > len(tiff):
            break
        tag, typ, n = struct.unpack_from(endian + "HHI", tiff, pos)
        entries[tag] = (typ, n, tiff[pos + 8: pos + 12])
    return entries


def _ascii(tiff: bytes, entry: tuple[int, int, bytes], endian: str) -> str:
    typ, n, raw = entry
    if typ != 2:
        return ""
    data = raw[:n] if n <= 4 else tiff[struct.unpack(endian + "I", raw)[0]:][:n]
    return data.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()


def exif(path: Path) -> dict[str, str]:
    """{'date': 'YYYY:MM:DD HH:MM:SS', 'camera': '...'} per le foto JPEG che li hanno."""
    data = _read_head(path)
    if data[:2] != b"\xff\xd8":
        return {}
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return {}
        marker = data[pos + 1]
        (length,) = struct.unpack_from(">H", data, pos + 2)
        if marker == 0xE1 and data[pos + 4: pos + 10] == b"Exif\0\0":
            tiff = data[pos + 10: pos + 2 + length]
            endian = "<" if tiff[:2] == b"II" else ">"
            if len(tiff) < 8:
                return {}
            ifd0 = _ifd(tiff, struct.unpack_from(endian + "I", tiff, 4)[0], endian)
            out = {}
            if 0x0110 in ifd0:
                out["camera"] = _ascii(tiff, ifd0[0x0110], endian)
            if 0x0132 in ifd0:
                out["date"] = _ascii(tiff, ifd0[0x0132], endian)
            if 0x8769 in ifd0:
                sub = _ifd(tiff, struct.unpack(endian + "I", ifd0[0x8769][2])[0], endian)
                if 0x9003 in sub:
                    out["date"] = _ascii(tiff, sub[0x9003], endian)
            return out
        if marker in (0xDA, 0xD9):  # inizio dei dati dell'immagine: niente EXIF
            return {}
        pos += 2 + length
    return {}


def photo_date(path: Path) -> datetime | None:
    raw = exif(path).get("date", "")
    try:
        return datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        pass
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_ T]?(\d{2})?(\d{2})?", path.name)  # IMG_20260814_103012.jpg
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 12), int(m.group(5) or 0))
        except ValueError:
            return None
    return None


# --- musica ------------------------------------------------------------------------------------


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3_text(payload: bytes) -> str:
    if not payload:
        return ""
    enc, body = payload[0], payload[1:]
    try:
        if enc == 0:
            text = body.decode("latin-1")
        elif enc == 1:
            text = body.decode("utf-16")
        elif enc == 2:
            text = body.decode("utf-16-be")
        else:
            text = body.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return text.split("\0", 1)[0].strip()


def _id3(data: bytes) -> dict[str, str]:
    if data[:3] != b"ID3" or len(data) < 10:
        return {}
    version, size = data[3], _syncsafe(data[6:10])
    pos, end, out = 10, min(10 + size, len(data)), {}
    names = {b"TIT2": "title", b"TPE1": "artist", b"TALB": "album", b"TCON": "genre"}
    while pos + 10 <= end:
        frame = data[pos: pos + 4]
        if not frame.strip(b"\0"):
            break
        length = _syncsafe(data[pos + 4: pos + 8]) if version >= 4 else struct.unpack(">I", data[pos + 4: pos + 8])[0]
        if frame in names:
            out[names[frame]] = _id3_text(data[pos + 10: pos + 10 + length])
        pos += 10 + length
    return {k: v for k, v in out.items() if v}


def _flac(data: bytes) -> dict[str, str]:
    if data[:4] != b"fLaC":
        return {}
    pos, out = 4, {}
    while pos + 4 <= len(data):
        header = data[pos]
        last, kind = header & 0x80, header & 0x7F
        length = int.from_bytes(data[pos + 1: pos + 4], "big")
        block = data[pos + 4: pos + 4 + length]
        if kind == 4 and len(block) >= 8:  # VORBIS_COMMENT
            vlen = struct.unpack_from("<I", block, 0)[0]
            p = 4 + vlen
            (n,) = struct.unpack_from("<I", block, p)
            p += 4
            for _ in range(min(n, 200)):
                (clen,) = struct.unpack_from("<I", block, p)
                key, _, value = block[p + 4: p + 4 + clen].decode("utf-8", errors="replace").partition("=")
                if key.upper() in ("TITLE", "ARTIST", "ALBUM", "GENRE"):
                    out[key.lower()] = value.strip()
                p += 4 + clen
            break
        if last:
            break
        pos += 4 + length
    return out


def music_tags(path: Path) -> dict[str, str]:
    data = _read_head(path, 512 * 1024)
    tags = _id3(data) or _flac(data)
    if "artist" not in tags or "title" not in tags:
        m = re.match(r"^(?:\d+[\s._-]+)?(?P<artist>.+?)\s+-\s+(?P<title>.+)$", path.stem)  # «Artista - Titolo»
        if m:
            tags.setdefault("artist", m.group("artist").strip())
            tags.setdefault("title", m.group("title").strip())
    return tags
