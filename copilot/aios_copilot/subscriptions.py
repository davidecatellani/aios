"""Riconoscimento degli abbonamenti attivi, in locale.

Fonti, dalla più forte alla più debole:
1. quello che dice l'utente («ho Netflix», «ho disdetto Disney+»): vince sempre;
2. le email: ricevute, rinnovi, benvenuto (attivo) e disdette (non attivo);
3. le app installate: solo un indizio («probabile»), da confermare.

I consigli usano gli abbonamenti attivi come filtro predefinito.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from .privacy import private_dir


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    kind: str  # video | musica | giochi | software | cloud
    domains: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()  # ID Flatpak o nomi .desktop
    free: bool = False  # gratuito (es. RaiPlay): sempre disponibile
    aliases: tuple[str, ...] = ()
    tmdb_names: tuple[str, ...] = ()  # come TMDB/JustWatch chiama il servizio


SERVICES: tuple[Service, ...] = (
    Service("netflix", "Netflix", "video", ("netflix.com",), tmdb_names=("Netflix",)),
    Service("prime", "Prime Video", "video", ("amazon.it", "amazon.com", "primevideo.com"),
            aliases=("amazon prime", "prime video", "prime"), tmdb_names=("Amazon Prime Video", "Amazon Video")),
    Service("disney", "Disney+", "video", ("disneyplus.com", "disney.com"), aliases=("disney plus", "disney+", "disney"),
            tmdb_names=("Disney Plus", "Disney+")),
    Service("appletv", "Apple TV+", "video", ("apple.com",), aliases=("apple tv", "apple tv+"), tmdb_names=("Apple TV Plus", "Apple TV+")),
    Service("now", "NOW", "video", ("nowtv.it", "now.tv"), aliases=("now tv",), tmdb_names=("NOW", "Now TV")),
    Service("paramount", "Paramount+", "video", ("paramountplus.com",), aliases=("paramount plus", "paramount"),
            tmdb_names=("Paramount Plus", "Paramount+")),
    Service("dazn", "DAZN", "video", ("dazn.com",)),
    Service("raiplay", "RaiPlay", "video", ("rai.it",), free=True, tmdb_names=("Rai Play", "RaiPlay")),
    Service("mediaset", "Mediaset Infinity", "video", ("mediaset.it", "mediasetinfinity.it"), free=True,
            aliases=("infinity", "mediaset"), tmdb_names=("Mediaset Infinity",)),
    Service("youtube", "YouTube Premium", "video", ("youtube.com",), aliases=("youtube",), tmdb_names=("YouTube Premium",)),
    Service("spotify", "Spotify", "musica", ("spotify.com",), apps=("com.spotify.Client", "spotify")),
    Service("applemusic", "Apple Music", "musica", ("apple.com",), aliases=("apple music",)),
    Service("amazonmusic", "Amazon Music", "musica", ("amazon.it", "amazon.com"), aliases=("amazon music",)),
    Service("deezer", "Deezer", "musica", ("deezer.com",)),
    Service("tidal", "Tidal", "musica", ("tidal.com",)),
    Service("gamepass", "Xbox Game Pass", "giochi", ("xbox.com", "microsoft.com"), aliases=("game pass", "xbox")),
    Service("psplus", "PlayStation Plus", "giochi", ("playstation.com", "sony.com"), aliases=("ps plus", "playstation")),
    Service("nintendo", "Nintendo Switch Online", "giochi", ("nintendo.com", "nintendo.it"), aliases=("nintendo",)),
    Service("steam", "Steam", "giochi", ("steampowered.com",), apps=("com.valvesoftware.Steam", "steam"), free=True),
    Service("m365", "Microsoft 365", "software", ("microsoft.com",), aliases=("office", "office 365", "microsoft 365")),
    Service("adobe", "Adobe Creative Cloud", "software", ("adobe.com",), aliases=("adobe", "creative cloud")),
    Service("googleone", "Google One", "cloud", ("google.com",), aliases=("google one",)),
    Service("icloud", "iCloud+", "cloud", ("apple.com",), aliases=("icloud",)),
    Service("dropbox", "Dropbox", "cloud", ("dropbox.com",), apps=("com.dropbox.Client",)),
)

# Il nome del servizio deve comparire nella mail (es. Apple vende più servizi dallo stesso dominio).
ACTIVE = re.compile(r"\b(ricevuta|fattura|rinnov\w*|addebit\w*|pagamento|abbonamento (?:attivo|confermato)|benvenut\w* (?:in|su)|"
                    r"grazie per (?:esserti|l'abbonamento)|receipt|invoice|renew\w*|payment|welcome to|subscription confirmed)\b", re.I)
CANCELLED = re.compile(r"\b(annullat\w*|disdett\w*|cancellat\w*|terminat\w*|scadut\w*|disattivat\w*|non è stato possibile|"
                       r"cancel{1,2}ed|has ended|expired|payment failed)\b", re.I)
AMOUNT = re.compile(r"(?:€\s?(\d{1,3}(?:\.\d{3})+,\d{2}|\d{1,4}[.,]\d{2})|(\d{1,3}(?:\.\d{3})+,\d{2}|\d{1,4}[.,]\d{2})\s?(?:€|eur\b|euro\b))", re.I)


def find_service(text: str) -> Service | None:
    low = text.lower()
    for s in SERVICES:
        names = (s.name.lower(), s.key, *s.aliases)
        if any(re.search(rf"(?<![\w+]){re.escape(n)}(?![\w+])", low) for n in names):
            return s
    return None


def service_for_sender(sender: str) -> Service | None:
    domain = sender.rsplit("@", 1)[-1].lower()
    for s in SERVICES:
        if any(domain == d or domain.endswith("." + d) for d in s.domains):
            return s
    return None


@dataclass
class Subscription:
    key: str
    status: str  # "attivo" | "probabile" | "non attivo"
    source: str  # "dichiarato" | "email" | "app"
    since: str = ""
    amount: float | None = None
    last_evidence: str = ""
    detail: str = ""


def store_path() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "subscriptions.json"


class Subscriptions:
    def __init__(self, path: Path | None = None):
        self.path = path or store_path()
        try:
            self.items = {k: Subscription(**v) for k, v in json.loads(self.path.read_text()).items()}
        except (OSError, ValueError, TypeError):
            self.items = {}

    def save(self) -> None:
        self.path.write_text(json.dumps({k: asdict(v) for k, v in self.items.items()}, ensure_ascii=False, indent=1))
        os.chmod(self.path, 0o600)

    def declare(self, service: Service, active: bool) -> Subscription:
        sub = Subscription(service.key, "attivo" if active else "non attivo", "dichiarato",
                           last_evidence=datetime.now().isoformat(timespec="minutes"))
        old = self.items.get(service.key)
        if old and old.amount and active:
            sub.amount = old.amount
        self.items[service.key] = sub
        self.save()
        return sub

    def evidence(self, service: Service, when: datetime, active: bool, amount: float | None, detail: str) -> None:
        """Una mail che dice qualcosa sull'abbonamento: conta la più recente; mai sopra le dichiarazioni."""
        old = self.items.get(service.key)
        if old and old.source == "dichiarato":
            if old.status == "attivo" and amount and active:
                old.amount = amount
            return
        if old and old.source == "email" and old.last_evidence >= when.isoformat():
            return
        self.items[service.key] = Subscription(
            service.key, "attivo" if active else "non attivo", "email",
            since=(old.since if old and old.status == "attivo" and active else when.date().isoformat()),
            amount=amount if amount is not None else (old.amount if old else None),
            last_evidence=when.isoformat(timespec="minutes"), detail=detail[:120])

    def app_hint(self, service: Service) -> None:
        if service.key not in self.items:
            self.items[service.key] = Subscription(service.key, "probabile", "app", detail="app installata")

    def active_keys(self, include_probable: bool = True) -> set[str]:
        ok = {"attivo", "probabile"} if include_probable else {"attivo"}
        return {k for k, s in self.items.items() if s.status in ok}

    def available_services(self) -> set[str]:
        """Servizi da cui si può guardare/ascoltare senza pagare altro: abbonamenti attivi + gratuiti."""
        return self.active_keys() | {s.key for s in SERVICES if s.free}

    def summary(self) -> str:
        names = {s.key: s for s in SERVICES}
        active = [s for s in self.items.values() if s.status == "attivo"]
        probable = [s for s in self.items.values() if s.status == "probabile"]
        ended = [s for s in self.items.values() if s.status == "non attivo"]
        if not (active or probable or ended):
            return ("Non ho ancora trovato abbonamenti. Puoi dirmelo tu («ho Netflix e Spotify»), oppure collegare la posta: "
                    "li riconosco da ricevute e rinnovi.")
        lines = []
        if active:
            lines.append("Abbonamenti attivi:")
            for s in sorted(active, key=lambda s: names[s.key].name):
                price = f" — {s.amount:.2f} €".replace(".", ",") if s.amount else ""
                origin = {"email": "dalle tue email", "dichiarato": "me l'hai detto tu"}.get(s.source, s.source)
                lines.append(f"  ✓ {names[s.key].name}{price} ({origin})")
            total = sum(s.amount or 0 for s in active)
            if total:
                lines.append(f"Spesa riconosciuta: circa {total:.2f} € a ogni rinnovo.".replace(".", ",", 1))
        if probable:
            lines.append("Forse (da confermare): " + ", ".join(names[s.key].name for s in probable)
                         + ". Dimmi «ho …» o «non ho …».")
        if ended:
            lines.append("Non più attivi: " + ", ".join(names[s.key].name for s in ended) + ".")
        return "\n".join(lines)


