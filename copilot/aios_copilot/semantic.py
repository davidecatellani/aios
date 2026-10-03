"""Livello 1: riconosce le richieste riformulate liberamente.

Ogni azione del sistema ha un piccolo catalogo di frasi d'esempio. La richiesta
dell'utente viene trasformata in un vettore e confrontata con gli esempi: se
somiglia abbastanza a un'azione, e chiaramente più che alla seconda candidata,
l'azione viene eseguita senza interpellare il modello linguistico.

Due codificatori:
- LexicalEncoder (predefinito): radici delle parole + trigrammi di caratteri con
  pesi TF-IDF. Nessuna dipendenza, nessun download, meno di un millisecondo su CPU;
  tollera coniugazioni, refusi e ordine delle parole diverso.
- OllamaEncoder (facoltativo, AIOS_EMBED_MODEL): un modello di embedding neurale
  servito da Ollama, per cogliere anche sinonimi che non condividono parole. Le
  soglie vanno calibrate sul modello scelto con `python -m aios_copilot.semantic`.

Come il livello 0, in caso di dubbio si astiene e lascia decidere all'LLM.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
import urllib.request
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .fastpath import Intent, normalize
from .xdg import resolve_folder

STOPWORDS = frozenset(
    """il lo la i gli le l un uno una di del dello della dei degli delle d a al allo alla
    ai agli alle da dal dalla dai in nel nella nei con su sul sulla per tra fra e ed o
    mi ti ci vi si me te ce ne lo po un pochino poco favore grazie puoi potresti vorrei
    voglio dai ora adesso subito qui questo questa quello quella che sto sono ho
    piu molto troppo tutto tutti
    the a an to of my me please can you could would i it this that some now on""".split()
)
NEGATIONS = frozenset(
    "non not dont don never mai senza no ne pas jamais nicht kein keine nie nao nunca niet nee".split()
    + ["не", "нет", "ни"]
)
# Lingue senza spazi tra le parole: la negazione si cerca come sottostringa.
NEGATION_MARKS = ("不", "别", "没", "ない", "な", "않", "말")
MAX_WORDS = 14
# Quota minima di parole della richiesta che il catalogo conosce: "spegni il
# computer di Marco" contiene un elemento ignoto e non va eseguita alla cieca.
MIN_KNOWN = 0.7

# Concetti: parole diverse con lo stesso significato (radici, in italiano e inglese).
# Pesano più delle parole stesse, così "riduci il volume" e "abbassa il volume"
# coincidono, mentre "attiva" e "disattiva" restano opposti.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "UP": ("alz", "aument", "alto", "fort", "louder", "up", "increas"),
    "DOWN": ("abbass", "diminu", "ridu", "bass", "pian", "giu", "quiet", "down", "decreas", "dimmer", "meno"),
    "ON": ("accend", "attiv", "riattiv", "enabl", "on", "rimett"),
    "OFF": ("spegn", "disattiv", "togl", "scolleg", "disabl", "off", "shut", "arrest", "azzer"),
    "MUTE": ("mut", "silenz", "zitt"),
    "AUDIO": ("volum", "audio", "suon", "sonor", "sound"),
    "BRIGHT": ("lumin", "bright", "abbagl", "dimmer"),
    "DARK": ("scur", "dark", "nott"),
    "LIGHT": ("chiar", "light"),
    "THEME": ("tema", "modalit", "theme", "mode", "color"),
    "WIFI": ("wifi", "wi", "fi", "wireless"),
    "BLUETOOTH": ("bluetooth",),
    "MUSIC": ("music", "canzon", "bran", "song", "track"),
    "PAUSE": ("paus", "ferm", "stopp", "stop", "interromp"),
    "NEXT": ("success", "prossim", "salt", "next", "skip", "avanti"),
    "PREV": ("preceden", "prima", "previous", "indietro"),
    "SCREENSHOT": ("screenshot", "schermat", "cattur", "fotograf"),
    "LOCK": ("blocc", "lock"),
    "SCREEN": ("scherm", "screen"),
    "DEVICE": ("computer", "pc", "sistem", "dispositiv"),
    "SUSPEND": ("standby", "sospen", "suspend", "sleep"),
    "REBOOT": ("riavvi", "restart", "reboot"),
    "INFO": ("ram", "memori", "spazio", "disco", "memory", "stato", "info"),
    "DOWNLOAD": ("download", "scaric"),
    "PICTURES": ("foto", "immagin", "pictur", "photo"),
    "DOCUMENTS": ("document",),
    "VIDEOS": ("video", "filmat"),
}


# "Troppo basso" chiede di alzare: dopo "troppo" un aggettivo indica il contrario
# di ciò che l'utente vuole.
OPPOSITES = {"UP": "DOWN", "DOWN": "UP", "DARK": "LIGHT", "LIGHT": "DARK"}
TOO = frozenset({"troppo", "too"})
# I concetti di direzione distinguono azioni opposte sullo stesso oggetto
# ("alza"/"abbassa" la luminosità): pesano più dei concetti di oggetto.
DIRECTIONS = frozenset({"UP", "DOWN", "ON", "OFF", "DARK", "LIGHT", "NEXT", "PREV"})


def concepts(word: str) -> list[str]:
    found = []
    for name, stems in CONCEPTS.items():
        # Le radici corte (es. "on", "wi") devono coincidere con l'intera parola.
        if any(word == stem or (len(stem) >= 3 and word.startswith(stem)) for stem in stems):
            found.append(name)
    return found


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", normalize(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    # \w copre ogni alfabeto (cirillico, greco, arabo, CJK...), non solo il latino.
    return re.findall(r"\w+", text)


def negated(text: str) -> bool:
    return bool(NEGATIONS & set(tokens(text))) or any(mark in text for mark in NEGATION_MARKS)


def content_words(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS]


@dataclass(frozen=True)
class IntentSpec:
    name: str
    tool: str
    args: dict[str, Any] | Callable[[], dict[str, Any]]
    examples: tuple[str, ...]

    def build(self) -> Intent:
        args = self.args() if callable(self.args) else dict(self.args)
        return Intent(self.tool, args)


def _folder(key: str) -> Callable[[], dict[str, Any]]:
    return lambda: {"target": str(resolve_folder(key))}


CATALOG: tuple[IntentSpec, ...] = (
    IntentSpec("volume_up", "set_volume", {"action": "up"}, (
        "alza il volume", "aumenta il volume", "più forte", "non sento bene", "alza l'audio",
        "metti l'audio più alto", "volume su", "si sente troppo piano", "turn the volume up", "louder",
    )),
    IntentSpec("volume_down", "set_volume", {"action": "down"}, (
        "abbassa il volume", "diminuisci il volume", "più piano", "è troppo forte", "abbassa l'audio",
        "volume giù", "si sente troppo forte", "turn the volume down", "quieter",
    )),
    IntentSpec("mute", "set_volume", {"action": "mute"}, (
        "togli l'audio", "silenzio", "muto", "disattiva l'audio", "metti in muto",
        "spegni l'audio", "zittisci il computer", "mute", "mute the sound",
    )),
    IntentSpec("unmute", "set_volume", {"action": "unmute"}, (
        "riattiva l'audio", "togli il muto", "rimetti l'audio", "accendi l'audio", "unmute",
    )),
    IntentSpec("brightness_up", "set_brightness", {"action": "up"}, (
        "aumenta la luminosità", "alza la luminosità", "schermo più luminoso",
        "lo schermo è troppo scuro", "non vedo niente sullo schermo", "brighter", "increase brightness",
    )),
    IntentSpec("brightness_down", "set_brightness", {"action": "down"}, (
        "abbassa la luminosità", "diminuisci la luminosità", "schermo meno luminoso",
        "lo schermo è troppo luminoso", "lo schermo mi abbaglia", "rendi lo schermo più scuro",
        "dimmer", "decrease brightness",
    )),
    IntentSpec("theme_dark", "set_theme", {"mode": "dark"}, (
        "attiva il tema scuro", "modalità scura", "metti il tema scuro", "passa al tema scuro",
        "colori scuri", "modalità notte", "dark mode", "switch to dark theme",
    )),
    IntentSpec("theme_light", "set_theme", {"mode": "light"}, (
        "attiva il tema chiaro", "modalità chiara", "metti il tema chiaro", "togli il tema scuro",
        "togli la modalità scura",
        "torna ai colori chiari", "light mode", "switch to light theme",
    )),
    IntentSpec("wifi_on", "set_radio", {"device": "wifi", "state": "on"}, (
        "accendi il wifi", "attiva il wifi", "collegati al wifi", "riattiva la rete wireless",
        "attiva la connessione wifi", "turn on wifi", "enable wifi",
    )),
    IntentSpec("wifi_off", "set_radio", {"device": "wifi", "state": "off"}, (
        "spegni il wifi", "disattiva il wifi", "scollegati dal wifi", "togli il wifi",
        "disattiva la rete wireless", "turn off wifi", "disable wifi",
    )),
    IntentSpec("bluetooth_on", "set_radio", {"device": "bluetooth", "state": "on"}, (
        "accendi il bluetooth", "attiva il bluetooth", "riattiva il bluetooth", "turn on bluetooth",
    )),
    IntentSpec("bluetooth_off", "set_radio", {"device": "bluetooth", "state": "off"}, (
        "spegni il bluetooth", "disattiva il bluetooth", "togli il bluetooth", "turn off bluetooth",
    )),
    IntentSpec("media_play", "media_control", {"action": "play"}, (
        "metti un po' di musica", "fammi sentire della musica", "fai partire la musica",
        "riprendi la musica", "voglio ascoltare musica", "riprendi la riproduzione",
        "play some music", "play music",
    )),
    IntentSpec("media_pause", "media_control", {"action": "pause"}, (
        "metti in pausa", "pausa", "ferma la musica", "stoppa la canzone", "interrompi la riproduzione",
        "pause the music", "stop the music",
    )),
    IntentSpec("media_next", "media_control", {"action": "next"}, (
        "canzone successiva", "brano successivo", "salta questa canzone", "prossima canzone",
        "vai avanti di un brano", "next song", "skip this song",
    )),
    IntentSpec("media_previous", "media_control", {"action": "previous"}, (
        "canzone precedente", "brano precedente", "torna alla canzone di prima",
        "rimetti la canzone di prima", "previous song",
    )),
    IntentSpec("screenshot", "take_screenshot", {}, (
        "fai uno screenshot", "cattura lo schermo", "fotografa lo schermo",
        "salva un'immagine dello schermo", "take a screenshot", "screenshot",
    )),
    IntentSpec("lock", "lock_screen", {}, (
        "blocca lo schermo", "blocca il computer", "blocca il pc", "metti il blocco schermo", "lock the screen",
    )),
    IntentSpec("suspend", "power", {"action": "suspend"}, (
        "metti in standby", "sospendi il computer", "metti in sospensione", "manda il pc in standby",
        "suspend", "sleep mode",
    )),
    IntentSpec("poweroff", "power", {"action": "poweroff"}, (
        "spegni il computer", "spegni il pc", "arresta il sistema", "spegniti", "spegni tutto",
        "shut down the computer", "power off",
    )),
    IntentSpec("reboot", "power", {"action": "reboot"}, (
        "riavvia il computer", "riavvia il pc", "riavvia il sistema", "riavviati", "restart the computer", "reboot",
    )),
    IntentSpec("system_info", "system_info", {}, (
        "come sta il computer", "quanta ram ho", "quanto spazio libero ho", "stato del sistema",
        "informazioni sul pc", "il disco è pieno", "how much memory do i have",
    )),
    IntentSpec("open_downloads", "open_location", _folder("DOWNLOAD"), (
        "mostrami i file scaricati", "dove sono i download", "apri gli scaricati",
        "fammi vedere cosa ho scaricato", "show my downloads",
    )),
    IntentSpec("open_pictures", "open_location", _folder("PICTURES"), (
        "fammi vedere le mie foto", "apri le immagini", "mostrami le foto", "dove sono le mie foto",
        "show my pictures",
    )),
    IntentSpec("open_documents", "open_location", _folder("DOCUMENTS"), (
        "apri i miei documenti", "mostrami i documenti", "dove sono i documenti", "show my documents",
    )),
    IntentSpec("open_videos", "open_location", _folder("VIDEOS"), (
        "mostrami i miei video", "apri la cartella dei video", "dove sono i miei filmati",
    )),
)


class Encoder(Protocol):
    # Soglie di decisione: dipendono dal tipo di vettori prodotti.
    threshold: float
    margin: float
    # True se i vettori si basano sulle parole del catalogo: allora le parole ignote
    # sono un segnale di incertezza. Un modello neurale invece capisce anche parole
    # (e lingue) che il catalogo non contiene.
    lexical: bool

    def fit(self, corpus: Sequence[str]) -> None: ...

    def encode(self, texts: Sequence[str]) -> list[Any]: ...

    def similarity(self, a: Any, b: Any) -> float: ...


class LexicalEncoder:
    """Vettori sparsi TF-IDF di concetti, radici (prime 5 lettere) e trigrammi di caratteri.

    I concetti portano il significato, le radici distinguono le parole, i trigrammi
    (con peso basso) tollerano refusi e parole mai viste.
    """

    threshold = 0.45
    margin = 0.08
    lexical = True

    def __init__(self) -> None:
        self.idf: dict[str, float] = {}

    @staticmethod
    def features(text: str) -> Counter[str]:
        feats: Counter[str] = Counter()
        after_too = False
        for word in tokens(text):
            if word in TOO:
                after_too = True
                continue
            if word in STOPWORDS:
                continue
            for concept in concepts(word):
                if after_too:
                    concept = OPPOSITES.get(concept, concept)
                feats["k:" + concept] += 6 if concept in DIRECTIONS else 4
            after_too = False
            feats["w:" + word[:5]] += 2
            padded = f"#{word}#"
            for i in range(len(padded) - 2):
                feats["c:" + padded[i : i + 3]] += 0.25
        return feats

    def fit(self, corpus: Sequence[str]) -> None:
        df: Counter[str] = Counter()
        for text in corpus:
            df.update(set(self.features(text)))
        n = len(corpus)
        self.idf = {f: math.log((1 + n) / (1 + c)) + 1 for f, c in df.items()}

    def encode(self, texts: Sequence[str]) -> list[dict[str, float]]:
        vectors = []
        unseen = math.log(1 + len(self.idf)) + 1 if self.idf else 1.0
        for text in texts:
            vec = {f: tf * self.idf.get(f, unseen) for f, tf in self.features(text).items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            vectors.append({f: v / norm for f, v in vec.items()})
        return vectors

    def similarity(self, a: dict[str, float], b: dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(f, 0.0) for f, v in a.items())


class OllamaEncoder:
    """Embedding neurali multilingue tramite Ollama (/api/embed).

    Gli embedding degli esempi del catalogo vengono salvati in una cache su disco:
    all'avvio si ricalcolano solo quelli nuovi, e a ogni richiesta si codifica
    soltanto la frase dell'utente.
    """

    lexical = False

    def __init__(
        self,
        model: str,
        url: str | None = None,
        threshold: float = 0.75,
        margin: float = 0.05,
        prefix: str = "",
        cache_dir: Path | None = None,
    ):
        self.model = model
        self.url = (url or os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.threshold = threshold
        self.margin = margin
        self.prefix = prefix  # alcuni modelli (famiglia E5) vogliono "query: " davanti al testo
        safe_name = re.sub(r"[^\w.-]", "_", model)
        self.cache_path = cache_dir / f"embeddings-{safe_name}.json" if cache_dir else None
        self._cache: dict[str, list[float]] | None = None

    def fit(self, corpus: Sequence[str]) -> None:
        missing = [t for t in dict.fromkeys(corpus) if t not in self._load_cache()]
        for i in range(0, len(missing), 64):
            batch = missing[i : i + 64]
            self._cache.update(zip(batch, self._embed(batch)))
        if missing:
            self._save_cache()

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        cache = self._load_cache()
        missing = [t for t in texts if t not in cache]
        fresh = dict(zip(missing, self._embed(missing))) if missing else {}
        return [cache.get(t) or fresh[t] for t in texts]

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": [self.prefix + t for t in texts], "keep_alive": -1}
        req = urllib.request.Request(
            f"{self.url}/api/embed", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            vectors = json.loads(resp.read())["embeddings"]
        out = []
        for v in vectors:
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

    def _load_cache(self) -> dict[str, list[float]]:
        if self._cache is None:
            self._cache = {}
            if self.cache_path and self.cache_path.exists():
                try:
                    self._cache = json.loads(self.cache_path.read_text())
                except (OSError, ValueError):
                    pass
        return self._cache

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            rounded = {t: [round(x, 5) for x in v] for t, v in self._cache.items()}
            self.cache_path.write_text(json.dumps(rounded))
        except OSError:
            pass  # la cache è solo un'ottimizzazione

    def similarity(self, a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))


@dataclass
class Match:
    spec: IntentSpec
    score: float
    runner_up: float
    example: str


def with_examples(catalog: Sequence[IntentSpec], extra: dict[str, list[str]]) -> tuple[IntentSpec, ...]:
    """Aggiunge al catalogo esempi in altre lingue (es. tradotti automaticamente)."""
    return tuple(
        replace(spec, examples=spec.examples + tuple(extra.get(spec.name, ()))) for spec in catalog
    )


@dataclass
class SemanticRouter:
    catalog: Sequence[IntentSpec] = CATALOG
    encoder: Encoder = field(default_factory=LexicalEncoder)

    def __post_init__(self) -> None:
        examples = [(spec, ex) for spec in self.catalog for ex in spec.examples]
        self.encoder.fit([ex for _, ex in examples])
        vectors = self.encoder.encode([ex for _, ex in examples])
        self._index = [(spec, ex, vec) for (spec, ex), vec in zip(examples, vectors)]
        self._vocabulary = {w[:5] for _, ex in examples for w in content_words(ex)}

    def known_fraction(self, text: str) -> float:
        words = content_words(text)
        if not words:
            return 0.0
        known = [w for w in words if w[:5] in self._vocabulary or concepts(w)]
        return len(known) / len(words)

    def rank(self, text: str) -> Match | None:
        """Azione più somigliante, con il punteggio della seconda azione candidata."""
        query = self.encoder.encode([text])[0]
        best: dict[str, tuple[float, IntentSpec, str]] = {}
        for spec, example, vec in self._index:
            score = self.encoder.similarity(query, vec)
            if spec.name not in best or score > best[spec.name][0]:
                best[spec.name] = (score, spec, example)
        ranked = sorted(best.values(), key=lambda item: item[0], reverse=True)
        if not ranked:
            return None
        score, spec, example = ranked[0]
        return Match(spec, score, ranked[1][0] if len(ranked) > 1 else 0.0, example)

    def match(self, text: str) -> Intent | None:
        m = self.candidate(text)
        if m is None or not self.accept(m, text, self.encoder.threshold, self.encoder.margin):
            return None
        return m.spec.build()

    def candidate(self, text: str) -> Match | None:
        """Azione più probabile, prima di applicare le soglie (None se la frase è da scartare)."""
        words = tokens(text)
        if not words or len(words) > MAX_WORDS:
            return None
        if getattr(self.encoder, "lexical", False) and self.known_fraction(text) < MIN_KNOWN:
            return None
        return self.rank(text)

    @staticmethod
    def accept(m: Match, text: str, threshold: float, margin: float) -> bool:
        if m.score < threshold or m.score - m.runner_up < margin:
            return False
        # "Non spegnere il wifi" somiglia a "spegni il wifi": davanti a una negazione
        # si agisce solo se anche l'esempio la contiene ("non sento bene").
        return not (negated(text) and not negated(m.example))


def default_router() -> SemanticRouter:
    return SemanticRouter()


def _cli() -> None:
    """Prova interattiva: mostra azione, punteggi e tempo per ogni frase."""
    router = default_router()
    print("Scrivi una richiesta (Ctrl+D per uscire).")
    while True:
        try:
            text = input("› ")
        except EOFError:
            return
        start = time.perf_counter()
        m = router.rank(text)
        intent = router.match(text)
        ms = (time.perf_counter() - start) * 1000
        if m:
            print(f"  migliore: {m.spec.name} {m.score:.2f} (seconda {m.runner_up:.2f}, esempio «{m.example}»)")
        print(f"  → {intent if intent else 'non sicuro: decide l’LLM'}   [{ms:.2f} ms]")


if __name__ == "__main__":
    _cli()
