#!/usr/bin/env python3
"""Fine-tune the semantic engine's bi-encoder (BAAI/bge-small-en-v1.5, MIT) on label/charge meanings.

Objective: prototype classification in embedding space. Each batch embeds the anchors
("charge: OTHER CHARGES THC | mode: sea | ...") and all 34 class texts with the SAME encoder;
logits = cos(anchor, class) / tau, cross-entropy on the gold class. At inference the engine
scores the same class prototypes plus every alias in the alias bank, so reviewer-taught aliases
work without retraining.

Overfitting controls: duplicates collapsed, weight decay, 3 epochs max, eval every epoch on val
with the UNSEEN-alias slice as the early-stopping metric (wording never shown in training).

  python training/train_semantic_encoder.py --data work/sem_v2 --output checkpoints/semantic-encoder
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invoice_extractor.semantic import CLASS_DESCRIPTIONS, CLASSES, model_input  # noqa: E402


def load(path: Path, dedupe: bool) -> list[dict]:
    rows, seen = [], set()
    for line in path.open():
        r = json.loads(line)
        x = model_input(r["kind"], r["text"], r["context"])
        key = (x, r["field"])
        if dedupe and key in seen:
            continue
        seen.add(key)
        rows.append({"x": x, "y": CLASSES.index(r["field"]), "kind": r["kind"],
                     "unseen": r["bucket"] in ("val", "test")})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="work/sem_v2")
    ap.add_argument("--base", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--output", default="checkpoints/semantic-encoder")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--tau", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=91731)
    a = ap.parse_args()
    from sentence_transformers import SentenceTransformer

    random.seed(a.seed)
    torch.manual_seed(a.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    data = Path(a.data)
    train = load(data / "train.jsonl", dedupe=True)
    val = load(data / "val.jsonl", dedupe=True)
    print(json.dumps({"train": len(train), "val": len(val), "val_unseen": sum(v["unseen"] for v in val)}), flush=True)
    model = SentenceTransformer(a.base, device=dev)
    cls_text = [f"class: {c} | {CLASS_DESCRIPTIONS[c]}" for c in CLASSES]
    allowed = {"label": torch.tensor([c in ("Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No",
                                             "Vendor Name", "Bill of Lading / HBL", "WEIGHT", "Ex Rate", "Master B/L",
                                             "Chargeable Weight", "NONE") for c in CLASSES], device=dev)}
    allowed["charge"] = ~allowed["label"]
    allowed["charge"][CLASSES.index("NONE")] = True

    def embed(texts: list[str]) -> torch.Tensor:
        feats = model.tokenize(texts)
        feats = {k: (v.to(dev) if hasattr(v, "to") else v) for k, v in feats.items()}
        return F.normalize(model(feats)["sentence_embedding"], dim=-1)

    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
    steps = a.epochs * (len(train) // a.batch + 1)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.05)

    @torch.no_grad()
    def evaluate() -> dict:
        model.eval()
        c = embed(cls_text)
        ok = ok_u = n_u = 0
        for i in range(0, len(val), 512):
            b = val[i:i + 512]
            q = embed([r["x"] for r in b])
            s = q @ c.T
            for j, r in enumerate(b):
                sj = s[j].masked_fill(~allowed[r["kind"]], -1e4)
                hit = int(sj.argmax()) == r["y"]
                ok += hit
                if r["unseen"]:
                    n_u += 1
                    ok_u += hit
        model.train()
        return {"val_acc": ok / len(val), "val_unseen_acc": ok_u / max(1, n_u), "val_seen_acc": (ok - ok_u) / max(1, len(val) - n_u)}

    out = Path(a.output)
    best, history, bad = -1.0, [], 0
    m0 = evaluate()
    history.append({"epoch": 0, **m0})
    print(json.dumps(history[-1]), flush=True)
    model.train()
    for ep in range(1, a.epochs + 1):
        random.shuffle(train)
        tot = 0.0
        for i in range(0, len(train), a.batch):
            b = train[i:i + a.batch]
            q = embed([r["x"] for r in b])
            c = embed(cls_text)
            logits = (q @ c.T) / a.tau
            mask = torch.stack([allowed[r["kind"]] for r in b])
            logits = logits.masked_fill(~mask, -1e4)
            loss = F.cross_entropy(logits, torch.tensor([r["y"] for r in b], device=dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss.detach())
        m = evaluate()
        history.append({"epoch": ep, "train_loss": tot / (len(train) // a.batch + 1), **m})
        print(json.dumps(history[-1]), flush=True)
        if m["val_unseen_acc"] > best:
            best, bad = m["val_unseen_acc"], 0
            model.save(str(out))
        else:
            bad += 1
            if bad >= 2:
                break
    (out / "train_history.json").write_text(json.dumps(history, indent=2))
    print("ENCODER_DONE", json.dumps({"best_val_unseen_acc": best}), flush=True)


if __name__ == "__main__":
    main()
