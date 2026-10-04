"""Addestra un adattatore LoRA del nucleo sul modello base (Qwen3.5 0.8B).

    python addestramento/addestra.py --dati dati/smistamento.jsonl --uscita lora/smistamento

Si impara solo la risposta (la parte dopo il prompt), con pochi parametri in più sul modello (LoRA):
il modello base resta quello di tutti, e l'adattatore pesa pochi MB. Gira anche senza scheda video
(lento: un'ora o due su un processore normale); con una scheda NVIDIA bastano pochi minuti.
Poi llama.cpp lo converte in GGUF (convert_lora_to_gguf.py) per aios-nucleo.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

BASE = "Qwen/Qwen3.5-0.8B"
END = "<|im_end|>"
# i nomi che llama.cpp sa convertire (solo il modello di testo: Qwen3_5ForCausalLM)
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def load_rows(path: Path, limit: int | None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dati", required=True)
    ap.add_argument("--uscita", required=True)
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--epoche", type=float, default=2.0)
    ap.add_argument("--rango", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--lotto", type=int, default=4, help="esempi per passo (poca memoria: GitHub dà 7 GB)")
    ap.add_argument("--accumula", type=int, default=2, help="passi sommati prima di aggiornare")
    ap.add_argument("--strati", type=float, default=0.5,
                    help="parte finale del modello che impara (0.5 = metà): meno calcoli all'indietro, più veloce")
    ap.add_argument("--max-esempi", type=int, default=None)
    ap.add_argument("--max-minuti", type=float, default=None, help="si ferma e salva dopo questo tempo")
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(args.base)
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    try:
        model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=dtype)
    except ValueError:  # Qwen3.5 nasce anche per le immagini: si carica intero, l'adattatore tocca solo il testo
        from transformers import AutoModelForImageTextToText

        model = AutoModelForImageTextToText.from_pretrained(args.base, torch_dtype=dtype)
    print("modello:", type(model).__name__, flush=True)
    model.to(device)
    model.config.use_cache = False
    n_layers = int(getattr(model.config, "num_hidden_layers", 0) or getattr(getattr(model.config, "text_config", None),
                                                                            "num_hidden_layers", 24))
    first = int(n_layers * (1 - args.strati))
    model = get_peft_model(model, LoraConfig(r=args.rango, lora_alpha=args.rango * 2, lora_dropout=0.05,
                                             target_modules=TARGETS, layers_to_transform=list(range(first, n_layers)),
                                             layers_pattern="layers", task_type="CAUSAL_LM"))
    print(f"strati che imparano: {first}–{n_layers - 1} di {n_layers}", flush=True)
    model.print_trainable_parameters()

    rows = load_rows(Path(args.dati), args.max_esempi)
    examples = []
    for r in rows:
        p = tok(r["prompt"], add_special_tokens=False)["input_ids"]
        a = tok(r["risposta"] + END, add_special_tokens=False)["input_ids"]
        examples.append((p + a, [-100] * len(p) + a))  # la perdita conta solo sulla risposta
    steps_per_epoch = math.ceil(len(examples) / (args.lotto * args.accumula))
    total = max(1, int(steps_per_epoch * args.epoche))
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 20) * max(0.05, 1 - s / total))
    pad = tok.pad_token_id if tok.pad_token_id is not None else 0
    rng = random.Random(0)
    started = time.monotonic()
    model.train()
    order: list[int] = []
    for step in range(total):
        for _ in range(args.accumula):
            if not order:
                order = list(range(len(examples)))
                rng.shuffle(order)
            batch = [examples[order.pop()] for _ in range(min(args.lotto, len(order)))]
            width = max(len(ids) for ids, _ in batch)
            ids = torch.tensor([x + [pad] * (width - len(x)) for x, _ in batch], device=device)
            labels = torch.tensor([y + [-100] * (width - len(y)) for _, y in batch], device=device)
            mask = torch.tensor([[1] * len(x) + [0] * (width - len(x)) for x, _ in batch], device=device)
            loss = model(input_ids=ids, attention_mask=mask, labels=labels).loss
            (loss / args.accumula).backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        opt.zero_grad()
        elapsed = (time.monotonic() - started) / 60
        if step % 5 == 0 or step == total - 1:
            print(f"passo {step + 1}/{total}  perdita {loss.item():.4f}  {elapsed:.1f} min", flush=True)
        if args.max_minuti and elapsed > args.max_minuti:
            print(f"tempo finito: mi fermo al passo {step + 1}", flush=True)
            break
    out = Path(args.uscita)
    model.save_pretrained(out)
    tok.save_pretrained(out)
    print(f"adattatore salvato in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
