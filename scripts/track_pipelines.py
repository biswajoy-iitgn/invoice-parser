#!/usr/bin/env python3
"""Multi-pipeline tracker: 50 varied invoices, full ML metrics (§1-24).

Pipelines:
  A  hunyuan ingest + ollama qwen3-vl:8b reader + LAYA   (conditions: orig/lr/sr)
  B  hunyuan ingest + MLX Qwen3-4B reader + LAYA          (condition: orig)
  C  docling-ocr ingest + ollama reader + LAYA           (condition: orig)

Paddle path excluded (needs CUDA; Mac-only per instruction).
LayoutLMv3 tracked separately as a classifier probe (scripts/track_layoutlmv3.py).

Outputs:
  work/track/pred_<pipe>_<cond>.jsonl, reports/field_metrics.{csv,json},
  reports/document_metrics.csv, reports/dashboard.json
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.extract import extract_invoice  # noqa: E402
from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402
from invoice_extractor.validation import (  # noqa: E402
    supported, supported_date, supported_number, supported_rate,
)
from invoice_extractor.schema import CHARGE_FIELDS  # noqa: E402

CRITICAL = ["Invoice No", "Invoice Date", "Vendor Name", "Container Number",
            "MAWB No", "HAWB No", "Bill of Lading / HBL", "WEIGHT"]


# ---------------- sampling ----------------

def sample_docs(root: Path, n: int = 50, seed: int = 7) -> list[dict]:
    import random

    recs = [json.loads(l) for l in open(root / "val.jsonl") if l.strip()]
    by_vendor: dict[str, list] = {}
    for r in recs:
        by_vendor.setdefault(r.get("vendor_id", "?"), []).append(r)
    rng = random.Random(seed)
    out = []
    per = max(1, n // max(1, len(by_vendor)))
    for v in sorted(by_vendor):
        members = sorted(by_vendor[v], key=lambda r: r["id"])
        sea = [r for r in members if not any(b["field"] in ("MAWB No", "HAWB No") for b in r["regions"])]
        air = [r for r in members if r not in sea]
        # Mode mix where available; otherwise fill from whichever pool exists
        # (val is sea-only, so air contributes nothing there).
        take = []
        half = (per + 1) // 2
        take += rng.sample(sea, min(len(sea), half))
        take += rng.sample(air, min(len(air), half))
        if len(take) < per:
            rest = [r for r in members if r not in take]
            take += rng.sample(rest, min(len(rest), per - len(take)))
        out += take[:per]
    # Top up from any vendor if some vendors were short.
    if len(out) < n:
        rest = [r for r in recs if r not in out]
        out += rng.sample(rest, min(len(rest), n - len(out)))
    return out[:n]


# ---------------- text metrics ----------------

def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def _strip_markup(s: str) -> str:
    """Remove OCR markup (LaTeX, HTML, markdown tables) for fair CER.

    Approved: formatting-only differences. Digits, words, and order kept.
    """
    s = re.sub(r"\$\\textbf\{([^}]*)\}\$", r"\1", s)  # hunyuan bold figures
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    s = re.sub(r"\\[a-z]+\{([^}]*)\}", r"\1", s)
    s = re.sub(r"</?(table|tr|td|caption)[^>]*>", " ", s)
    s = re.sub(r"[|_+=\-]{2,}", " ", s)  # md table rules
    s = s.replace("|", " ")
    s = re.sub(r"#{1,6}\s*", "", s)
    s = re.sub(r"\\", "", s)
    return _norm_ws(s)


def _lev_ops(a: str, b: str) -> tuple[int, int, int]:
    """Levenshtein (S, D, I) for a=GT -> b=pred at char or token level."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1,
                           dp[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    s = d = ins = 0
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1]:
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            s += 1
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            d += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return s, d, ins


