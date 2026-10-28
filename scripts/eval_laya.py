#!/usr/bin/env python3
"""Evaluate a LAYA checkpoint on held-out Choice decisions.

Builds val decisions from the corpus (via build_laya_items logic) if the
items file is missing, runs Router.predict per decision, and reports
top-1 accuracy plus a confusion summary of the most-missed fields.

Usage:
  .venv/bin/python scripts/eval_laya.py --limit 200 --output work/laya_eval.json
  LAYA_MODEL_DIR=checkpoints/laya-invoice/laya-invoice-ft .venv/bin/python scripts/eval_laya.py
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.laya_client import criteria, ontology  # noqa: E402


def load_items(path: Path, split: str, root: str) -> list[dict]:
    if not path.exists():
        import subprocess

        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_laya_items.py"),
             "--split", split, "--output", str(path)],
            check=True,
        )
    return [json.loads(line) for line in path.open()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default="work/laya_val.jsonl")
    ap.add_argument("--split", default="val")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--output", default="work/laya_eval.json")
    a = ap.parse_args()

    from laya import Router
    import os

    model_dir = os.environ.get(
        "LAYA_MODEL_DIR",
        str(ROOT / "checkpoints" / "laya-invoice" / "laya-invoice-ft"),
    )
    onto = ontology()
    crit = criteria()
    instructions = "Which canonical invoice field does this document line describe?"
    items = load_items(Path(a.items), a.split, a.root)[:a.limit]

    router = Router(models={"english": model_dir})
    t0 = time.time()
    correct, total = 0, 0
    misses: Counter = Counter()
    per_field: Counter = Counter()
    per_field_ok: Counter = Counter()
    for it in items:
        try:
            r = router.predict(
                it["state"],
                {"field": {"type": "choice", "instructions": instructions, "criteria": crit}},
                model="english",
            )
            pred = r["answers"]["field"]["choice"]
        except Exception as exc:
            pred = f"ERROR:{type(exc).__name__}"
        total += 1
        per_field[it["gold"]] += 1
        if pred == it["gold"]:
            correct += 1
            per_field_ok[it["gold"]] += 1
        else:
            misses[(it["gold"], pred)] += 1
        if total % 25 == 0:
            print(f"  {total}/{len(items)} acc={correct / total:.3f}", flush=True)

    elapsed = time.time() - t0
    report = {
        "model_dir": model_dir,
        "items": a.items,
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "elapsed_s": round(elapsed, 1),
        "s_per_item": round(elapsed / total, 2) if total else 0.0,
        "worst_misses": [[g, p, c] for (g, p), c in misses.most_common(10)],
        "per_field_acc": {
            f: round(per_field_ok[f] / per_field[f], 3) for f in sorted(per_field)
        },
    }
    Path(a.output).write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("total", "correct", "accuracy", "elapsed_s", "s_per_item")}, indent=2))
    print("worst misses (gold -> pred x count):")
    for g, p, c in report["worst_misses"]:
        print(f"  {g} -> {p} x{c}")


if __name__ == "__main__":
    main()
