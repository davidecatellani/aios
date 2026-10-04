"""Addestra l'adattatore «documenti» del nucleo: leggere bollette, scontrini e avvisi (immagine → campi o testo).

    python addestramento/addestra_documenti.py --base base-hf --dati dati-documenti --uscita lora/documenti

Come addestra.py, ma ogni esempio ha un'immagine: il modello intero (con la parte che vede) resta com'è,
impara solo il modello di testo (LoRA sugli strati della seconda metà). La richiesta è costruita con il
modello di chat di Qwen, lo stesso che usa llama-server (/v1/chat/completions) quando Nova legge un documento.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aios_copilot.nucleo import SYSTEM_DOCUMENTO  # noqa: E402

END = "<|im_end|>"
PROJ = "q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--dati", required=True)
    ap.add_argument("--uscita", required=True)
    ap.add_argument("--epoche", type=float, default=1.0)
    ap.add_argument("--rango", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--accumula", type=int, default=8)
    ap.add_argument("--strati", type=float, default=0.5)
    ap.add_argument("--max-minuti", type=float, default=None)
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model
    from PIL import Image
    from transformers import AutoModelForImageTextToText, AutoProcessor

    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(args.base)
    model = AutoModelForImageTextToText.from_pretrained(args.base, dtype=torch.bfloat16 if device == "cuda" else torch.float32)
    model.to(device)
    model.config.use_cache = False
    text_cfg = getattr(model.config, "text_config", model.config)
    n_layers = int(text_cfg.num_hidden_layers)
    first = int(n_layers * (1 - args.strati))
    layers = "|".join(str(i) for i in range(first, n_layers))
    target = rf".*language_model\.layers\.({layers})\..*\.({PROJ})$"
    model = get_peft_model(model, LoraConfig(r=args.rango, lora_alpha=args.rango * 2, lora_dropout=0.05,
                                             target_modules=target, task_type="CAUSAL_LM"))
    model.print_trainable_parameters()
    print(f"strati che imparano: {first}–{n_layers - 1} di {n_layers}", flush=True)

    base = Path(args.dati)
    rows = [json.loads(line) for line in (base / "documenti.jsonl").read_text().splitlines() if line.strip()]
    rng = random.Random(0)
    rng.shuffle(rows)
    tok = processor.tokenizer

    def encode(row: dict) -> dict:
        messages = [{"role": "system", "content": SYSTEM_DOCUMENTO},
                    {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": row["prompt"]}]}]
        prompt = processor.apply_chat_template(messages, add_generation_prompt=True, enable_thinking=False)
        image = Image.open(base / row["immagine"]).convert("RGB")
        inputs = processor(text=[prompt], images=[image], return_tensors="pt")
        answer = tok(row["risposta"] + END, add_special_tokens=False, return_tensors="pt")["input_ids"]
        ids = torch.cat([inputs["input_ids"], answer], dim=1)
        labels = torch.cat([torch.full_like(inputs["input_ids"], -100), answer], dim=1)
        extra = {k: v for k, v in inputs.items() if k not in ("input_ids", "attention_mask")}
        return {"input_ids": ids, "attention_mask": torch.ones_like(ids), "labels": labels, **extra}

    total = max(1, int(math.ceil(len(rows) / args.accumula) * args.epoche))
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 10) * max(0.05, 1 - s / total))
    started = time.monotonic()
    model.train()
    k = 0
    for step in range(total):
        loss_sum = 0.0
        for _ in range(args.accumula):
            batch = {key: v.to(device) for key, v in encode(rows[k % len(rows)]).items()}
            k += 1
            loss = model(**batch).loss / args.accumula
            loss.backward()
            loss_sum += loss.item()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        opt.zero_grad()
        elapsed = (time.monotonic() - started) / 60
        if step % 5 == 0 or step == total - 1:
            print(f"passo {step + 1}/{total}  perdita {loss_sum:.4f}  {elapsed:.1f} min", flush=True)
        if args.max_minuti and elapsed > args.max_minuti:
            print(f"tempo finito: mi fermo al passo {step + 1}", flush=True)
            break
    out = Path(args.uscita)
    model.save_pretrained(out)
    print(f"adattatore salvato in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
