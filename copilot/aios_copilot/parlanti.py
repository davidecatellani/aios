"""Chi parla e quando (diarizzazione): Nemotron 3 Diarization di NVIDIA (OpenMDW 1.1), tutto sul computer.

Dato un audio, il modello dice quante voci diverse ci sono (fino a 8) e quando parla ciascuna:
[(0.0, 8.2, 0), (8.2, 15.4, 1), …] — persona 0, 1… nell'ordine in cui hanno parlato la prima volta.
Non capisce le parole (quello è Parakeet): unendo le due cose si ottengono trascrizioni con chi parla.

Usi in AIOS:
- ascolto: in una frase con più voci (TV accesa, ospiti) si tiene solo la parte della voce principale;
- «trascrivi la riunione» / i messaggi vocali: testo diviso per persona.

Gira con onnxruntime, senza torch (modello ONNX int8, 120 MB). Il calcolo delle caratteristiche dell'audio
e la memoria delle voci tra un pezzo e l'altro (Arrival-Order Speaker Cache) sono riscritti qui in numpy
dall'implementazione di riferimento (transformers, Nemotron3Diarization), con gli stessi parametri.
"""

from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any

MODEL_DIRS = [Path("/usr/share/aios/parlanti")]
RATE = 16000
N_FFT, HOP, WIN, N_MELS, PREEMPH = 512, 160, 400, 128, 0.97
LOG_GUARD = 2.0 ** -24
SUB = 8  # 8 frame da 10 ms → un frame del modello ogni 80 ms
# registrazione intera (modalità «offline» del modello di riferimento)
CHUNK, RIGHT, FIFO, UPDATE = 340, 40, 40, 300
# memoria delle voci
CACHE, SILENCE_SLOTS, SPEAKERS = 264, 1, 8
SCORE_THRESHOLD, LATEST_BOOST, STRONG_RATE, WEAK_RATE, MIN_POSITIVE_RATE = 0.25, 0.05, 0.75, 1.5, 0.5


def model_dir() -> Path | None:
    user = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "aios" / "modelli" / "parlanti"
    for d in [user, *MODEL_DIRS]:
        if (d / "model_quantized.onnx").exists():
            return d
    return None


def available() -> bool:
    if os.environ.get("AIOS_PARLANTI", "") == "spento" or model_dir() is None:
        return False
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


