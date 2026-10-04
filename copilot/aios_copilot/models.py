"""Quali modelli AI gratuiti può usare questo dispositivo, e come ottenerli.

Per ogni capacità (testo, vista, voce...) si sceglie il modello più completo che ci
sta, lasciando sempre memoria al resto del sistema. Le dimensioni sono indicative
(versioni quantizzate a 4 bit); i requisiti sono prudenti.

Mai installare da soli: si propone, e dopo il sì dell'utente lo scaricamento avviene
a riposo e in carica, a passi, con pausa e ripresa (vedi learning.DownloadTask).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .hardware import Device
from .privacy import private_dir

CAPABILITIES = {
    "testo": "capire e scrivere (Nova)",
    "vista": "descrivere immagini, leggere documenti fotografati e schermate",
    "lettura": "trascrivere fedelmente documenti scansionati, bollette e tabelle (OCR)",
    "smistamento": "capire al volo di cosa parla una richiesta, per rispondere più in fretta",
    "dettatura": "parlare invece di scrivere",
    "voce": "leggere ad alta voce con una voce naturale",
    "significato": "capire frasi in tutte le lingue e cercare i documenti per significato",
    "immagini": "creare immagini da una descrizione",
    "video": "creare brevi video da una descrizione",
}


@dataclass(frozen=True)
class Model:
    name: str  # nome per Ollama, o etichetta per i file scaricati
    capability: str
    size_gb: float  # spazio su disco
    ram_gb: float  # memoria necessaria (RAM senza GPU, VRAM con GPU)
    engine: str = "ollama"  # ollama | file
    urls: tuple[str, ...] = ()  # per engine=file
    needs_gpu: bool = False
    note: str = ""
    rank: float = 0  # più alto = più completo (nel catalogo remoto: punteggio del laboratorio AIOS)
    license: str = "apache-2.0"
    sha256: tuple[str, ...] = ()  # impronte dei file (engine=file), verificate dopo lo scaricamento
    quant: str = "q4"  # compressione dei pesi: q8, q4 (standard), q3, q2
    active_gb: float = 0.0  # modelli a esperti (MoE): GB letti per ogni parola; 0 = modello denso

    @property
    def open_license(self) -> bool:
        return self.license.lower() in OPEN_LICENSES

    @property
    def score(self) -> float:
        """Completezza attesa: la compressione spinta toglie un po' di qualità al modello."""
        return self.rank - QUANT_PENALTY.get(self.quant, 0.0)


# Un modello più grande compresso a 3 bit di solito batte uno più piccolo a 4 bit; a 2 bit
# la qualità cala di più. La prova sul dispositivo (trial.py) ha comunque l'ultima parola.
QUANT_PENALTY = {"q8": -0.1, "q4": 0.0, "q3": 0.25, "q2": 0.75}
# Senza GPU la velocità dipende da quanti GB si leggono per ogni parola: oltre questa
# dimensione un modello diventa troppo lento per una conversazione.
CPU_MAX_SIZE_GB = 5.0


OPEN_LICENSES = {"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0", "openrail++"}

