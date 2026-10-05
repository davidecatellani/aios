"""AI in cloud, quando serve davvero: un modello grande via OpenRouter (DeepSeek, Claude, Gemini, OpenAI,
Mistral… con una chiave sola), sopra i modelli locali.

- Spento finché l'utente non lo accende (Impostazioni › AI in cloud, o «accendi l'AI in cloud»).
- Decide Laya, in locale: al cloud vanno solo le richieste di «ragionamento» (o quelle chieste esplicitamente:
  «chiedilo al modello grande»); comandi, posta, agenda e domande semplici restano sul PC.
- Privacy: se nella conversazione ci sono dati privati (mail, documenti, file letti), prima di mandarli fuori si
  chiede (agent.py); con «mai dati privati» quelle richieste restano in locale.
- Limiti di spesa al giorno e al mese (in dollari, come li conta OpenRouter): raggiunti, si torna in locale con un
  avviso. Ogni risposta riporta il costo vero (usage.cost), che si somma qui.
- La chiave sta nel portachiavi del sistema (vault.py), mai su disco in chiaro.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable

URL = "https://openrouter.ai/api/v1"
KEY = "openrouter-chiave"
DEFAULT_MODEL = "deepseek/deepseek-chat"
DEFAULTS = {"attivo": False, "modello": DEFAULT_MODEL, "limite_giorno": 1.0, "limite_mese": 10.0,
            "privacy": "chiedi"}  # chiedi | mai (i dati privati non escono mai) | sempre
TIMEOUT = 180


class CloudError(RuntimeError):
    pass


def _dir(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, default)) / "aios"


def config_path() -> Path:
    return _dir("XDG_CONFIG_HOME", Path.home() / ".config") / "cloud.json"


def usage_path() -> Path:
    return _dir("XDG_DATA_HOME", Path.home() / ".local" / "share") / "cloud-uso.json"


def settings() -> dict[str, Any]:
    conf = dict(DEFAULTS)
    try:
        data = json.loads(config_path().read_text())
        if isinstance(data, dict):
            conf.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    return conf


def save_settings(changes: dict[str, Any]) -> dict[str, Any]:
    conf = settings()
    for k, v in changes.items():
        if k == "attivo":
            conf[k] = bool(v)
        elif k in ("limite_giorno", "limite_mese"):
            conf[k] = max(0.0, min(1000.0, float(v)))
        elif k == "modello" and isinstance(v, str) and 2 < len(v) < 120:
            conf[k] = v.strip()
        elif k == "privacy" and v in ("chiedi", "mai", "sempre"):
            conf[k] = v
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(conf, indent=1))
    return conf


def key() -> str:
    from . import vault

    return (vault.load(KEY) or os.environ.get("OPENROUTER_API_KEY", "")).strip()


def set_key(value: str) -> None:
    from . import vault

    vault.store(KEY, value.strip())


class Usage:
    """La spesa: per giorno, in dollari, con il numero di richieste."""

    def __init__(self, path: Path | None = None, today: Callable[[], date] = date.today):
        self.path, self.today = path or usage_path(), today

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def add(self, cost: float, model: str) -> None:
        data = self.load()
        day = self.today().isoformat()
        row = data.setdefault(day, {"costo": 0.0, "richieste": 0, "modelli": {}})
        row["costo"] = round(row["costo"] + max(0.0, cost), 6)
        row["richieste"] += 1
        row["modelli"][model] = row["modelli"].get(model, 0) + 1
        keep = sorted(data)[-62:]  # due mesi bastano
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({k: data[k] for k in keep}, indent=1))

    def spent(self) -> tuple[float, float, int]:
        """→ (oggi, questo mese, richieste di oggi)."""
        data, today = self.load(), self.today()
        day = data.get(today.isoformat(), {})
        month = sum(v.get("costo", 0.0) for k, v in data.items() if k.startswith(today.strftime("%Y-%m")))
        return float(day.get("costo", 0.0)), float(month), int(day.get("richieste", 0))


def budget_left(conf: dict[str, Any] | None = None, usage: Usage | None = None) -> str:
    """"" se si può spendere; altrimenti il motivo."""
    conf = conf or settings()
    day, month, _ = (usage or Usage()).spent()
    if day >= conf["limite_giorno"]:
        return f"limite di oggi raggiunto ({day:.2f} $ su {conf['limite_giorno']:.2f} $)"
    if month >= conf["limite_mese"]:
        return f"limite del mese raggiunto ({month:.2f} $ su {conf['limite_mese']:.2f} $)"
    return ""


def _http(url: str, payload: dict[str, Any] | None, api_key: str, timeout: int = TIMEOUT) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "HTTP-Referer": "https://github.com/davidecatellani/aios",
               "X-Title": "AIOS"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code == 401:
            raise CloudError("chiave di OpenRouter non valida") from exc
        if exc.code == 402:
            raise CloudError("credito di OpenRouter finito") from exc
        raise CloudError(f"OpenRouter ha risposto {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CloudError(f"OpenRouter non raggiungibile ({exc})") from exc


class CloudModel:
    """Un modello in cloud con la stessa interfaccia dei modelli locali (chat con gli strumenti)."""

    supports_stream = False

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 post: Callable[..., dict[str, Any]] = _http, usage: Usage | None = None):
        self.model = model or settings()["modello"]
        self.api_key = key() if api_key is None else api_key
        self.post, self.usage = post, usage or Usage()
        self.think = False
        self.last_cost = 0.0

    @property
    def label(self) -> str:
        return self.model.split("/", 1)[-1]

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        from .moe import from_openai, to_openai

        payload: dict[str, Any] = {"model": self.model, "messages": to_openai(messages), "usage": {"include": True}}
        if tools:
            payload["tools"] = tools
        reply = self.post(f"{URL}/chat/completions", payload, self.api_key)
        if "error" in reply and not reply.get("choices"):
            raise CloudError(str(reply["error"].get("message", reply["error"]))[:300])
        cost = float((reply.get("usage") or {}).get("cost") or 0.0)
        self.last_cost = cost
        self.usage.add(cost, self.model)
        return from_openai(reply)

    def warmup(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        pass


def models(post: Callable[..., dict[str, Any]] = _http) -> list[dict[str, Any]]:
    """I modelli di OpenRouter che sanno usare gli strumenti, con il prezzo (per milione di token)."""
    data = post(f"{URL}/models", None, "")
    out = []
    for m in data.get("data") or []:
        if "tools" not in (m.get("supported_parameters") or []):
            continue
        price = m.get("pricing") or {}
        try:
            prompt, completion = float(price.get("prompt", 0)) * 1e6, float(price.get("completion", 0)) * 1e6
        except (TypeError, ValueError):
            continue
        out.append({"id": m["id"], "nome": m.get("name", m["id"]), "ingresso": round(prompt, 2), "uscita": round(completion, 2),
                    "contesto": m.get("context_length", 0)})
    return out


# richieste per il modello grande: le chiede l'utente
WANTS_CLOUD = ("modello grande", "in cloud", "nel cloud", "con claude", "con gemini", "con deepseek", "con gpt",
               "ragiona bene", "pensaci bene", "con l'ai più potente")


class Escalation:
    """Quale modello usa una richiesta: il locale, o quello in cloud (se acceso, con budget, e se serve)."""

    def __init__(self, make: Callable[[], CloudModel] = CloudModel, conf: Callable[[], dict[str, Any]] = settings,
                 budget: Callable[[], str] = budget_left, has_key: Callable[[], bool] = lambda: bool(key())):
        self.make, self.conf, self.budget, self.has_key = make, conf, budget, has_key
        self.note = ""  # perché no (limite raggiunto…), da dire all'utente

    def choose(self, text: str, route: str | None) -> CloudModel | None:
        self.note = ""
        conf = self.conf()
        asked = any(w in text.lower() for w in WANTS_CLOUD)
        if not conf.get("attivo") or not (asked or route == "ragionamento"):
            return None
        if not self.has_key():
            self.note = "L'AI in cloud è accesa ma manca la chiave di OpenRouter (Impostazioni › AI in cloud)."
            return None
        why = self.budget()
        if why:
            self.note = f"Rispondo in locale: per l'AI in cloud {why}."
            return None
        return self.make()

    def private_ok(self) -> str:
        return self.conf().get("privacy", "chiedi")


def status_text() -> str:
    conf = settings()
    day, month, n = Usage().spent()
    state = "accesa" if conf["attivo"] else "spenta"
    keyed = "chiave impostata" if key() else "chiave mancante"
    return (f"AI in cloud {state} ({keyed}), modello {conf['modello']}. Oggi {n} richieste, {day:.2f} $ "
            f"(limite {conf['limite_giorno']:.2f} $); questo mese {month:.2f} $ (limite {conf['limite_mese']:.2f} $).")


def now() -> float:
    return time.time()