# --- caratteristiche dell'audio (come NemotronAsrStreamingFeatureExtractor) --------------------------------
def _mel_filters() -> Any:
    """Banco di filtri mel «slaney» (come librosa.filters.mel con norm="slaney")."""
    import numpy as np

    def hz_to_mel(f: Any) -> Any:
        f = np.asanyarray(f, dtype=np.float64)
        f_sp, min_log_hz = 200.0 / 3, 1000.0
        mels = f / f_sp
        min_log_mel, logstep = min_log_hz / f_sp, math.log(6.4) / 27.0
        return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep, mels)

    def mel_to_hz(m: Any) -> Any:
        m = np.asanyarray(m, dtype=np.float64)
        f_sp, min_log_hz = 200.0 / 3, 1000.0
        min_log_mel, logstep = min_log_hz / f_sp, math.log(6.4) / 27.0
        return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)

    fft_freqs = np.linspace(0, RATE / 2, 1 + N_FFT // 2)
    mel_f = mel_to_hz(np.linspace(hz_to_mel(0.0), hz_to_mel(RATE / 2), N_MELS + 2))
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fft_freqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    weights *= (2.0 / (mel_f[2:N_MELS + 2] - mel_f[:N_MELS]))[:, None]
    return weights.astype(np.float32)


_MEL: Any = None


def features(samples: Any) -> Any:
    """Audio a 16 kHz (float tra -1 e 1) → log-mel (frame, 128), un frame ogni 10 ms."""
    import numpy as np

    global _MEL
    if _MEL is None:
        _MEL = _mel_filters()
    x = np.asarray(samples, dtype=np.float32)
    n = len(x)
    x = np.concatenate([x[:1], x[1:] - PREEMPH * x[:-1]])
    x = np.pad(x, (N_FFT // 2, N_FFT // 2))
    window = np.zeros(N_FFT, dtype=np.float32)
    off = (N_FFT - WIN) // 2
    window[off:off + WIN] = np.hanning(WIN).astype(np.float32)  # come torch.hann_window(periodic=False)
    count = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(count)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * window, n=N_FFT, axis=1)) ** 2
    mel = np.log(spec.astype(np.float32) @ _MEL.T + LOG_GUARD)
    valid = n // HOP  # con center=True i frame validi sono floor(L / hop)
    mel[valid:] = 0.0
    return mel[:max(valid, 1)].astype(np.float32)


# --- la memoria delle voci (come Nemotron3DiarizationSpeakerCache) ----------------------------------------
class SpeakerCache:
    def __init__(self, fifo_length: int = FIFO, update_period: int = UPDATE):
        import numpy as np

        self.np = np
        self.fifo_length, self.update_period = fifo_length, update_period
        budget = CACHE // SPEAKERS - SILENCE_SLOTS
        self.min_positive = math.floor(budget * MIN_POSITIVE_RATE)
        self.strong = math.floor(budget * STRONG_RATE)
        self.weak = math.floor(budget * WEAK_RATE)
        self.embeds = np.zeros((0, 512), np.float32)
        self.probs = np.zeros((0, SPEAKERS), np.float32)
        self.fifo = np.zeros((0, 512), np.float32)
        self.compressed = False

    def get(self) -> Any:
        return self.np.concatenate([self.embeds, self.fifo], axis=0)

    def _pool(self, logits: Any) -> Any:
        np = self.np
        probs = 1.0 / (1.0 + np.exp(-logits))
        n = probs.shape[0] // SUB
        return probs[:n * SUB].reshape(n, SUB, SPEAKERS).mean(axis=1)

    def update(self, input_embeds: Any, logits: Any, silence: Any, n_chunk: int) -> None:
        np = self.np
        n_cache, n_fifo = len(self.embeds), len(self.fifo)
        probs = self._pool(logits)
        start = n_cache + n_fifo
        fifo_embeds = np.concatenate([self.fifo, input_embeds[start:start + n_chunk]], axis=0)
        popped = 0
        if len(fifo_embeds) > self.fifo_length:
            popped = min(max(self.update_period, len(fifo_embeds) - self.fifo_length), len(fifo_embeds))
        if popped:
            fifo_probs = probs[n_cache:n_cache + len(fifo_embeds)]
            stored = self.probs if self.compressed else probs[:n_cache]
            cache_e = np.concatenate([self.embeds, fifo_embeds[:popped]], axis=0)
            cache_p = np.concatenate([stored, fifo_probs[:popped]], axis=0)
            fifo_embeds = fifo_embeds[popped:]
            if len(cache_e) > CACHE:
                cache_e, cache_p = self._compress(cache_e, cache_p, silence)
                self.compressed = True
            self.embeds, self.probs = cache_e, cache_p
        self.fifo = fifo_embeds

    def _scores(self, probs: Any) -> Any:
        np = self.np
        lp = np.log(np.maximum(probs, SCORE_THRESHOLD))
        lc = np.log(np.maximum(1.0 - probs, SCORE_THRESHOLD))
        scores = lp - lc + lc.sum(axis=-1, keepdims=True) - math.log(0.5)
        speech = probs > 0.5
        scores = np.where(speech, scores, -np.inf)
        positive = scores > 0
        enough = positive.sum(axis=0, keepdims=True) >= self.min_positive
        return np.where(~positive & speech & enough, -np.inf, scores)

    def _boost(self, scores: Any, k: int, boost: float) -> Any:
        np = self.np
        idx = np.argpartition(-scores, k - 1, axis=0)[:k] if k else np.zeros((0, scores.shape[1]), int)
        out = scores.copy()
        for s in range(scores.shape[1]):
            out[idx[:, s], s] += boost
        return out

    def _compress(self, embeds: Any, probs: Any, silence: Any) -> tuple[Any, Any]:
        np = self.np
        n = len(embeds)
        with np.errstate(invalid="ignore"):
            scores = self._scores(probs)
            scores[CACHE:] += LATEST_BOOST
            scores = self._boost(scores, self.strong, -2.0 * math.log(0.5))
            scores = self._boost(scores, self.weak, -math.log(0.5))
        scores = np.concatenate([scores, np.full((SILENCE_SLOTS, SPEAKERS), np.inf)], axis=0)
        embeds = np.concatenate([embeds, silence[None, :]], axis=0)
        probs = np.concatenate([probs, np.zeros((1, SPEAKERS), probs.dtype)], axis=0)
        scored = n + SILENCE_SLOTS
        sentinel = scored * SPEAKERS
        flat = scores.T.reshape(-1)  # speaker per speaker, poi i frame
        top = np.argpartition(-flat, CACHE - 1)[:CACHE]
        top = np.where(flat[top] == -np.inf, sentinel, top)
        top.sort()
        frames = np.where(top == sentinel, n, np.minimum(top % scored, n))
        return embeds[frames], probs[frames]


# --- il modello -------------------------------------------------------------------------------------------
class Parlanti:
    def __init__(self, folder: Path | None = None, threads: int = 2):
        self.folder = folder or model_dir()
        self.threads = threads
        self._sess: Any = None
        self._lock = threading.Lock()

    def _session(self) -> Any:
        import onnxruntime as ort

        if self._sess is None:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = self.threads
            self._sess = ort.InferenceSession(str(self.folder / "model_quantized.onnx"), opts,
                                              providers=["CPUExecutionProvider"])
        return self._sess

    def probabilities(self, samples: Any) -> Any:
        """Audio a 16 kHz → probabilità (frame da 10 ms, 8 persone)."""
        import numpy as np

        feats = features(samples)
        n_frames = len(feats)
        pad = -n_frames % SUB
        feats = np.pad(feats, ((0, pad), (0, 0)))
        n_embeds = len(feats) // SUB
        cache = SpeakerCache()
        out = []
        with self._lock:
            sess = self._session()
            for start in range(0, n_embeds, CHUNK):
                end = min(start + CHUNK, n_embeds)
                stop = min(end + RIGHT, n_embeds)
                chunk = feats[start * SUB:stop * SUB][None]
                cached = cache.get()[None].astype(np.float32)
                mask = np.ones((1, cached.shape[1] + chunk.shape[1] // SUB), np.int64)  # frame del modello (80 ms)
                logits, chunk_embeds, silence = sess.run(None, {"input_features": chunk, "cached_embeds": cached,
                                                                "attention_mask": mask})
                logits, chunk_embeds = logits[0], chunk_embeds[0]
                n_cached = cached.shape[1]
                if logits.shape[0] < (n_cached + chunk_embeds.shape[0]) * SUB:  # solo i frame del pezzo
                    logits = np.concatenate([np.zeros((n_cached * SUB, SPEAKERS), np.float32) - 20, logits])
                cache.update(np.concatenate([cache.get(), chunk_embeds], axis=0), logits, silence, end - start)
                out.append(logits[n_cached * SUB:(n_cached + end - start) * SUB])
        logits = np.concatenate(out, axis=0)[:n_frames]
        return 1.0 / (1.0 + np.exp(-logits))

    def turns(self, samples: Any, threshold: float = 0.5, min_seconds: float = 0.25) -> list[tuple[float, float, int]]:
        """→ [(inizio, fine, persona)] in secondi, persone numerate nell'ordine in cui hanno parlato."""
        import numpy as np

        probs = self.probabilities(samples)
        active = probs > threshold
        segments = []
        for s in range(active.shape[1]):
            changes = np.diff(np.concatenate([[0], active[:, s].astype(int), [0]]))
            for a, b in zip(np.nonzero(changes == 1)[0], np.nonzero(changes == -1)[0]):
                if (b - a) * HOP / RATE >= min_seconds:
                    segments.append((round(a * HOP / RATE, 2), round(b * HOP / RATE, 2), s))
        return sorted(segments)


def merge(turns: list[tuple[float, float, int]], gap: float = 0.6) -> list[tuple[float, float, int]]:
    """Unisce i pezzi consecutivi della stessa persona (pause brevi comprese), in ordine di tempo."""
    out: list[tuple[float, float, int]] = []
    for a, b, s in sorted(turns):
        if out and out[-1][2] == s and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], b), s)
        elif out and a < out[-1][1] and out[-1][2] != s:  # voci sovrapposte: la nuova comincia dove finisce l'altra
            if b > out[-1][1]:
                out.append((out[-1][1], b, s))
        else:
            out.append((a, b, s))
    return out