def cer_wer(gt: str, pred: str) -> dict:
    s, d, i = _lev_ops(gt, pred)
    cer = (s + d + i) / max(1, len(gt))
    gs, ps = gt.split(), pred.split()
    s2, d2, i2 = _lev_ops(gs, ps)
    wer = (s2 + d2 + i2) / max(1, len(gs))
    gc, pc = Counter(gs), Counter(ps)
    inter = sum((gc & pc).values())
    return {"cer": cer, "wer": wer,
            "coverage": inter / max(1, sum(gc.values())),
            "tok_hall": (sum(pc.values()) - inter) / max(1, sum(pc.values())),
            "ned": (s + d + i) / max(1, max(len(gt), len(pred)))}


def reading_order(regions: list[dict], transcription: str) -> dict:
    """Pairwise ordering accuracy of GT region texts found in transcription."""
    texts = [b["text"] for b in sorted(regions, key=lambda b: (b["bbox"][1], b["bbox"][0]))]
    pos = []
    for t in texts:
        p = transcription.find(t)
        if p >= 0:
            pos.append(p)
    pairs = tot = 0
    for i in range(len(pos)):
        for j in range(i + 1, len(pos)):
            tot += 1
            pairs += pos[i] < pos[j]
    return {"pairwise": pairs / tot if tot else 0.0,
            "found_rate": len(pos) / max(1, len(texts))}


# ---------------- field metrics ----------------

def norm_eq(field: str, gold, pred) -> bool:
    g, p = _norm_ws(str(gold or "")).casefold(), _norm_ws(str(pred or "")).casefold()
    if field in CHARGE_FIELDS or field in ("Container Number",):
        g = g.replace(",", "")
        p = p.replace(",", "")
    return g == p and g != "" or (not g and not p)


def field_metrics(gold: dict, pred: dict) -> dict:
    out = {}
    for k in OUTPUT_FIELDS:
        y, x = _norm_ws(str(gold.get(k) or "")), _norm_ws(str(pred.get(k) or ""))
        eq = norm_eq(k, gold.get(k), pred.get(k))
        tp = bool(y and x and eq)
        fp = bool(x and (not y or not eq))
        fn = bool(y and not eq)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        out[k] = {"em": eq, "tp": tp, "fp": fp, "fn": fn,
                  "f1": 2 * p * r / (p + r) if p + r else 0.0,
                  "f2": 5 * p * r / (4 * p + r) if p + r else 0.0}
    return out


def supported_field(key: str, value: str, evidence: str) -> bool:
    if key == "Invoice Date":
        return supported_date(value, evidence)
    if key in CHARGE_FIELDS:
        return supported_number(value, evidence)
    if key == "Ex Rate":
        return supported_rate(value, evidence)
    return supported(value, evidence)


# ---------------- SR (SUPERSEDED, licensing: OmniSR removed in favor of
# SwinIR, see training/train_swinir.py; kept for reference) ----------------

_sr_model = None


def sr_enhance(lr_path: str, out_path: str) -> str:
    global _sr_model
    import torch
    from PIL import Image
    from torchvision.transforms.functional import to_pil_image, to_tensor

    from training.checkpointing import load_checkpoint
    from training.omnisr_factory import build_official_omnisr

    if _sr_model is None:
        _sr_model = build_official_omnisr(scale=2)
        load_checkpoint(str(ROOT / "checkpoints" / "omnisr" / "best.pt"), model=_sr_model)
        _sr_model.eval()
        if torch.backends.mps.is_available():
            _sr_model.to("mps")
    dev = next(_sr_model.parameters()).device
    img = Image.open(lr_path).convert("RGB")
    with torch.no_grad():
        out = _sr_model(to_tensor(img).unsqueeze(0).to(dev)).clamp(0, 1)
    to_pil_image(out[0].cpu()).save(out_path)
    return out_path


# ---------------- runner ----------------

