"""Benchmark the extractor on the synthetic dataset (dataset/manifest.jsonl + ground_truth/).

    python -m invoice_extractor.evaluate --split dev --backend layout --ocr tesseract --run dev_tess
    python -m invoice_extractor.evaluate --split test --run test_tess --report evaluation/results

Metric definitions (per field f, per document):
  TP  gold non-empty, prediction matches          FN  gold non-empty, prediction empty or wrong
  FP  prediction non-empty and (gold empty or wrong); a wrong value counts once as FP and once as FN
  Matching: IDs compare upper-case alphanumerics; vendor compares case/punctuation-folded text; dates ISO;
  money exact to the paisa (|d| <= 0.01); Ex Rate |d| <= 1e-4; weight |d| <= 0.01 kg; containers as sets.
Hallucination
  fabrication rate  = predictions on fields the document does not have / all non-empty predictions
  ungrounded rate   = non-empty predictions whose value is not printed anywhere on the page (a charge total
                      may be a sum of up to 3 printed amounts) / all non-empty predictions
mAP  DocILE-style: per field, predictions ranked by confidence; AP = mean precision at each correct
     prediction over all gold positives; mAP = mean over fields with >= 1 gold positive.
Schema obedience  output parses, exactly the 31 keys, JSON-Schema types, "" as the only empty marker.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import jsonschema

from .schema import CHARGE_FIELDS, OUTPUT_FIELDS, OUTPUT_JSON_SCHEMA
from .validators import find_dates, parse_number

ID_FIELDS = {"Invoice No", "MAWB No", "HAWB No", "Bill of Lading / HBL"}
MONEY_FIELDS = set(CHARGE_FIELDS)
EMPTY_MARKERS = {None, "N/A", "NA", "NOT_FOUND", "null", "None", "-"}


def _alnum(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _vendor(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(s).lower())).strip()


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    return parse_number(re.sub(r"[^\d.,\-]", "", str(v))) if str(v).strip() else None


def _is_marker(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() in EMPTY_MARKERS)


def is_empty(v) -> bool:
    return v == "" or _is_marker(v)


def match(field: str, gold, pred) -> bool:
    if field in ID_FIELDS:
        return _alnum(gold) == _alnum(pred)
    if field == "Container Number":
        return {_alnum(x) for x in str(gold).split(",")} == {_alnum(x) for x in str(pred).split(",")}
    if field == "Vendor Name":
        return _vendor(gold) == _vendor(pred)
    if field == "Invoice Date":
        return str(gold) == str(pred)
    g, p = _num(gold), _num(pred)
    if g is None or p is None:
        return False
    tol = 1e-4 if field == "Ex Rate" else 0.01
    return abs(g - p) <= tol + 1e-9


def _gold_numbers(text: str) -> list[float]:
    nums = set()
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        v = parse_number(tok)
        if v is not None:
            nums.add(round(v, 4))
    return sorted(nums)


def grounded(field: str, pred, gold_text: str, gold_alnum: str, gold_nums: list[float], gold_dates: set) -> bool:
    if field == "Invoice Date":
        return str(pred) in gold_dates
    if field == "Container Number":
        return all(_alnum(x) in gold_alnum for x in str(pred).split(","))
    if field in ID_FIELDS or field == "Vendor Name":
        return _alnum(pred) in gold_alnum
    p = _num(pred)
    if p is None:
        return False
    close = lambda a: abs(a - p) <= 0.011
    if any(close(n) for n in gold_nums):
        return True
    if field in MONEY_FIELDS:
        cand = [n for n in gold_nums if 0 < n < p + 0.02]
        if len(cand) <= 400:
            for k in (2, 3):
                if k == 3 and len(cand) > 120:
                    break
                for combo in itertools.combinations(cand, k):
                    if close(sum(combo)):
                        return True
    return False


def schema_check(obj) -> list[str]:
    errs = []
    if not isinstance(obj, dict):
        return ["not a JSON object"]
    keys = list(obj)
    missing = [k for k in OUTPUT_FIELDS if k not in obj]
    extra = [k for k in keys if k not in OUTPUT_FIELDS]
    if missing:
        errs.append("missing_keys")
    if extra:
        errs.append("extra_keys")
    if not missing and not extra and keys != OUTPUT_FIELDS:
        errs.append("key_order")
    if any(_is_marker(v) for v in obj.values()):
        errs.append("non_standard_empty_marker")
    try:
        jsonschema.validate(obj, OUTPUT_JSON_SCHEMA)
    except jsonschema.ValidationError:
        errs.append("type_or_format")
    return errs


# ---------------------------------------------------------------- running the extractor
def _run_one(args):
    path, out_dir, backend, ocr, model = args
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    from .extract import extract_invoice
    stem = Path(path).stem
    t0 = time.time()
    try:
        fields, audit = extract_invoice(path, backend=backend, ocr=ocr, model=model)
        err = None
    except Exception as exc:  # recorded as a failed document, scored as all-empty
        fields, audit, err = None, {}, f"{type(exc).__name__}: {exc}"
    audit["wall_sec"] = round(time.time() - t0, 2)
    audit["error"] = err
    with open(out_dir / f"{stem}.json", "w") as fh:
        json.dump(fields, fh, indent=2)
    with open(out_dir / f"{stem}.audit.json", "w") as fh:
        json.dump(audit, fh, indent=2, default=str)
    return stem, err


def run_predictions(rows, dataset: Path, out_dir: Path, backend: str, ocr: str, model: str, jobs: int, reuse: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [r for r in rows if not (reuse and (out_dir / f"{r['id']}.json").exists())]
    work = [(str(dataset / r["file"]), out_dir, backend, ocr, model) for r in todo]
    if not work:
        return
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(_run_one, w) for w in work]
        for i, f in enumerate(as_completed(futs), 1):
            stem, err = f.result()
            print(f"  [{i}/{len(work)}] {stem}" + (f"  ERROR {err}" if err else ""), file=sys.stderr, flush=True)


# ---------------------------------------------------------------- scoring
def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def _prepare(d: dict) -> None:
    """Per-field outcome of one document, computed once (grounding checks are the expensive part)."""
    if "_o" in d:
        return
    pred = d["pred"]
    d["_schema"] = schema_check(pred) if pred is not None else ["no_output"]
    pred = pred if isinstance(pred, dict) else {}
    galnum, gnums = _alnum(d["gold_text"]), _gold_numbers(d["gold_text"])
    gdates = {iso for _, iso in find_dates(d["gold_text"])}
    out = {}
    for f in OUTPUT_FIELDS:
        g, p = d["gold"].get(f, ""), pred.get(f, "")
        ge, pe = is_empty(g), is_empty(p)
        ok = (not ge) and (not pe) and match(f, g, p)
        gr = None if pe else grounded(f, p, d["gold_text"], galnum, gnums, gdates)
        out[f] = (ge, pe, ok, gr, d["conf"].get(f, 0.5))
    d["_o"] = out


def score_docs(docs: list[dict]) -> dict:
    """docs: [{id, gold{}, pred{} or None, conf{}, gold_text, meta{}, lines, audit}]"""
    per_field = {f: Counter() for f in OUTPUT_FIELDS}
    ranked = defaultdict(list)
    n_pred = n_fab = n_ungrounded = 0
    doc_exact = 0
    schema_viol = Counter()
    schema_ok = 0
    for d in docs:
        _prepare(d)
        for e in d["_schema"]:
            schema_viol[e] += 1
        schema_ok += not d["_schema"]
        all_ok = True
        for f, (ge, pe, ok, gr, cf) in d["_o"].items():
            c = per_field[f]
            if not pe:
                n_pred += 1
                n_ungrounded += not gr
                c["ungrounded"] += not gr
                ranked[f].append((cf, ok))
            if ge and pe:
                c["tn"] += 1
            elif ge and not pe:
                c["fp"] += 1
                c["fabricated"] += 1
                n_fab += 1
                all_ok = False
            elif not ge and pe:
                c["fn"] += 1
                c["missed"] += 1
                all_ok = False
            elif ok:
                c["tp"] += 1
            else:
                c["fp"] += 1
                c["fn"] += 1
                c["wrong"] += 1
                all_ok = False
        doc_exact += all_ok
    tp = sum(c["tp"] for c in per_field.values())
    fp = sum(c["fp"] for c in per_field.values())
    fn = sum(c["fn"] for c in per_field.values())
    tn = sum(c["tn"] for c in per_field.values())
    p, r, f1 = _prf(tp, fp, fn)
    fields_out, aps = {}, []
    for f, c in per_field.items():
        fp_, fr_, ff_ = _prf(c["tp"], c["fp"], c["fn"])
        pos = c["tp"] + c["fn"]
        ap = None
        if pos:
            lst = sorted(ranked[f], key=lambda x: -x[0])
            hits, s = 0, 0.0
            for k, (_, ok) in enumerate(lst, 1):
                if ok:
                    hits += 1
                    s += hits / k
            ap = s / pos
            aps.append(ap)
        fields_out[f] = {"precision": fp_, "recall": fr_, "f1": ff_, "ap": ap, "support": pos,
                         **{k: c[k] for k in ("tp", "fp", "fn", "tn", "wrong", "missed", "fabricated", "ungrounded")}}
    n = len(docs)
    return {
        "documents": n,
        "micro": {"precision": p, "recall": r, "f1": f1},
        "macro_f1": statistics.mean(v["f1"] for v in fields_out.values() if v["support"]),
        "field_accuracy_incl_empty": (tp + tn) / (n * len(OUTPUT_FIELDS)) if n else 0.0,
        "mAP": statistics.mean(aps) if aps else 0.0,
        "hallucination": {"fabrication_rate": n_fab / n_pred if n_pred else 0.0,
                          "ungrounded_rate": n_ungrounded / n_pred if n_pred else 0.0,
                          "predicted_values": n_pred},
        "wrong_value_rate": sum(c["wrong"] for c in per_field.values()) / max(1, tp + fn),
        "document_exact_match": doc_exact / n if n else 0.0,
        "schema": {"compliance_rate": schema_ok / n if n else 0.0, "violations": dict(schema_viol)},
        "fields": fields_out,
    }


def score_lines(docs: list[dict]) -> dict:
    """Charge lines: detection by amount, then category accuracy on matched lines (seen vs held-out wording)."""
    det = Counter()
    cat = defaultdict(Counter)
    confusions = Counter()
    for d in docs:
        pred_items = list((d["audit"] or {}).get("line_items") or [])
        used = set()
        for gl in d["lines"]:
            j = next((k for k, it in enumerate(pred_items) if k not in used and
                      abs(float(it["amount_inr"]) - gl["amount_inr"]) <= 0.01), None)
            key = "heldout" if gl["heldout_wording"] else "seen"
            if j is None:
                det["missed"] += 1
                continue
            used.add(j)
            det["matched"] += 1
            ok = pred_items[j]["category"] == gl["category"]
            cat[key]["correct" if ok else "wrong"] += 1
            if not ok:
                confusions[(gl["description"], gl["category"], pred_items[j]["category"])] += 1
        det["spurious"] += len(pred_items) - len(used)
    acc = {k: v["correct"] / max(1, v["correct"] + v["wrong"]) for k, v in cat.items()}
    total = det["matched"] + det["missed"]
    return {"line_recall": det["matched"] / max(1, total), "line_precision": det["matched"] / max(1, det["matched"] + det["spurious"]),
            "gold_lines": total, "spurious_lines": det["spurious"],
            "category_accuracy": acc, "category_counts": {k: dict(v) for k, v in cat.items()},
            "top_confusions": [{"description": a, "gold": b, "pred": c, "n": n} for (a, b, c), n in confusions.most_common(25)]}


def bootstrap(docs: list[dict], iters: int = 1000, seed: int = 7) -> dict:
    rng = random.Random(seed)
    stats = defaultdict(list)
    for _ in range(iters):
        sample = [docs[rng.randrange(len(docs))] for _ in docs]
        s = score_docs(sample)
        stats["micro_f1"].append(s["micro"]["f1"])
        stats["recall"].append(s["micro"]["recall"])
        stats["precision"].append(s["micro"]["precision"])
        stats["fabrication_rate"].append(s["hallucination"]["fabrication_rate"])
        stats["ungrounded_rate"].append(s["hallucination"]["ungrounded_rate"])
    ci = lambda xs: [round(sorted(xs)[int(0.025 * len(xs))], 4), round(sorted(xs)[int(0.975 * len(xs)) - 1], 4)]
    return {k: ci(v) for k, v in stats.items()}


def load_docs(rows, dataset: Path, pred_dir: Path) -> list[dict]:
    docs = []
    for r in rows:
        gt = json.loads((dataset / "ground_truth" / f"{r['id']}.json").read_text())
        pf, af = pred_dir / f"{r['id']}.json", pred_dir / f"{r['id']}.audit.json"
        pred = json.loads(pf.read_text()) if pf.exists() else None
        audit = json.loads(af.read_text()) if af.exists() else {}
        docs.append({"id": r["id"], "meta": r, "gold": gt["fields"], "lines": gt["lines"], "pred": pred, "audit": audit,
                     "conf": (audit or {}).get("field_confidence") or {},
                     "gold_text": (dataset / "gold_text" / f"{r['id']}.txt").read_text()})
    return docs


def strata(docs, key) -> dict:
    groups = defaultdict(list)
    for d in docs:
        groups[str(d["meta"][key])].append(d)
    out = {}
    for k, ds in sorted(groups.items()):
        s = score_docs(ds)
        out[k] = {"n": len(ds), "micro_f1": s["micro"]["f1"], "recall": s["micro"]["recall"],
                  "precision": s["micro"]["precision"], "fabrication_rate": s["hallucination"]["fabrication_rate"],
                  "doc_exact": s["document_exact_match"]}
    return out


def evaluate(rows, dataset: Path, pred_dir: Path, iters: int) -> dict:
    docs = load_docs(rows, dataset, pred_dir)
    res = score_docs(docs)
    res["lines"] = score_lines(docs)
    res["ci95"] = bootstrap(docs, iters) if iters else {}
    res["by_tier"] = strata(docs, "tier")
    res["by_format"] = strata(docs, "format")
    res["by_template"] = strata(docs, "template")
    res["by_mode"] = strata(docs, "mode")
    res["by_unseen_template"] = strata(docs, "unseen_template")
    lat = [d["audit"].get("wall_sec") for d in docs if d["audit"].get("wall_sec") is not None]
    res["latency_sec"] = {"mean": statistics.mean(lat), "median": statistics.median(lat),
                          "p95": sorted(lat)[max(0, int(0.95 * len(lat)) - 1)]} if lat else {}
    res["errors"] = [d["id"] for d in docs if d["audit"].get("error")]
    res["needs_review_rate"] = sum(bool(d["audit"].get("needs_review")) for d in docs) / max(1, len(docs))
    return res


def summary_text(res: dict, title: str) -> str:
    L = [f"== {title}  ({res['documents']} docs)",
         f"micro P/R/F1   {res['micro']['precision']:.3f} / {res['micro']['recall']:.3f} / {res['micro']['f1']:.3f}"
         + (f"   F1 95% CI {res['ci95'].get('micro_f1')}" if res.get("ci95") else ""),
         f"macro F1       {res['macro_f1']:.3f}    mAP {res['mAP']:.3f}    field acc (incl. empty) {res['field_accuracy_incl_empty']:.3f}",
         f"hallucination  fabricated {res['hallucination']['fabrication_rate']:.3%}   ungrounded {res['hallucination']['ungrounded_rate']:.3%}"
         f"   wrong-value {res['wrong_value_rate']:.3%}",
         f"doc exact      {res['document_exact_match']:.3f}    schema compliance {res['schema']['compliance_rate']:.3f} {res['schema']['violations']}",
         f"lines          recall {res['lines']['line_recall']:.3f}  precision {res['lines']['line_precision']:.3f}  "
         f"category acc {({k: round(v, 3) for k, v in res['lines']['category_accuracy'].items()})}",
         f"latency        {res.get('latency_sec')}   errors {res['errors']}", "", "per field (P / R / F1 / AP | tp fp fn | wrong missed fabricated ungrounded):"]
    for f, v in res["fields"].items():
        ap = f"{v['ap']:.3f}" if v["ap"] is not None else "  -  "
        L.append(f"  {f:28} {v['precision']:.3f} {v['recall']:.3f} {v['f1']:.3f} {ap} | {v['tp']:3} {v['fp']:3} {v['fn']:3} | "
                 f"{v['wrong']:3} {v['missed']:3} {v['fabricated']:3} {v['ungrounded']:3}")
    for key in ("by_tier", "by_format", "by_template", "by_mode", "by_unseen_template"):
        L.append(f"{key}: " + "  ".join(f"{k}(n={v['n']}) F1 {v['micro_f1']:.3f}" for k, v in res[key].items()))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="dataset")
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--backend", default="layout")
    ap.add_argument("--ocr", default="tesseract")
    ap.add_argument("--model", default="qwen3-vl:8b-instruct")
    ap.add_argument("--run", required=True, help="run name; predictions go to <runs-dir>/<run>/")
    ap.add_argument("--runs-dir", default="evaluation/runs")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--reuse", action="store_true", help="skip documents that already have predictions")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--ids", nargs="*", help="restrict to these document ids")
    a = ap.parse_args(argv)
    dataset = Path(a.dataset)
    rows = [json.loads(l) for l in (dataset / "manifest.jsonl").read_text().splitlines() if l.strip()]
    rows = [r for r in rows if a.split == "all" or r["split"] == a.split]
    if a.ids:
        rows = [r for r in rows if r["id"] in a.ids]
    run_dir = Path(a.runs_dir) / a.run
    if not a.score_only:
        run_predictions(rows, dataset, run_dir / "predictions", a.backend, a.ocr, a.model, a.jobs, a.reuse)
    res = evaluate(rows, dataset, run_dir / "predictions", a.bootstrap)
    res["config"] = {"split": a.split, "backend": a.backend, "ocr": a.ocr, "model": a.model if "llm" in a.backend else None}
    (run_dir / "metrics.json").write_text(json.dumps(res, indent=2))
    text = summary_text(res, f"{a.run}: split={a.split} backend={a.backend} ocr={a.ocr}")
    (run_dir / "summary.txt").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
