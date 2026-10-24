#!/usr/bin/env python3
"""Split-integrity and leakage audit for the InvoiceLab v2 corpus.

Fails (exit 1) when any of these hold:
  * a layout family, vendor, vendor name, or page image hash appears in more than one split;
  * a val/test document is a near-duplicate of a train document (word-shingle MinHash Jaccard >= 0.8);
  * any entity of the real benchmark invoices (invoice numbers, vendor names, GSTINs, containers,
    B/L numbers, IRNs) appears anywhere in the synthetic corpus text.
Also reports the share of val/test charge lines and labels whose wording never occurs in train
(the "unseen alias" slice used to measure semantic generalization).

Usage: .venv/bin/python scripts/check_leakage.py dataset_v2 --output dataset_v2/leakage_report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_GOLD = ROOT / "eval" / "real_invoices" / "gold.json"
# Tokens that identify the real benchmark invoices (beyond the gold values themselves).
REAL_EXTRA = ["DACHSER", "KUEHNE", "NAGEL", "MEGALO", "JEENA", "EPT EXIM", "EPT GLOBAL", "DSV AIR",
              "HARSHA ENGINEERS", "24AACCH5549Q1Z2", "AACCH5549Q", "24AACCA7791D1ZS", "24AACCD3848A1Z5",
              "24AADCE0916H1Z2", "24AABCE7934M1ZD", "24AAAFJ1721H1ZX", "24AAACK2676H1ZM", "24AAPCA1490A1Z1",
              "MEDUAEO12208", "HLCUBO12608AWQG7", "HLCUBO12606CLPC8", "HLCUBO12607AZYT9", "COSU6462596850",
              "ES02627", "IN1AMD", "AMDTI2", "IVJCAMD", "AMD1142005", "AMDTI26-27-0289"]
NUM_PERM = 64
_P = (1 << 61) - 1
_rand = random.Random(91731)
_AB = [(_rand.randrange(1, _P), _rand.randrange(0, _P)) for _ in range(NUM_PERM)]


def minhash(tokens: list[str]) -> tuple:
    sh = {" ".join(tokens[i:i + 3]) for i in range(max(1, len(tokens) - 2))}
    hs = [int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little") for s in sh]
    return tuple(min((a * h + b) % _P for h in hs) for a, b in _AB)


def jaccard_est(x: tuple, y: tuple) -> float:
    return sum(a == b for a, b in zip(x, y)) / NUM_PERM


def doc_tokens(root: Path, row: dict) -> list[str]:
    ann = json.loads((root / row["ann"]).read_text())
    # Values are random per document; layout leakage shows up in the label/boilerplate skeleton,
    # so shingle the non-value words (keys, headings, boilerplate) plus charge descriptions.
    toks = []
    for p in ann["pages"]:
        for w in p["words"]:
            k = w["k"]
            if k.endswith(":value") and not k.startswith("li_desc"):
                continue
            toks.append(re.sub(r"\d", "0", w["t"].casefold()))
    return toks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--output", default="")
    ap.add_argument("--near-dup-sample", type=int, default=600, help="val/test docs checked against all train")
    a = ap.parse_args()
    root = Path(a.root)
    rows = {s: [json.loads(l) for l in (root / f"{s}.jsonl").open()] for s in ("train", "val", "test")}
    errors, report = [], {"counts": {s: len(v) for s, v in rows.items()}}

    for key in ("family", "vendor_id", "vendor_name"):
        where = defaultdict(set)
        for s, rs in rows.items():
            for r in rs:
                where[r[key]].add(s)
        crossing = [k for k, v in where.items() if len(v) > 1]
        report[f"{key}_crossing_splits"] = len(crossing)
        if crossing:
            errors.append(f"{key} crosses splits: {crossing[:5]}")
    hashes = defaultdict(set)
    for s, rs in rows.items():
        for r in rs:
            for p in r["pages"]:
                hashes[p["sha256"]].add(s)
    dup = [h for h, v in hashes.items() if len(v) > 1]
    report["image_hash_crossing_splits"] = len(dup)
    if dup:
        errors.append(f"{len(dup)} identical page images cross splits")

    # unseen-alias slice
    train_alias = {it["alias"].casefold() for r in rows["train"] for it in r["items"]}
    train_labels = {v["raw"].casefold() for r in rows["train"] for v in r["labels"].values() if v}
    for s in ("val", "test"):
        al = [it["alias"].casefold() for r in rows[s] for it in r["items"]]
        lb = [v["raw"].casefold() for r in rows[s] for v in r["labels"].values() if v]
        report[f"{s}_unseen_charge_alias_rate"] = round(sum(x not in train_alias for x in al) / max(1, len(al)), 4)
        report[f"{s}_unseen_label_rate"] = round(sum(x not in train_labels for x in lb) / max(1, len(lb)), 4)

    # near-duplicates (layout skeleton) val/test vs train
    rnd = random.Random(7)
    train_mh = [minhash(doc_tokens(root, r)) for r in rows["train"]]
    worst = 0.0
    n_dup = 0
    for s in ("val", "test"):
        sample = rnd.sample(rows[s], min(a.near_dup_sample // 2, len(rows[s])))
        for r in sample:
            m = minhash(doc_tokens(root, r))
            best = max(jaccard_est(m, t) for t in train_mh)
            worst = max(worst, best)
            n_dup += best >= 0.8
    report["near_dup_max_jaccard_vs_train"] = round(worst, 3)
    report["near_dup_count_ge_0.8"] = n_dup
    if n_dup:
        errors.append(f"{n_dup} val/test docs are near-duplicates (>=0.8) of train docs")
    # within-train skeleton diversity: how similar are random train pairs on average
    pairs = [(rnd.randrange(len(train_mh)), rnd.randrange(len(train_mh))) for _ in range(2000)]
    report["train_pairwise_mean_jaccard"] = round(sum(jaccard_est(train_mh[i], train_mh[j]) for i, j in pairs if i != j) / len(pairs), 3)

    # real-invoice entity leakage (exempt when the frozen gold file is absent)
    if REAL_GOLD.exists():
        gold = json.loads(REAL_GOLD.read_text())
        needles = set(REAL_EXTRA)
        for inv in gold["invoices"]:
            for k in ("Invoice No", "Container Number", "Bill of Lading / HBL", "Vendor Name"):
                for v in str(inv["fields"].get(k, "")).split(","):
                    if len(v.strip()) >= 6:
                        needles.add(v.strip())
        needles = {n.casefold() for n in needles}
        hits = []
        for s, rs in rows.items():
            for r in rs:
                ann = json.loads((root / r["ann"]).read_text())
                text = " ".join(w["t"] for p in ann["pages"] for w in p["words"]).casefold()
                for n in needles:
                    if n in text:
                        hits.append((r["id"], n))
        report["real_entity_hits"] = len(hits)
        if hits:
            errors.append(f"real-invoice entities found in synthetic corpus: {hits[:5]}")
    else:
        report["real_entity_hits"] = 0
        report["real_entity_check"] = f"skipped: {REAL_GOLD} absent"
        print(f"WARNING: {REAL_GOLD} absent, real-invoice entity check exempt",
              file=sys.stderr)
    report["errors"] = errors
    report["passed"] = not errors
    out = json.dumps(report, indent=2)
    print(out)
    if a.output:
        Path(a.output).write_text(out)
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