# Catalogo integrato: vale finché non arriva un catalogo aggiornato e firmato (modelcatalog.py).
# Le licenze indicate qui vanno ricontrollate dal laboratorio AIOS a ogni versione.
BUILTIN_VERSION = 0
BUILTIN: tuple[Model, ...] = (
    # testo (con uso degli strumenti). Qwen 3.5 (2026): a parità di peso nettamente meglio di Qwen 2.5,
    # e vede anche le immagini; il 2B è quello incluso nell'immagine di AIOS.
    Model("qwen3.5:0.8b", "testo", 1.0, 2, rank=1.5, note="incluso in AIOS: compila i campi delle azioni (2× più veloce del 2B)"),
    Model("qwen3.5:2b", "testo", 2.7, 4, rank=3.2, note="incluso in AIOS: risponde anche senza internet"),
    Model("qwen3.5:4b", "testo", 3.4, 6, rank=4.3, note="il migliore per i portatili senza scheda video"),
    Model("qwen3.5:9b", "testo", 6.6, 10, rank=5.2, note="per PC con 16 GB o una scheda video"),
    # Qwen 3.8 (2026): solo in taglie grandi; la versione Flash Next (125B a esperti) per ora è solo per Mac
    Model("qwen3.8:27b", "testo", 18.0, 22, needs_gpu=True, rank=6.6, note="27B: per schede video da 24 GB"),
    # generazione precedente: resta per chi l'ha già scaricata
    Model("qwen2.5:0.5b-instruct", "testo", 0.4, 1.5, rank=1),
    Model("qwen2.5:1.5b-instruct", "testo", 1.0, 3, rank=2),
    Model("qwen2.5:3b-instruct", "testo", 1.9, 5, rank=3, license="qwen-research"),
    Model("qwen2.5:7b-instruct", "testo", 4.7, 6, rank=4),
    Model("qwen2.5:14b-instruct", "testo", 9.0, 12, needs_gpu=True, rank=5),
    Model("qwen2.5:32b-instruct", "testo", 20.0, 24, needs_gpu=True, rank=6),
    # varianti compresse: modelli più grandi in meno memoria (e più veloci da leggere)
    Model("qwen2.5:7b-instruct-q3_K_M", "testo", 3.8, 4.8, rank=4, quant="q3", note="7B compresso a 3 bit"),
    Model("qwen2.5:14b-instruct-q3_K_M", "testo", 7.3, 9, rank=5, quant="q3", note="14B compresso a 3 bit"),
    Model("qwen2.5:14b-instruct-q2_K", "testo", 5.8, 7.5, rank=5, quant="q2", note="14B compresso a 2 bit"),
    Model("qwen2.5:32b-instruct-q3_K_M", "testo", 15.9, 18, needs_gpu=True, rank=6, quant="q3", note="32B compresso a 3 bit"),
    # modelli a esperti: tanti parametri, pochi letti per ogni parola (moe.py li sistema tra GPU, RAM e disco)
    Model("qwen3:30b-a3b-instruct-2507-q4_K_M", "testo", 18.6, 21, rank=5.5, active_gb=2.0,
          note="30B a esperti: per ogni parola ne usa 3, veloce anche senza GPU"),
    Model("gpt-oss:20b", "testo", 13.8, 16, rank=5, active_gb=2.0, note="21B a esperti, 3,6B attivi per parola"),
    # vista (multimodale)
    Model("moondream", "vista", 1.7, 3, rank=1, note="leggero, descrizioni brevi"),
    # MiniCPM-V 4.6 (2026, su Qwen 3.5 0.8B): vista al livello di un 2B in 1,6 GB, adatto anche ai telefoni
    Model("minicpm-v4.6:1b", "vista", 1.6, 3, rank=2.5, license="minicpm", note="leggero ma bravo: foto, schermate e documenti"),
    Model("qwen2.5vl:3b", "vista", 3.2, 6, rank=2, license="qwen-research"),
    Model("qwen2.5vl:7b", "vista", 6.0, 7, rank=3, note="legge bene testi e documenti"),
    Model("llama3.2-vision:11b", "vista", 7.9, 12, needs_gpu=True, rank=4, license="llama3.2"),
    # smistamento: modelli decisionali (API System One di Ollama), vedi smistatore.py
    Model("tev1:0.8b", "smistamento", 0.8, 1.5, rank=1, license="together", note="incluso in AIOS: sceglie gli strumenti giusti per Nova"),
    Model("tev1:4b", "smistamento", 4.5, 6, rank=2, license="together", note="più preciso, per PC con 16 GB"),
    # lettura (OCR specializzato): trascrive pagine intere, tabelle comprese
    # GLM-OCR (Z.ai, 2026): 0,9B, primo su OmniDocBench; legge testo, tabelle e formule (q8_0: 1,6 GB)
    Model("glm-ocr:q8_0", "lettura", 1.6, 2.5, rank=2, license="mit", note="leggero, legge testo e tabelle"),
    Model("deepseek-ocr:3b", "lettura", 6.7, 8, rank=1, license="mit", note="legge documenti e tabelle quasi senza errori"),
    # significato (embedding multilingue)
    # EmbeddingGemma (Google, 2025): 308M, oltre 100 lingue, sotto i 200 MB di memoria; sopra bge-m3 nelle prove multilingue (MMTEB)
    Model("embeddinggemma", "significato", 0.62, 1, rank=3.5, license="gemma", note="leggero e multilingue"),
    Model("granite-embedding:278m", "significato", 0.6, 1, rank=1),
    Model("paraphrase-multilingual", "significato", 0.6, 1, rank=2),
    Model("nomic-embed-text-v2-moe", "significato", 0.96, 1.5, rank=2.5, note="multilingue, a esperti, tutto aperto"),
    Model("bge-m3", "significato", 1.2, 2, rank=3, license="mit"),
    # dettatura (whisper.cpp)
    Model("whisper-base", "dettatura", 0.15, 1, "file",
          ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",), rank=1, license="mit"),
    Model("whisper-small", "dettatura", 0.47, 2, "file",
          ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",), rank=2, license="mit"),
    Model("whisper-large-v3-turbo", "dettatura", 1.6, 4, "file",
          ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",), rank=3,
          note="la più precisa; veloce con GPU o CPU recenti", license="mit"),
    # voce (piper)
    Model("piper-it-paola", "voce", 0.07, 0.5, "file",
          ("https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx",
           "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx.json"),
          rank=1, license="cc-by-4.0"),
    # immagini (stable-diffusion.cpp)
    Model("sd-turbo", "immagini", 2.5, 6, "file",
          ("https://huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors",), rank=1,
          note="un'immagine in pochi secondi con GPU, circa un minuto senza", license="stability-community"),
    Model("sdxl-turbo", "immagini", 6.9, 10, "file",
          ("https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors",),
          needs_gpu=True, rank=2, license="stability-community"),
    # video: solo con GPU molto potenti
    Model("generazione-video", "video", 10.0, 16, "file", (), needs_gpu=True, rank=1,
          note="sperimentale: richiede una GPU con almeno 16 GB"),
)


def catalog() -> tuple[Model, ...]:
    """I modelli del catalogo attivo: quello aggiornato e firmato se c'è, altrimenti quello integrato."""
    from .modelcatalog import active_models

    return active_models()


def find_model(name: str) -> Model | None:
    return next((m for m in catalog() if m.name == name), None)


def open_only() -> bool:
    """Impostazione per aziende e scuole: solo modelli con licenza aperta."""
    return load_config().get("_licenze") == "aperte"


def budget_gb(device: Device) -> tuple[float, bool]:
    """Memoria utilizzabile dai modelli, e se è memoria della GPU.

    Senza GPU si lascia al sistema almeno 2 GB o un quarto della RAM, il più grande.
    """
    if device.vram_gb >= 4:
        return device.vram_gb * 0.9, True
    reserve = max(2.0, device.ram_gb * 0.25)
    return max(0.0, device.ram_gb - reserve), False


def candidates_for(device: Device, capability: str, config: dict[str, str] | None = None) -> list[Model]:
    """I modelli adatti al dispositivo, dal più completo; esclusi quelli già provati e scartati."""
    budget, gpu = budget_gb(device)
    strict = open_only()
    rejected = set(rejected_models(config))
    candidates = [m for m in catalog() if m.capability == capability and (m.open_license or not strict)
                  and m.name not in rejected]
    fitting = []
    for m in candidates:
        if m.active_gb:  # a esperti: conta dove si possono sistemare i pezzi e quanto andrebbe veloce
            from .moe import best_placement

            if best_placement(device, m) is not None:
                fitting.append(m)
            continue
        if m.needs_gpu and not gpu:
            continue
        if m.ram_gb > budget:
            continue
        if not gpu and m.engine == "ollama" and capability == "testo" and m.size_gb > CPU_MAX_SIZE_GB:
            continue
        # Senza GPU un modello deve anche essere veloce: quelli pesanti solo con una CPU robusta.
        heavy = (m.capability == "testo" and m.rank >= 4) or (m.capability in ("vista", "dettatura") and m.rank >= 3)
        if heavy and not gpu and not device.fast_cpu:
            continue
        if capability == "immagini" and not gpu and device.ram_gb < 16:
            continue
        if not m.urls and m.engine == "file":
            continue  # nessun download automatico disponibile (es. video): solo come informazione
        fitting.append(m)
    # a parità di completezza, il più leggero
    return sorted(fitting, key=lambda m: (-m.score, m.size_gb))


def best_for(device: Device, capability: str, config: dict[str, str] | None = None) -> Model | None:
    return next(iter(candidates_for(device, capability, config)), None)


def rejected_models(config: dict[str, str] | None = None) -> list[str]:
    """Modelli scartati dalla prova sul dispositivo (troppo lenti o meno bravi): non si ripropongono."""
    config = config if config is not None else load_config()
    try:
        return list(json.loads(config.get("_scartati", "[]")))
    except ValueError:
        return []


def next_candidate(device: Device, capability: str, current: str = "") -> Model | None:
    """Dopo uno scarto: la variante successiva che sia comunque meglio del modello attuale."""
    floor = find_model(current).score if current and find_model(current) else -1.0
    return next((m for m in candidates_for(device, capability) if m.score > floor and m.name != current), None)


@dataclass
class Proposal:
    capability: str
    model: Model
    current: str = ""  # modello attuale per questa capacità, se c'è
    reason: str = ""


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "aios" / "models.json"


def load_config() -> dict[str, str]:
    try:
        data = json.loads(config_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(data: dict[str, str]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def propose(device: Device, installed: Iterable[str], config: dict[str, str] | None = None) -> list[Proposal]:
    """Miglioramenti possibili rispetto a ciò che è già installato o in uso."""
    config = config if config is not None else load_config()
    installed = set(installed)
    by_name = {m.name: m for m in catalog()}
    proposals = []
    disk_left = device.disk_free_gb - 10  # mai riempire il disco
    for cap in CAPABILITIES:
        best = best_for(device, cap, config)
        if best is None:
            continue
        current_name = config.get(cap) or next((m.name for m in sorted(catalog(), key=lambda m: -m.score)
                                                if m.capability == cap and m.name in installed), "")
        current = by_name.get(current_name)
        if current is not None and current.score >= best.score:
            continue
        if best.size_gb > disk_left:
            continue
        disk_left -= best.size_gb
        reason = f"più completo di {current_name}" if current_name else f"nuova capacità: {CAPABILITIES[cap]}"
        proposals.append(Proposal(cap, best, current_name, reason))
    return proposals


def describe_proposals(device: Device, proposals: list[Proposal]) -> str:
    if not proposals:
        return f"Il tuo dispositivo ({device.summary()}) usa già i modelli migliori che può gestire bene. 👍"
    total = sum(p.model.size_gb for p in proposals)
    lines = [f"Ho guardato il tuo dispositivo: {device.summary()}.", "Puoi usare modelli più completi, gratuiti e in locale:"]
    for n, p in enumerate(proposals, 1):
        note = f" — {p.model.note}" if p.model.note else ""
        if p.model.active_gb:
            from .moe import best_placement

            placement = best_placement(device, p.model)
            if placement is not None:
                note += f" ({placement.describe()})"
        if not p.model.open_license:
            note += f" (licenza {p.model.license}: gratuita per uso personale, con condizioni)"
        lines.append(f"  {n}. {p.capability.capitalize()}: {p.model.name} (~{p.model.size_gb:.1f} GB), {p.reason}{note}")
    lines.append(f"In tutto circa {total:.1f} GB. Dimmi «aggiorna i modelli» (o solo alcuni, es. «installa la vista»): "
                 "li scarico quando il computer è a riposo e in carica.")
    budget, gpu = budget_gb(device)
    if not gpu and best_for(device, "video") is None:
        lines.append("Per creare video in locale servirebbe una GPU con almeno 16 GB: su questo dispositivo non lo propongo.")
    return "\n".join(lines)


def weekly_hint(device: Device, installed: Iterable[str], now: float | None = None) -> str | None:
    """Una riga per il riepilogo del mattino, al massimo una volta alla settimana."""
    now = now or time.time()
    config = load_config()
    if now - float(config.get("_hint", 0)) < 7 * 86400:
        return None
    proposals = propose(device, installed, config)
    if not proposals:
        return None
    config["_hint"] = str(now)
    save_config(config)
    caps = ", ".join(p.capability for p in proposals)
    return f"🧠 Il tuo dispositivo può usare modelli AI più completi ({caps}). Dimmi «che modelli posso usare»."


def main(argv: list[str] | None = None) -> int:
    """aios-modelli [proposte | installa [capacità…] | stato | ripristina [capacità] | solo-aperte]"""
    import sys

    from .hardware import detect
    from .multilingual import installed_models

    args = list(sys.argv[1:] if argv is None else argv) or ["proposte"]
    device = detect()
    try:
        installed = sorted(installed_models())
    except Exception:
        installed = []
    if args[0] == "proposte":
        print(describe_proposals(device, propose(device, installed)))
    elif args[0] == "installa":
        wanted = set(args[1:])
        queue = Queue()
        added = [p.model.name for p in propose(device, installed) if (not wanted or p.capability in wanted) and queue.add(p.model)]
        print(("In coda: " + ", ".join(added) + ". Verranno scaricati a riposo (aios-learn) e attivati da soli.")
              if added else "Niente da aggiungere.")
    elif args[0] == "ripristina":
        from .learning import restore_model

        print(restore_model(args[1] if len(args) > 1 else "testo"))
    elif args[0] == "solo-aperte":
        config = load_config()
        config["_licenze"] = "aperte"
        save_config(config)
        print("D'ora in poi propongo solo modelli con licenza aperta (Apache, MIT, BSD...).")
    elif args[0] == "stato":
        for d in Queue().items:
            pct = f" {100 * d.done_bytes // d.total_bytes}%" if d.total_bytes else ""
            print(f"{d.capability}: {d.name} — {d.status}{pct} {d.error}")
        print("Attivi:", {k: v for k, v in load_config().items() if not k.startswith("_")})
    else:
        print(main.__doc__)
        return 1
    return 0


# --- coda degli scaricamenti -----------------------------------------------------------------


def queue_path() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios") / "downloads.json"


def models_dir() -> Path:
    return private_dir(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "models")


@dataclass
class Download:
    name: str
    capability: str
    status: str = "in coda"  # in coda | in corso | fatto | errore
    done_bytes: int = 0
    total_bytes: int = 0
    error: str = ""


class Queue:
    def __init__(self, path: Path | None = None):
        self.path = path or queue_path()
        try:
            self.items = [Download(**d) for d in json.loads(self.path.read_text())]
        except (OSError, ValueError, TypeError):
            self.items = []

    def save(self) -> None:
        self.path.write_text(json.dumps([asdict(d) for d in self.items], indent=1))

    def add(self, model: Model) -> bool:
        if any(d.name == model.name and d.status != "errore" for d in self.items):
            return False
        self.items = [d for d in self.items if d.name != model.name] + [Download(model.name, model.capability)]
        self.save()
        return True

    def pending(self) -> list[Download]:
        return [d for d in self.items if d.status in ("in coda", "in corso")]


def ollama_pull_step(name: str, seconds: float, url: str | None = None) -> tuple[bool, int, int]:
    """Scarica per al massimo `seconds`; Ollama riprende da dove era rimasto. → (finito, fatti, totale)."""
    base = (url or os.environ.get("AIOS_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
    req = urllib.request.Request(f"{base}/api/pull", data=json.dumps({"model": name, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    end = time.monotonic() + seconds
    done = total = 0
    with urllib.request.urlopen(req, timeout=60) as resp:
        for line in resp:
            event = json.loads(line or b"{}")
            if event.get("error"):
                raise RuntimeError(event["error"])
            done, total = event.get("completed", done), event.get("total", total)
            if event.get("status") == "success":
                return True, total, total
            if time.monotonic() > end:
                return False, done, total  # chiudere la connessione mette in pausa
    return False, done, total


def file_download_step(urls: Iterable[str], target_dir: Path, seconds: float,
                       opener: Callable[[urllib.request.Request], Any] = urllib.request.urlopen) -> tuple[bool, int, int]:
    """Scaricamento riprendibile (HTTP Range) di uno o più file, a tempo."""
    end = time.monotonic() + seconds
    done_all = total_all = 0
    finished = True
    for url in urls:
        target = target_dir / url.rsplit("/", 1)[-1]
        part = target.with_suffix(target.suffix + ".part")
        if target.exists():
            size = target.stat().st_size
            done_all += size
            total_all += size
            continue
        have = part.stat().st_size if part.exists() else 0
        if time.monotonic() >= end:
            finished = False
            done_all += have
            continue
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-", "User-Agent": "AIOS/0.1"})
        with opener(req) as resp:
            length = int(resp.headers.get("Content-Length") or 0)
            if have and getattr(resp, "status", 206) == 200:
                have = 0  # il server non supporta la ripresa: si ricomincia
            total = have + length
            with open(part, "ab" if have else "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    have += len(chunk)
                    if time.monotonic() >= end:
                        break
        done_all += have
        total_all += total
        if total and have >= total:
            part.replace(target)
        else:
            finished = False
    return finished, done_all, total_all


if __name__ == "__main__":
    import sys

    sys.exit(main())
