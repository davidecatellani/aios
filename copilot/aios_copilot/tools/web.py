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

# Un browser comune: con un nome da programma i motori di ricerca rispondono con una verifica anti-robot
# (pagina senza risultati) invece che con i risultati.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
MAX_PAGE_CHARS = 6000

Fetch = Callable[[str], str]


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
                                               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
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


def parse_duckduckgo_lite(html: str, limit: int = 6) -> list[dict[str, str]]:
    """La versione «lite» di DuckDuckGo (tabella): link «result-link», estratti «result-snippet»."""
    links = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*class=[\"']result-link[\"'][^>]*>(.*?)</a>", html, re.S)
    links += re.findall(r"<a[^>]+class=[\"']result-link[\"'][^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.S)
    snippets = re.findall(r"class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", html, re.S)
    out = []
    for i, (href, title) in enumerate(links[:limit]):
        query = urllib.parse.urlparse(unescape(href)).query
        url = urllib.parse.parse_qs(query).get("uddg", [unescape(href)])[0]
        out.append({"title": _strip_tags(title), "url": url, "snippet": _strip_tags(snippets[i]) if i < len(snippets) else ""})
    return out


def parse_bing(html: str, limit: int = 6) -> list[dict[str, str]]:
    out = []
    for block in re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.S)[:limit]:
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append({"title": _strip_tags(a.group(2)), "url": unescape(a.group(1)), "snippet": _strip_tags(p.group(1)) if p else ""})
    return out


def parse_mojeek(html: str, limit: int = 6) -> list[dict[str, str]]:
    out = []
    for block in re.findall(r"<li[^>]*>(.*?)</li>", html, re.S):
        a = re.search(r'<a[^>]+class="title"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S) or \
            re.search(r'<a[^>]+href="([^"]+)"[^>]+class="title"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        p = re.search(r'<p class="s"[^>]*>(.*?)</p>', block, re.S)
        out.append({"title": _strip_tags(a.group(2)), "url": unescape(a.group(1)), "snippet": _strip_tags(p.group(1)) if p else ""})
        if len(out) >= limit:
            break
    return out


def parse_news_rss(xml: str, limit: int = 6) -> list[dict[str, str]]:
    """Google News (RSS): titolo, link, fonte e data di ogni notizia."""
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:limit]:
        def tag(name: str) -> str:
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
            return _strip_tags(re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", m.group(1).strip(), flags=re.S)) if m else ""

        out.append({"title": tag("title"), "url": tag("link"), "snippet": " · ".join(x for x in (tag("source"), tag("pubDate")) if x)})
    return out


NEWS = re.compile(r"\b(?:notizi\w*|news|ultim[ae] or[ae]|cronaca|giornale|tg|telegiornale|cosa (?:è|e') successo)\b", re.I)
q = urllib.parse.quote_plus
ENGINES: list[tuple[str, Callable[[str], str], Callable[[str, int], list[dict[str, str]]]]] = [
    ("duckduckgo", lambda t: f"https://html.duckduckgo.com/html/?q={q(t)}&kl=it-it", parse_duckduckgo),
    ("duckduckgo lite", lambda t: f"https://lite.duckduckgo.com/lite/?q={q(t)}&kl=it-it", parse_duckduckgo_lite),
    ("bing", lambda t: f"https://www.bing.com/search?q={q(t)}&setlang=it&cc=IT", parse_bing),
    ("mojeek", lambda t: f"https://www.mojeek.com/search?q={q(t)}&lb=it", parse_mojeek),
]
NEWS_ENGINE = ("google news", lambda t: f"https://news.google.com/rss/search?q={q(t)}&hl=it&gl=IT&ceid=IT:it", parse_news_rss)


def search(query: str, fetch: Fetch = http_get, limit: int = 6) -> list[dict[str, str]]:
    """Cerca con SearXNG se configurato (AIOS_SEARXNG_URL); altrimenti un motore dopo l'altro finché uno dà
    risultati (DuckDuckGo, la sua versione lite, Bing, Mojeek). Per le notizie prima Google News."""
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
    engines = [NEWS_ENGINE, *ENGINES] if NEWS.search(query) else ENGINES
    errors: list[Exception] = []
    for _name, url, parse in engines:
        try:
            found = parse(fetch(url(query)), limit)
        except Exception as exc:  # motore irraggiungibile o pagina strana: si prova il prossimo
            errors.append(exc)
            continue
        if found:
            return found
    if len(errors) == len(engines):
        raise errors[-1]
    return []


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