def main_voice(samples: Any, turns: list[tuple[float, float, int]]) -> Any:
    """Solo i pezzi della voce che parla di più (la persona che si rivolge a Nova, non la TV o un ospite)."""
    import numpy as np

    if len({s for *_, s in turns}) < 2:
        return samples
    talk: dict[int, float] = {}
    for a, b, s in turns:
        talk[s] = talk.get(s, 0.0) + b - a
    who = max(talk, key=talk.get)
    pieces = [samples[int(a * RATE):int(b * RATE)] for a, b, s in turns if s == who]
    return np.concatenate(pieces) if pieces else samples


def transcript(samples: Any, transcribe: Any, names: list[str] | None = None) -> str:
    """Trascrizione divisa per persona: «Persona 1: …». `transcribe(pcm16) → testo` (Parakeet)."""
    import numpy as np

    lines = []
    for a, b, s in merge(shared().turns(samples)):
        piece = samples[int(max(0.0, a - 0.1) * RATE):int((b + 0.1) * RATE)]
        text = transcribe((np.clip(piece, -1, 1) * 32767).astype(np.int16).tobytes()).strip()
        if text:
            who = names[s] if names and s < len(names) else f"Persona {s + 1}"
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


def from_pcm(pcm: bytes) -> Any:
    import numpy as np

    return np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0


_shared: Parlanti | None = None


def shared() -> Parlanti:
    global _shared
    if _shared is None:
        _shared = Parlanti()
    return _shared
