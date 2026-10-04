"""Ricerca su internet e lettura di pagine web."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from typing import Callable

from .base import Tool, attach, params

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AIOS-Copilot/0.1"
MAX_PAGE_CHARS = 6000

Fetch = Callable[[str], str]


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _strip_tags(fragment: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_duckduckgo(html: str, limit: int = 6) -> list[dict[str, str]]:
    """Estrae i risultati dalla versione HTML di DuckDuckGo."""
    links = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S
    )
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    results = []
    for i, (href, title) in enumerate(links[:limit]):
        # I link passano dal redirect di DDG: l'URL reale è nel parametro "uddg".
        query = urllib.parse.urlparse(unescape(href)).query
        url = urllib.parse.parse_qs(query).get("uddg", [unescape(href)])[0]
        results.append(
            {
                "title": _strip_tags(title),
                "url": url,
                "snippet": _strip_tags(snippets[i]) if i < len(snippets) else "",
            }
        )
    return results


def search(query: str, fetch: Fetch = http_get, limit: int = 6) -> list[dict[str, str]]:
    """Cerca con SearXNG se configurato (AIOS_SEARXNG_URL), altrimenti con DuckDuckGo."""
    searx = os.environ.get("AIOS_SEARXNG_URL")
    if searx:
        url = f"{searx.rstrip('/')}/search?" + urllib.parse.urlencode(
            {"q": query, "format": "json"}
        )
        data = json.loads(fetch(url))
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in data.get("results", [])[:limit]
        ]
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    return parse_duckduckgo(fetch(url), limit)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str, max_chars: int = MAX_PAGE_CHARS) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    return text[:max_chars] + (" […]" if len(text) > max_chars else "")


def make_tools(fetch: Fetch = http_get) -> list[Tool]:
    def search_web(query: str) -> str:
        try:
            results = search(query, fetch)
        except Exception as exc:  # rete assente, servizio non raggiungibile...
            return f"Errore nella ricerca: {exc}"
        if not results:
            return "Nessun risultato."
        attach("link", [{"titolo": r["title"], "url": r["url"], "sottotitolo": re.sub(r"^https?://(?:www\.)?([^/]+).*", r"\1", r["url"]),
                         "estratto": r["snippet"][:140]} for r in results[:6]], "Dal web")
        return "\n\n".join(
            f"[{i}] {r['title']}\n{r['url']}\n{r['snippet']}"
            for i, r in enumerate(results, 1)
        )

    def read_webpage(url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return "URL non valido: deve iniziare con http:// o https://"
        try:
            return html_to_text(fetch(url)) or "La pagina non contiene testo leggibile."
        except Exception as exc:
            return f"Impossibile leggere la pagina: {exc}"

    return [
        Tool(
            "search_web",
            "Cerca informazioni aggiornate su internet. Restituisce titoli, URL e "
            "anteprime dei risultati.",
            params(query="Testo da cercare"),
            search_web,
            sends_out=True,
        ),
        Tool(
            "read_webpage",
            "Scarica una pagina web e ne restituisce il testo, per leggere i dettagli "
            "di un risultato di ricerca.",
            params(url="Indirizzo completo della pagina"),
            read_webpage,
            sends_out=True,
        ),
    ]
