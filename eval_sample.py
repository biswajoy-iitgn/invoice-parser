"""Sample-folder eval: <5s/invoice and field accuracy vs ground truth.

Usage (from invoice_parsing/):
    .venv/bin/python eval_sample.py [--engine mineru] [--files DACHSER "EPT EXIM"]

Ground truth: Table_Parsing/sample/DACHSER_invoice.json (keys normalize onto
OUTPUT_FIELDS; numeric fields compare with tolerance, dates via validation
normalize_date, vendor/BL via case-insensitive containment).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "sample invoices"


def load_gt(sample_dir: Path) -> dict[str, dict]:
    """All *.json ground-truth files keyed by lowercased stem."""
    out = {}
    for p in sorted(sample_dir.glob("*.json")):
        try:
            out[p.stem.lower()] = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
    return out


def match_gt(pdf_stem: str, gt: dict[str, dict]) -> dict:
    """Exact stem match, else first GT whose leading token names this vendor."""
    if pdf_stem in gt:
        return gt[pdf_stem]
    for stem, fields in gt.items():
        head = stem.split("_")[0]
        if head and head in pdf_stem:
            return fields
    return {}

NUMERIC = {"Sea Freight", "Air Freight", "Terminal Handling Charges",
           "Air Waybill Charges", "BL Charges", "ENS Charges", "Seal Charges",
           "VGM", "EDI Fees", "DAP Charges", "Other Charges", "AMS CHARGES",
           "Handling Charges", "Pre Carriage Haulage", "Misc", "Transportation",
           "Warehouse charges Port", "Examination charges", "Fuel Charges",
           "Cargo Screening", "Agency Charges", "Shipping bill charges"}


def norm_num(v) -> float | None:
    try:
        return round(float(str(v).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def norm_date(v) -> str:
    try:
        from invoice_extractor.validation import normalize_date

        return normalize_date(str(v)) or str(v).strip()
    except Exception:
        return str(v).strip()


def compare(field: str, got, want) -> bool:
    got_s, want_s = str(got or "").strip(), str(want or "").strip()
    if want is None or want_s == "":
        return got_s == ""
    if field in NUMERIC:
        g, w = norm_num(got_s), norm_num(want_s)
        return g is not None and w is not None and abs(g - w) < 0.015
    if field == "Invoice Date":
        return norm_date(got_s) == norm_date(want_s)
    if field in ("Vendor Name", "Bill of Lading / HBL"):
        return want_s.lower() in got_s.lower() or got_s.lower() in want_s.lower()
    if field == "Ex Rate":
        if isinstance(want, dict):
            return all(str(v) in got_s for v in want.values() if v)
        return want_s.lower() in got_s.lower() or got_s.lower() in want_s.lower()
    if field == "WEIGHT":
        g_parts, w_parts = got_s.split(), str(want_s).split()
        g, w = norm_num(g_parts[0]) if g_parts else None, norm_num(w_parts[0]) if w_parts else None
        return g is not None and g == w
    return got_s == want_s


def run(engine: str, only: list[str], sample_dir: Path = SAMPLE) -> int:
    if engine == "mineru":
        from invoice_extractor import mineru_engine as eng
    elif engine == "paddle":
        from invoice_extractor import paddle_engine as eng
    elif engine == "docling":
        from invoice_extractor import docling_engine as eng
    else:
        raise SystemExit(f"unknown engine {engine}")
    files = sorted(glob.glob(str(sample_dir / "*.pdf")))
    if only:
        files = [f for f in files if any(o.lower() in os.path.basename(f).lower() for o in only)]
    if not files:
        print(f"ERROR: no sample PDFs under {sample_dir}", file=sys.stderr)
        return 2
    gt = load_gt(sample_dir)
    print(f"engine={engine} files={len(files)}")
    times, fails = [], 0
    for f in files:
        t0 = time.time()
        try:
            fields, audit = eng.extract(f)
            dt = time.time() - t0
        except Exception as exc:
            print(f"FAIL {os.path.basename(f)}: {type(exc).__name__}: {str(exc)[:150]}")
            fails += 1
            continue
        times.append(dt)
        filled = sum(1 for v in fields.values() if v)
        line = f"{dt:6.1f}s filled={filled:2d} route={audit.get('review_route'):12} {os.path.basename(f)}"
        want = match_gt(Path(f).stem.lower(), gt)
        if want:
            ok = tot = 0
            for k, v in want.items():
                if k not in fields:
                    continue
                tot += 1
                match = compare(k, fields[k], v)
                ok += match
                if not match:
                    line += f"\n    DIFF {k!r}: got={fields[k]!r} want={v!r}"
            if tot:
                line += f"\n    accuracy: {ok}/{tot} = {ok / tot * 100:.1f}%"
            else:
                line += "\n    accuracy: no comparable fields"
        print(line, flush=True)
    if times:
        print(f"\nwall-avg={sum(times) / len(times):.1f}s/invoice "
              f"pmax={max(times):.1f}s under5s={sum(1 for t in times if t < 5)}/{len(times)}")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="mineru")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--sample-dir", type=Path, default=SAMPLE)
    args = ap.parse_args()
    sys.exit(run(args.engine, args.files, args.sample_dir))
