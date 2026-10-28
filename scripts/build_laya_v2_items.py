#!/usr/bin/env python3
"""LAYA (v2) decisions from the semantic items: state = engine model input, gold = one of 34 classes.

LAYA packs every option into a 192-token head, so criteria are the short class names (the
meaning is learned from the decisions). Frequent surface forms are capped per (alias, class)
so common wordings do not dominate; val keeps its unseen-alias slice for reporting.

  .venv/bin/python scripts/build_laya_v2_items.py --sem work/sem_v2 --out work/laya_v2
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_extractor.semantic import CLASSES, model_input  # noqa: E402

INSTRUCTIONS = "Which canonical invoice field does this printed label or charge mean?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem", default="work/sem_v2")
    ap.add_argument("--out", default="work/laya_v2")
    ap.add_argument("--cap", type=int, default=40, help="max decisions per (alias, class) in train")
    ap.add_argument("--val-n", type=int, default=6000)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(91731)
    crit = {"instructions": INSTRUCTIONS, "criteria": {c: c for c in CLASSES}}
    (out / "criteria.json").write_text(json.dumps(crit, indent=1))
    for split in ("train", "val", "test"):
        rows = [json.loads(l) for l in (Path(a.sem) / f"{split}.jsonl").open()]
        rnd.shuffle(rows)
        seen, cnt, keep = set(), Counter(), []
        for r in rows:
            s = model_input(r["kind"], r["text"], r["context"])
            if (s, r["field"]) in seen:
                continue
            seen.add((s, r["field"]))
            key = (r["alias"].casefold(), r["field"])
            if split == "train" and cnt[key] >= a.cap:
                continue
            cnt[key] += 1
            keep.append({"state": s, "gold": r["field"], "unseen": r["bucket"] in ("val", "test"),
                         "source_id": r["doc_id"]})
        if split != "train":
            keep = keep[:a.val_n]
        with (out / f"{split}.jsonl").open("w") as f:
            for k in keep:
                f.write(json.dumps(k, ensure_ascii=False) + "\n")
        print(split, len(keep), "unseen", sum(k["unseen"] for k in keep),
              "classes", len({k["gold"] for k in keep}))


if __name__ == "__main__":
    main()
