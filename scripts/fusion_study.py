#!/usr/bin/env python3
"""Study the post-reader logic without re-running the reader.

  collect   run ingest + reader once per held-out document and cache (reader JSON, layout) on disk
  evaluate  re-run pipeline.assemble() on the cache and score it (seconds, not minutes), optionally with
            variants of the fusion policy, and list every wrong field with the votes behind it

  .venv/bin/python scripts/fusion_study.py collect dataset_v2 --split val --source digital --n 150
  .venv/bin/python scripts/fusion_study.py evaluate dataset_v2 --split val --source digital

Tune on `val`; `test` is only for the final confirmation (use --offset to take documents never looked at).
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "work" / "fusion_cache"


def documents(root: Path, split: str, source: str, n: int, offset: int, seed: int = 13) -> list[dict]:
    """Same order as eval_synth.py (seed 13), so --offset skips documents an earlier evaluation used."""
    if source == "digital":
        docs = [json.loads(l) for l in (root / "pdf_index.jsonl").open()]
        docs = [{"id": d["id"], "path": str(root / d["pdf"])} for d in docs if d["split"] == split]
    else:
        docs = [json.loads(l) for l in (root / f"scans_{split}.jsonl").open()]
        docs = [{"id": d["id"], "path": str(root / d["pdf"]), "kind": d["degradation"]["kind"]} for d in docs if d.get("pdf")]
    random.Random(seed).shuffle(docs)
    return docs[offset:offset + n]


def real_documents(source: str) -> list[dict]:
    """The real invoices (eval/real_invoices/gold.json): digital PDFs, or their office / phone scans."""
    gold = json.loads((ROOT / "eval" / "real_invoices" / "gold.json").read_text())
    docs = []
    for inv in gold["invoices"]:
        if source == "digital":
            docs.append({"id": inv["id"], "path": str(ROOT / "sample invoices" / inv["files"][0]), "gold": inv})
        else:
            for recipe in ("office", "phone"):
                tag = f"{inv['id']}__{recipe}"
                docs.append({"id": tag, "path": str(ROOT / "eval" / "real_invoices" / "scanned" / f"{tag}.pdf"),
                             "kind": recipe, "gold": inv})
    return docs


def collect(a) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from invoice_extractor.layout import serialize
    from invoice_extractor.pipeline import call_reader, load_pages

    out = CACHE / f"{a.split}_{a.source}"
    out.mkdir(parents=True, exist_ok=True)
    pool_docs = real_documents(a.source) if a.split == "real" else documents(Path(a.root), a.split, a.source, a.n, a.offset)
    docs = [d for d in pool_docs if not (out / f"{d['id']}.pkl").exists()]
    print(f"{len(docs)} documents to read -> {out}", flush=True)
    t0 = time.time()

    def one(d):
        try:
            pages = load_pages(d["path"])
            text, index = serialize([(p.number, p.units) for p in pages])
            raw = call_reader(text)
            tmp = out / f"{d['id']}.tmp"
            tmp.write_bytes(pickle.dumps({"id": d["id"], "path": d["path"], "kind": d.get("kind", "digital"),
                                          "raw": raw, "text": text, "index": index, "pages": pages}))
            tmp.rename(out / f"{d['id']}.pkl")
            return None
        except Exception as exc:
            return f"{d['id']}: {type(exc).__name__}: {exc}"[:200]

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for k, err in enumerate(pool.map(one, docs), 1):
            if err:
                print("ERROR", err, flush=True)
            if k % 10 == 0:
                print(f"{k}/{len(docs)}  {time.time() - t0:.0f}s", flush=True)
    print("done", flush=True)


def load_cache(split: str, source: str, ids: set[str] | None = None) -> list[dict]:
    d = CACHE / f"{split}_{source}"
    out = [pickle.loads(f.read_bytes()) for f in sorted(d.glob("*.pkl"))]
    return [x for x in out if ids is None or x["id"] in ids]


def memoize_models() -> None:
    """Keep the encoder / LAYA outputs on disk per input text, so a re-run with other fusion settings is instant."""
    import numpy as np

    from invoice_extractor.semantic import engine

    eng = engine()
    path = CACHE / "model_outputs.pkl"
    memo = pickle.loads(path.read_bytes()) if path.exists() else {"enc": {}, "laya": {}}
    dirty = [False]
    if eng.encoder is not None:
        raw_scores = eng.encoder.scores

        def scores(inputs, bank, kind):
            key = lambda s: (kind, len(bank), s)
            miss = [s for s in dict.fromkeys(inputs) if key(s) not in memo["enc"]]
            if miss:
                for s, row in zip(miss, raw_scores(miss, bank, kind)):
                    memo["enc"][key(s)] = row
                dirty[0] = True
            return np.stack([memo["enc"][key(s)] for s in inputs])

        eng.encoder.scores = scores
    if eng.laya is not None:
        raw_probs = eng.laya.probs

        def probs(inputs):
            miss = [s for s in dict.fromkeys(inputs) if s not in memo["laya"]]
            if miss:
                for s, row in zip(miss, raw_probs(miss)):
                    memo["laya"][s] = row
                dirty[0] = True
            return np.stack([memo["laya"][s] for s in inputs])

        eng.laya.probs = probs

    def save():
        if dirty[0]:
            path.write_bytes(pickle.dumps(memo))
            dirty[0] = False

    memoize_models.save = save


def score(cached: list[dict], rows: dict, verbose: bool = False) -> dict:
    """Run assemble() on every cached document; return the headline numbers and the error list."""
    import copy

    from invoice_extractor.pipeline import assemble
    from invoice_extractor.scoring import score_document

    exact = fields_ok = fields_n = 0
    cat_ok = cat_n = amt_found = 0
    auto = auto_ok = 0
    errors, by_field = [], Counter()
    for c in cached:
        row = rows[c["id"]]
        fields, audit = assemble(copy.deepcopy(c["raw"]), c["text"], c["index"], c["pages"])
        fs = score_document(row["ground_truth"], fields)
        ok = all(v["strict"] for v in fs.values())
        exact += ok
        fields_ok += sum(v["strict"] for v in fs.values())
        fields_n += len(fs)
        preds = audit.get("line_items", [])
        for g in row["items"]:
            m = next((p for p in preds if abs(float(p.get("amount_inr", -1)) - g["taxable"]) < 0.01), None)
            cat_n += 1
            amt_found += bool(m)
            good = bool(m and m.get("category") == g["category"] and "excluded" not in m)
            cat_ok += good
            if not good:
                errors.append({"id": c["id"], "kind": "category", "desc": g["desc"], "gold": g["category"],
                               "pred": m.get("category") if m else None, "reader": m.get("reader_category") if m else None,
                               "conf": m.get("category_confidence") if m else None,
                               "votes": (m.get("semantic") or {}).get("votes") if m else None,
                               "excluded": m.get("excluded") if m else "amount-missing", "bucket": g["alias_bucket"]})
        for f, v in fs.items():
            if not v["strict"]:
                by_field[f] += 1
                e = audit["fields_explained"].get(f, {})
                errors.append({"id": c["id"], "kind": "field", "field": f, "gold": v["gold"], "pred": v["pred"],
                               "basis": e.get("basis"), "raw": {k: c["raw"].get(k) for k in (
                                   "vendor_name", "mawb_no", "hawb_no", "house_bl_no", "master_bl_no", "gross_weight",
                                   "chargeable_weight", "invoice_no")} if f not in audit.get("line_items", []) else None})
        if audit["review_route"] == "AUTO_ACCEPT":
            auto += 1
            auto_ok += ok
    n = len(cached)
    return {"n": n, "invoice_exact": round(exact / max(1, n), 4), "field_accuracy": round(fields_ok / max(1, fields_n), 4),
            "category_accuracy": round(cat_ok / max(1, cat_n), 4), "amount_recall": round(amt_found / max(1, cat_n), 4),
            "charge_lines": cat_n, "auto_accept": round(auto / max(1, n), 4),
            "auto_accept_precision": round(auto_ok / auto, 4) if auto else None, "wrong_auto_accepted": auto - auto_ok,
            "errors": errors, "by_field": dict(by_field.most_common())}


def score_real(source: str) -> dict:
    """Same as score(), for the real invoices (lenient comparison: accepted alternatives count as right)."""
    import copy

    from invoice_extractor.pipeline import assemble
    from invoice_extractor.scoring import score_document, score_line_items

    gold = {d["id"]: d["gold"] for d in real_documents(source)}
    exact = fields_ok = fields_n = cat_ok = cat_n = auto = auto_ok = 0
    errors = []
    for c in load_cache("real", source):
        inv = gold[c["id"]]
        fields, audit = assemble(copy.deepcopy(c["raw"]), c["text"], c["index"], c["pages"])
        fs = score_document(inv["fields"], fields, inv.get("alternatives"))
        it = score_line_items(inv["line_items"], audit.get("line_items", []))
        ok = all(v["lenient"] for v in fs.values())
        exact += ok
        fields_ok += sum(v["lenient"] for v in fs.values())
        fields_n += len(fs)
        cat_ok += it["category_lenient"]
        cat_n += it["gold"]
        if audit["review_route"] == "AUTO_ACCEPT":
            auto += 1
            auto_ok += ok
        for f, v in fs.items():
            if not v["lenient"]:
                errors.append({"id": c["id"], "field": f, "gold": v["gold"], "pred": v["pred"],
                               "basis": audit["fields_explained"].get(f, {}).get("basis")})
    n = max(1, len(gold))
    return {"n": len(gold), "cached": len(load_cache("real", source)), "invoice_exact": f"{exact}/{len(gold)}",
            "field_accuracy": round(fields_ok / max(1, fields_n), 4), "category_accuracy": f"{cat_ok}/{cat_n}",
            "auto_accept": f"{auto}/{len(gold)}", "wrong_auto_accepted": auto - auto_ok, "errors": errors}


def evaluate(a) -> None:
    if a.split == "real":
        memoize_models()
        res = score_real(a.source)
        memoize_models.save()
        errs = res.pop("errors")
        print(json.dumps(res, indent=1))
        for e in errs:
            print(json.dumps(e, ensure_ascii=False)[:420])
        return
    root = Path(a.root)
    rows = {json.loads(l)["id"]: json.loads(l) for l in (root / f"{a.split}.jsonl").open()}
    cached = load_cache(a.split, a.source)
    if a.offset or a.n:
        keep = {d["id"] for d in documents(root, a.split, a.source, a.n or 10 ** 6, a.offset)}
        cached = [c for c in cached if c["id"] in keep]
    memoize_models()
    res = score(cached, rows)
    memoize_models.save()
    errs = res.pop("errors")
    print(json.dumps(res, indent=1))
    if a.errors:
        for e in errs:
            print(json.dumps(e, ensure_ascii=False)[:420])
    if a.out:
        Path(a.out).write_text(json.dumps({**res, "errors": errs}, indent=1, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "evaluate"):
        p = sub.add_parser(name)
        p.add_argument("root")
        p.add_argument("--split", default="val")
        p.add_argument("--source", choices=["digital", "scan"], default="digital")
        p.add_argument("--n", type=int, default=0 if name == "evaluate" else 150)
        p.add_argument("--offset", type=int, default=0)
        p.add_argument("--workers", type=int, default=2)
        p.add_argument("--errors", action="store_true")
        p.add_argument("--out")
    a = ap.parse_args()
    collect(a) if a.cmd == "collect" else evaluate(a)


if __name__ == "__main__":
    main()