def _amount(text: str) -> float | None:
    m = AMOUNT.search(text)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    if "," in raw:  # formato italiano: 1.299,00
        raw = raw.replace(".", "").replace(",", ".")
    value = float(raw)
    return value if 0.5 <= value <= 500 else None


def scan_mail(subs: Subscriptions, messages: Iterable[Any], now: datetime | None = None) -> int:
    """Esamina le mail (oggetti con sender, subject, body, date) e aggiorna gli abbonamenti."""
    now = now or datetime.now()
    found = 0
    for m in messages:
        service = service_for_sender(m.sender)
        if service is None or m.date < now - timedelta(days=400):
            continue
        text = f"{m.subject}\n{m.body[:4000]}"
        # Dallo stesso dominio arrivano più servizi (Apple, Amazon, Microsoft): serve il nome giusto.
        named = find_service(text)
        if named is not None and named.key != service.key and set(named.domains) & set(service.domains):
            service = named
        if CANCELLED.search(text):
            subs.evidence(service, m.date, False, None, m.subject)
        elif ACTIVE.search(text):
            subs.evidence(service, m.date, True, _amount(text), m.subject)
        else:
            continue
        found += 1
    subs.save()
    return found


def scan_apps(subs: Subscriptions, installed: Callable[[str], bool]) -> None:
    for service in SERVICES:
        if not service.free and any(installed(app) for app in service.apps):
            subs.app_hint(service)
    subs.save()


def renewal_suggestions(subs: Subscriptions, agenda: Any, now: datetime | None = None) -> int:
    """Propone in agenda il prossimo rinnovo (stima mensile dall'ultima ricevuta)."""
    now = now or datetime.now()
    names = {s.key: s.name for s in SERVICES}
    added = 0
    for sub in subs.items.values():
        if sub.status != "attivo" or sub.source != "email" or not sub.amount or not sub.last_evidence:
            continue
        nxt = datetime.fromisoformat(sub.last_evidence).replace(hour=9, minute=0)
        while nxt <= now:
            nxt = (nxt.replace(day=1) + timedelta(days=32)).replace(day=min(nxt.day, 28))
        price = f"{sub.amount:.2f}".replace(".", ",")
        if agenda.suggest(f"Rinnovo {names[sub.key]} ({price} €)", nxt, f"abbonamento:{sub.key}") is not None:
            added += 1
    return added