PIPES = {
    "A": {"ingest": "hunyuan", "reader": "ollama", "conds": ("orig", "lr", "sr")},
    "B": {"ingest": "hunyuan", "reader": "mlx", "conds": ("orig",)},
    "C": {"ingest": "docling-ocr", "reader": "ollama", "conds": ("orig",)},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--work", default="work/track")
    ap.add_argument("--ids", default="", help="comma-separated doc ids (overrides sampling)")
    ap.add_argument("--pipes", default="A,B,C", help="subset of pipelines to run")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    root = ROOT / "dataset"
    if a.ids:
        want = set(a.ids.split(","))
        docs = [json.loads(l) for l in open(root / "val.jsonl") if json.loads(l)["id"] in want]
    else:
        docs = sample_docs(root, a.count)
    print(f"tracking {len(docs)} docs", flush=True)

    want_pipes = set(a.pipes.split(","))
    for pipe, cfg in PIPES.items():
        if pipe not in want_pipes:
            continue
        os.environ["INVOICE_INGEST"] = cfg["ingest"]
        os.environ["READER_BACKEND"] = cfg["reader"]
        for cond in cfg["conds"]:
            out_path = work / f"pred_{pipe}_{cond}.jsonl"
            if out_path.exists():
                print(f"[{pipe}/{cond}] exists, skip", flush=True)
                continue
            with open(out_path, "w") as f:
                for i, rec in enumerate(docs):
                    if cond == "orig":
                        img = str(root / rec["image"])
                    elif cond == "lr":
                        img = str(root / rec["sr_pair"]["lr_path"])
                    else:
                        lr = str(root / rec["sr_pair"]["lr_path"])
                        img = str(work / "sr" / f"{rec['id']}.png")
                        if not Path(img).exists():
                            Path(img).parent.mkdir(parents=True, exist_ok=True)
                            t0 = time.time()
                            sr_enhance(lr, img)
                            sr_s = time.time() - t0
                        else:
                            sr_s = 0.0
                    t0 = time.time()
                    try:
                        fields, audit = extract_invoice(img)
                        err = ""
                    except Exception as exc:
                        fields, audit = {}, {"error": str(exc)[:200], "timing_sec": {}}
                        err = str(exc)[:200]
                    dt = time.time() - t0
                    gt_text = "\n".join(
                        b["text"] for b in sorted(rec["regions"], key=lambda b: (b["bbox"][1], b["bbox"][0])))
                    ev = audit.get("evidence", "")
                    tr = audit.get("transcription", ev)
                    m = cer_wer(_strip_markup(gt_text), _strip_markup(tr))
                    m_raw = cer_wer(gt_text, tr)
                    ro = reading_order(rec["regions"], tr)
                    fm = field_metrics(rec["ground_truth"], fields)
                    inv_em = all(v["em"] for v in fm.values())
                    crit_em = all(fm[k]["em"] for k in CRITICAL)
                    unsup = sum(1 for k in OUTPUT_FIELDS
                                if str(fields.get(k) or "") and not supported_field(
                                    k, str(fields.get(k)), ev))
                    f.write(json.dumps({
                        "id": rec["id"], "pipe": pipe, "cond": cond,
                        "vendor": rec.get("vendor_id"), "error": err,
                        "fields": fields, "field_metrics": fm,
                        "invoice_em": inv_em, "critical_em": crit_em,
                        "cer": m["cer"], "wer": m["wer"],
                        "raw_cer": m_raw["cer"], "coverage": m["coverage"],
                        "tok_hall": m["tok_hall"], "ned": m["ned"],
                        "read_order": ro, "unsupported": unsup,
                        "nonempty": sum(1 for v in fields.values() if str(v or "")),
                        "timing": audit.get("timing_sec", {}), "total_s": round(dt, 2),
                        "sr_s": round(sr_s, 2) if cond == "sr" else 0.0,
                        "citation_coverage": audit.get("citation_coverage", 0),
                    }) + "\n")
                    print(f"[{pipe}/{cond}] {i + 1}/{len(docs)} {rec['id']} err={bool(err)} t={dt:.1f}s",
                          flush=True)
    print("TRACK-DONE", flush=True)


if __name__ == "__main__":
    main()
