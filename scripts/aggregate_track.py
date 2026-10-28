#!/usr/bin/env python3
"""Aggregate tracker outputs into §15/16/20 reports.

Merges work/track + work/track_topup pred files, writes:
  reports/field_metrics.json / .csv, reports/document_metrics.csv,
  reports/dashboard.json, and prints the comparison table.
"""
import csv
import glob
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate import field_equal  # noqa: E402
from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402

CRITICAL = ["Invoice No", "Invoice Date", "Vendor Name", "Container Number",
            "MAWB No", "HAWB No", "Bill of Lading / HBL", "WEIGHT"]


def load(workdirs: list[str]) -> dict:
    rows = defaultdict(list)  # (pipe, cond) -> [rows]
    for wd in workdirs:
        for f in glob.glob(f"{wd}/pred_*.jsonl"):
            for line in open(f):
                r = json.loads(line)
                rows[(r["pipe"], r["cond"])].append(r)
    return rows


def pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


GT: dict[str, dict] = {}


def recompute(row: dict) -> None:
    """Recompute field metrics with approved normalization (dates, numbers).

    The tracker stored raw-string EM; this corrects it from stored fields.
    """
    gold = GT.get(row["id"], {})
    fm = {}
    for k in OUTPUT_FIELDS:
        y = str(gold.get(k) or "")
        x = str(row["fields"].get(k) or "")
        eq = field_equal(k, gold.get(k), row["fields"].get(k))
        tp = bool(y and x and eq)
        fp = bool(x and (not y or not eq))
        fn = bool(y and not eq)
        p = tp / (tp + fp) if tp + fp else 0.0
        r_ = tp / (tp + fn) if tp + fn else 0.0
        fm[k] = {"em": eq, "tp": tp, "fp": fp, "fn": fn,
                 "f1": 2 * p * r_ / (p + r_) if p + r_ else 0.0,
                 "f2": 5 * p * r_ / (4 * p + r_) if p + r_ else 0.0}
    row["field_metrics"] = fm
    row["invoice_em"] = all(v["em"] for v in fm.values())
    row["critical_em"] = all(fm[k]["em"] for k in CRITICAL)


def summarize(rows: list[dict]) -> dict:
    for r in rows:
        recompute(r)
    n = len(rows)
    errs = sum(bool(r["error"]) for r in rows)
    tot = [r["total_s"] for r in rows]
    lat = {}
    for stage in ("ingest", "llm", "cite"):
        vals = [r["timing"].get(stage, 0) for r in rows if isinstance(r.get("timing"), dict)]
        lat[stage] = {"mean": round(statistics.fmean(vals), 2) if vals else 0,
                      "p95": round(pct(vals, 0.95), 2)}
    ok = [r for r in rows if not r["error"]] or rows
    fm = {k: {"tp": 0, "fp": 0, "fn": 0, "em": 0, "n": 0} for k in OUTPUT_FIELDS}
    for r in rows:
        for k in OUTPUT_FIELDS:
            m = r["field_metrics"][k]
            fm[k]["tp"] += m["tp"]
            fm[k]["fp"] += m["fp"]
            fm[k]["fn"] += m["fn"]
            fm[k]["em"] += m["em"]
            fm[k]["n"] += 1
    for k, d in fm.items():
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else 0.0
        rec = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else 0.0
        d.update({"precision": round(p, 4), "recall": round(rec, 4),
                  "f1": round(2 * p * rec / (p + rec), 4) if p + rec else 0.0,
                  "f2": round(5 * p * rec / (4 * p + rec), 4) if p + rec else 0.0,
                  "em_rate": round(d["em"] / d["n"], 4)})
    micro_tp = sum(d["tp"] for d in fm.values())
    micro_fp = sum(d["fp"] for d in fm.values())
    micro_fn = sum(d["fn"] for d in fm.values())
    mp = micro_tp / (micro_tp + micro_fp) if micro_tp + micro_fp else 0.0
    mr = micro_tp / (micro_tp + micro_fn) if micro_tp + micro_fn else 0.0
    return {
        "n": n, "errors": errs, "error_rate": round(errs / n, 4),
        "n_transcribed": len(ok),
        "cer": {k: round(v, 4) for k, v in
                [("mean", statistics.fmean([r["cer"] for r in ok])),
                 ("median", statistics.median([r["cer"] for r in ok])),
                 ("p90", pct([r["cer"] for r in ok], 0.9)),
                 ("p95", pct([r["cer"] for r in ok], 0.95))]},
        "wer_mean": round(statistics.fmean([r["wer"] for r in ok]), 4),
        "coverage": round(statistics.fmean([r["coverage"] for r in ok]), 4),
        "tok_hall": round(statistics.fmean([r["tok_hall"] for r in ok]), 4),
        "ned": round(statistics.fmean([r["ned"] for r in ok]), 4),
        "read_order": round(statistics.fmean([r["read_order"]["pairwise"] for r in ok]), 4),
        "invoice_em": round(sum(r["invoice_em"] for r in rows) / n, 4),
        "critical_em": round(sum(r["critical_em"] for r in rows) / n, 4),
        "field_hall_rate": round(sum(r["unsupported"] for r in rows) / max(1, sum(r["nonempty"] for r in rows)), 4),
        "micro": {"precision": round(mp, 4), "recall": round(mr, 4),
                  "f1": round(2 * mp * mr / (mp + mr), 4) if mp + mr else 0.0,
                  "f2": round(5 * mp * mr / (4 * mp + mr), 4) if mp + mr else 0.0},
        "macro_f1": round(statistics.fmean([d["f1"] for d in fm.values()]), 4),
        "latency_s": lat,
        "total_s": {"mean": round(statistics.fmean(tot), 2), "p95": round(pct(tot, 0.95), 2)},
        "throughput_pages_sec": round(n / sum(tot), 3) if sum(tot) else 0,
        "citation_coverage": round(statistics.fmean([r.get("citation_coverage", 0) for r in rows]), 4),
        "field_metrics": fm,
    }


