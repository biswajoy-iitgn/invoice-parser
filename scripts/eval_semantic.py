#!/usr/bin/env python3
"""Evaluate the semantic engine layer by layer, and tune fusion weights on validation only.

Slices: synthetic val/test items split into SEEN vs UNSEEN wording (unseen = alias bucket held
out of training), plus the 47 charge lines of the real benchmark invoices (never tuned on).
Baselines: the previous fine-tuned LAYA checkpoint and the old deterministic ontology matcher.

  .venv/bin/python scripts/eval_semantic.py --sem work/sem_v2 --tune --out reports/semantic_eval.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from invoice_extractor import semantic as S  # noqa: E402


def load_items(path: Path, n: int, seed: int) -> list[dict]:
    rows = [json.loads(l) for l in path.open()]
    rnd = random.Random(seed)
    rnd.shuffle(rows)
    seen, out = set(), []
    for r in rows:
        key = (r["kind"], r["text"], r["context"], r["field"])
        if key in seen:
            continue
        seen.add(key)
        ctx = dict(p.split(": ", 1) for p in r["context"].split(" | ") if ": " in p)
        out.append({"kind": r["kind"], "text": r["text"], "field": r["field"], "unseen": r["bucket"] in ("val", "test"),
                    "mode": ctx.get("mode", ""), "region": ctx.get("destination", ""), "incoterm": ctx.get("incoterm", ""),
                    "value": ctx.get("value", "")})
        if len(out) >= n:
            break
    return out


def real_items() -> list[dict]:
    g = json.loads((ROOT / "eval" / "real_invoices" / "gold.json").read_text())
    out = []
    for inv in g["invoices"]:
        reg = S.region_of(inv.get("destination_country", ""))
        for it in inv["line_items"]:
            out.append({"kind": "charge", "text": it["description"], "field": it["category"],
                        "alt": it.get("alt_categories", []), "mode": inv.get("transport_mode", "sea"), "region": reg,
                        "unseen": True})
    return out


def layer_probs(eng: S.SemanticEngine, items: list[dict]) -> dict[str, np.ndarray]:
    """Per-layer class probability matrices (n x C), computed once so fusion can be re-weighted cheaply."""
    inputs = [S.model_input(it["kind"], it["text"], S.context_string(it["kind"], it.get("mode", ""), it.get("region", ""),
                                                                      it.get("incoterm", ""), it.get("value", "")))
              for it in items]
    C = len(S.CLASSES)
    out = {}
    if eng.encoder is not None:
        emb = np.zeros((len(items), C))
        for kind in ("label", "charge"):
            idx = [i for i, it in enumerate(items) if it["kind"] == kind]
            if idx:
                emb[idx] = eng.encoder.scores([inputs[i] for i in idx], eng.bank.items(kind), kind)
        out["embedding_scores"] = emb
    if eng.laya is not None:
        out["laya"] = eng.laya.probs(inputs)
    alias = np.zeros((len(items), C))
    lex = np.zeros((len(items), C))
    for i, it in enumerate(items):
        hit = eng.bank.lookup(it["kind"], it["text"])
        if hit and hit[0] in S.CLASSES:
            alias[i, S.CLASSES.index(hit[0])] = 1
        if it["kind"] == "charge":
            for c in S._lexicon(it["text"]):
                lex[i, S.CLASSES.index(c)] = 1.0 / max(1, len(S._lexicon(it["text"])))
    out["alias"], out["lexicon"] = alias, lex
    return out


def fuse(lp: dict, items: list[dict], w: dict) -> list[str]:
    C = len(S.CLASSES)
    z = np.zeros((len(items), C))
    if "embedding_scores" in lp and w.get("embedding", 0):
        s = lp["embedding_scores"] / w["embedding_temperature"]
        s = s - s.max(axis=1, keepdims=True)
        p = np.exp(s) / np.exp(s).sum(axis=1, keepdims=True)
        z += w["embedding"] * np.log(p + 1e-6)
    if "laya" in lp and w.get("laya", 0):
        z += w["laya"] * np.log(lp["laya"] + 1e-6)
    z += w["alias_bonus"] * lp["alias"] + w["lexicon"] * lp["lexicon"]
    preds = []
    for i, it in enumerate(items):
        allowed = S.LABEL_CLASSES if it["kind"] == "label" else S.CHARGE_CLASSES
        zi = np.where([c in allowed for c in S.CLASSES], z[i], -1e9)
        if not (lp["alias"][i].any() or lp["lexicon"][i].any() or ("laya" in lp and w.get("laya")) or
                ("embedding_scores" in lp and w.get("embedding"))):
            preds.append("Other Charges" if it["kind"] == "charge" else "NONE")
        else:
            preds.append(S.CLASSES[int(np.argmax(zi))])
    return preds


def acc(preds: list[str], items: list[dict], subset=None) -> float | None:
    idx = [i for i, it in enumerate(items) if subset is None or subset(it)]
    if not idx:
        return None
    ok = sum(preds[i] == items[i]["field"] or preds[i] in items[i].get("alt", []) for i in idx)
    return round(ok / len(idx), 4)


def report(name: str, preds: list[str], items: list[dict]) -> dict:
    return {"name": name, "all": acc(preds, items), "seen": acc(preds, items, lambda x: not x["unseen"]),
            "unseen": acc(preds, items, lambda x: x["unseen"]),
            "labels": acc(preds, items, lambda x: x["kind"] == "label"),
            "charges": acc(preds, items, lambda x: x["kind"] == "charge")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem", default="work/sem_v2")
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--out", default="reports/semantic_eval.json")
    a = ap.parse_args()
    eng = S.SemanticEngine()
    print("backends:", eng.backends, flush=True)
    sets = {"val": load_items(Path(a.sem) / "val.jsonl", a.n, 1), "test": load_items(Path(a.sem) / "test.jsonl", a.n, 2),
            "real": real_items()}
    lps = {k: layer_probs(eng, v) for k, v in sets.items()}
    w0 = S._weights()
    best_w = dict(w0)
    if a.tune:
        grid = itertools.product([0.0, 0.5, 1.0, 1.5], [0.0, 0.5, 1.0, 1.5], [0.0, 0.35, 0.7], [1.0, 3.0], [0.03, 0.05, 0.08])
        best = -1
        for e, l, x, ab, t in grid:
            w = {"embedding": e, "laya": l, "lexicon": x, "alias_bonus": ab, "embedding_temperature": t, "reader": w0.get("reader", 0.6)}
            if e == 0 and l == 0:
                continue
            sc = acc(fuse(lps["val"], sets["val"], w), sets["val"])   # validation only
            if sc > best:
                best, best_w = sc, w
        (ROOT / "configs" / "semantic_fusion.json").write_text(json.dumps(best_w, indent=2))
        print("tuned on val:", best_w, best, flush=True)
    rows = {}
    for split, items in sets.items():
        lp = lps[split]
        r = [report("fused (engine)", fuse(lp, items, best_w), items),
             report("alias bank only", fuse(lp, items, {**best_w, "embedding": 0, "laya": 0, "lexicon": 0}), items),
             report("lexicon only", fuse(lp, items, {**best_w, "embedding": 0, "laya": 0, "alias_bonus": 0, "lexicon": 1}), items)]
        if "embedding_scores" in lp:
            r.append(report("embedding only", fuse(lp, items, {**best_w, "laya": 0, "lexicon": 0, "alias_bonus": 0, "embedding": 1}), items))
        if "laya" in lp:
            r.append(report("LAYA v2 only", fuse(lp, items, {**best_w, "embedding": 0, "lexicon": 0, "alias_bonus": 0, "laya": 1}), items))
        rows[split] = r
    # baselines on the real lines: old fine-tuned LAYA + old deterministic matcher
    try:
        os.environ["LAYA_MODEL_DIR"] = str(ROOT / "checkpoints" / "laya-invoice" / "laya-invoice-ft")
        from invoice_extractor.laya_client import classify_deterministic, semantic_field

        ri = sets["real"]
        rows["real"].append(report("OLD LAYA (previous checkpoint)", [semantic_field(it["text"])[0] for it in ri], ri))
        rows["real"].append(report("OLD deterministic matcher", [classify_deterministic(it["text"])["field"] for it in ri], ri))
    except Exception as exc:
        print("baseline skipped:", exc)
    out = {"backends": eng.backends, "weights": best_w, "sizes": {k: len(v) for k, v in sets.items()}, "results": rows}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2))
    for split, r in rows.items():
        print(f"== {split} (n={len(sets[split])})")
        for x in r:
            print(f"  {x['name']:34s} all={x['all']}  seen={x['seen']}  unseen={x['unseen']}  labels={x['labels']}  charges={x['charges']}")


if __name__ == "__main__":
    main()