def main() -> None:
    global GT
    for line in open(ROOT / "dataset" / "val.jsonl"):
        rec = json.loads(line)
        GT[rec["id"]] = rec["ground_truth"]
    rows = load(["work/track", "work/track_topup"])
    dash = {"pipelines": {}, "config": {"model": "see per-pipe", "dataset": "val-50",
                                        "charts": "NOT APPLICABLE", "tables": "counts only (no GT)"}}
    doc_rows = []
    for (pipe, cond), rs in sorted(rows.items()):
        if not rs:
            continue
        s = summarize(rs)
        dash["pipelines"][f"{pipe}/{cond}"] = {k: v for k, v in s.items() if k != "field_metrics"}
        dash["pipelines"][f"{pipe}/{cond}"]["n"] = len(rs)
        for r in rs:
            doc_rows.append({"doc": r["id"], "pipe": pipe, "cond": cond, **{k: r[k] for k in
                              ("cer", "wer", "coverage", "tok_hall", "ned", "invoice_em",
                               "critical_em", "unsupported", "nonempty", "total_s",
                               "citation_coverage")},
                             "read_order": r["read_order"]["pairwise"], "error": r["error"]})
    # Field tables for the primary arm (A/orig).
    key = ("A", "orig")
    if key in rows:
        fm = summarize(rows[key])["field_metrics"]
        Path("reports/field_metrics.json").write_text(json.dumps(fm, indent=1))
        with open("reports/field_metrics.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["field", "support", "em_rate", "precision", "recall", "f1", "f2"])
            for k in OUTPUT_FIELDS:
                d = fm[k]
                w.writerow([k, d["n"], d["em_rate"], d["precision"], d["recall"], d["f1"], d["f2"]])
    with open("reports/document_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(doc_rows[0].keys()))
        w.writeheader()
        w.writerows(doc_rows)
    Path("reports/dashboard.json").write_text(json.dumps(dash, indent=2))
    print(f"{'arm':<8}{'n':>4}{'cer':>7}{'cov':>6}{'invEM':>7}{'critEM':>7}{'fHall':>6}{'p95s':>7}{'cit':>6}")
    for name, s in sorted(dash["pipelines"].items()):
        print(f"{name:<8}{s['n']:>4}{s['cer']['mean']:>7.3f}{s['coverage']:>6.3f}"
              f"{s['invoice_em']:>7.3f}{s['critical_em']:>7.3f}{s['field_hall_rate']:>6.3f}"
              f"{s['total_s']['p95']:>7.1f}{s['citation_coverage']:>6.3f}")
    print("wrote reports/field_metrics.{csv,json} reports/document_metrics.csv reports/dashboard.json")


if __name__ == "__main__":
    main()
