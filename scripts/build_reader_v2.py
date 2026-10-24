#!/usr/bin/env python3
"""Reader (Qwen3-VL-8B-Instruct) fine-tuning examples: line-numbered layout text -> cited JSON.

Inputs are produced by the PRODUCTION ingest code, never from ground-truth text:
  digital : text-layer words of the Chromium-printed PDFs (layout.pdf_units)
  scan    : PP-OCRv6 output on the scan-degraded copies (ocr_results.jsonl from run_ppocr_batch.py)
Field roles reach the input units through measured geometry (DOM word boxes; exact for PDFs,
pushed through the scan homography for scans), so every target value knows its line id(s).

Evidence-consistent targets: a value is only kept when it is actually readable in the input
(exact, or a close OCR reading that is then copied verbatim); unreadable values become ""/null
and unreadable charge rows are dropped. The model is never trained to output text it cannot see.

Usage:
  .venv/bin/python scripts/build_reader_v2.py dataset_v2 --ocr work/ocr/ocr_results.jsonl --out work/reader_v2
Writes {train,val,test}.jsonl with {"messages": [...], "meta": {...}} rows.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_extractor.layout import Unit, ocr_units, pdf_units, serialize  # noqa: E402
from invoice_extractor.reader import SYSTEM_PROMPT, target_json, user_message  # noqa: E402
from invoice_extractor.validation import _norm_num  # noqa: E402

PT_PER_DEVICE_PX = 0.375  # DOM boxes are CSS px x2; Chromium prints 1 CSS px = 0.75 pt
NUM = re.compile(r"-?\d[\d,]*\.?\d*")
FIELD_CODE = {"invoice_no": "invoice_number", "invoice_date": "invoice_date", "vendor_name": "vendor_name",
              "mawb_no": "mawb_number", "hawb_no": "hawb_number", "master_bl_no": "master_bl",
              "house_bl_no": "bill_of_lading_hbl", "gross_weight": "weight", "chargeable_weight": "chargeable_weight"}


def an(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").casefold())


def tags_of(w: dict) -> list[tuple[str, int]]:
    out = [(w["k"], w["li"])] if w.get("k") else []
    if w.get("k2"):
        out.append((w["k2"], w["li"]))
    return out


def attach_pdf_tags(units: list[Unit], words: list[dict]) -> None:
    by_text = defaultdict(list)
    for w in words:
        by_text[w["t"]].append(w)
    for u in units:
        ux, uy = (u.box[0] + u.box[2]) / 2, (u.box[1] + u.box[3]) / 2
        best, bd = None, 3.0
        for w in by_text.get(u.text, []):
            d = max(abs((w["b"][0] + w["b"][2]) / 2 * PT_PER_DEVICE_PX - ux),
                    abs((w["b"][1] + w["b"][3]) / 2 * PT_PER_DEVICE_PX - uy))
            if d < bd:
                best, bd = w, d
        if best:
            u.tags = tags_of(best)
            continue
        # fallback: the PDF split a DOM word (hyphen wrap inside a narrow cell); match by geometry
        for w in words:
            x0, y0, x1, y1 = (v * PT_PER_DEVICE_PX for v in w["b"])
            if x0 - 1 <= ux <= x1 + 1 and y0 - 1 <= uy <= y1 + 1 and u.text.strip("-") and u.text.strip("-") in w["t"]:
                u.tags = tags_of(w)
                break


def attach_scan_tags(units: list[Unit], words: list[dict]) -> None:
    for u in units:
        x0, y0, x1, y1 = u.box[0] - 3, u.box[1] - 3, u.box[2] + 3, u.box[3] + 3
        tg = []
        for w in words:
            cx, cy = (w["b"][0] + w["b"][2]) / 2, (w["b"][1] + w["b"][3]) / 2
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                tg += tags_of(w)
        u.tags = tg


def lines_with(index: dict, pred) -> list[str]:
    return [lid for lid, ln in index.items() if any(pred(k, li) for u in ln.units for k, li in u.tags)]


def read_value(value: str, line_texts: list[str]) -> str | None:
    """Exact (alnum-normalized) -> the true value; close OCR reading -> that reading; else None."""
    if not value:
        return None
    v = an(value)
    joined = [" ".join(line_texts[i:i + 2]) for i in range(len(line_texts))]   # values wrapped onto 2 lines
    for t in list(line_texts) + joined:
        if v and v in an(t):
            return value
    best, score = None, 0.0
    toks_v = len(value.split())
    for t in line_texts:
        toks = t.split()
        for n in {max(1, toks_v - 1), toks_v, toks_v + 1}:
            for i in range(0, max(1, len(toks) - n + 1)):
                cand = " ".join(toks[i:i + n])
                s = SequenceMatcher(None, an(cand), v).ratio()
                if s > score:
                    best, score = cand, s
    return best if best and score >= 0.85 else None


def read_number(value: float, line_texts: list[str]) -> float | None:
    want = _norm_num(f"{value:.2f}")
    nums = [n for t in line_texts for n in NUM.findall(t)]
    for n in nums:
        if _norm_num(n) == want:
            return round(float(value), 2)
    wd = re.sub(r"\D", "", f"{value:.2f}")
    for n in nums:   # OCR misread of the same amount (one digit / separator off): copy the reading
        nd = re.sub(r"\D", "", n)
        if len(nd) == len(wd) and sum(a != b for a, b in zip(nd, wd)) == 1 and "." in n:
            try:
                return round(float(n.replace(",", "")), 2)
            except ValueError:
                return None
    return None


def build_target(row: dict, index: dict) -> tuple[dict, dict]:
    rt = row["reader_target"]
    text_of = {lid: ln.text for lid, ln in index.items()}
    t = {"transport_mode": rt["transport_mode"], "destination_country": rt["destination_country"], "evidence": {}}
    stats = Counter()
    for key, code in FIELD_CODE.items():
        lids = lines_with(index, lambda k, li, c=code: k == f"{c}:value")
        val = read_value(rt[key], [text_of[x] for x in lids]) if rt[key] else None
        t[key] = val or ""
        if val:
            t["evidence"][key] = lids
        stats["fields_total"] += bool(rt[key])
        stats["fields_kept"] += bool(val)
    conts, clids = [], []
    for c in rt["container_numbers"]:
        lids = [x for x in lines_with(index, lambda k, li: k == "container_number:value")
                if read_value(c, [text_of[x]])]
        v = read_value(c, [text_of[x] for x in lids]) if lids else None
        if v:
            conts.append(v)
            clids += [x for x in lids if x not in clids]
    t["container_numbers"] = conts
    if conts:
        t["evidence"]["container_numbers"] = clids
    fx_lids = lines_with(index, lambda k, li: k == "exchange_rate:value")
    t["exchange_rates"] = []
    for r in rt["exchange_rates"]:
        hit = [x for x in fx_lids if any(_norm_num(n) == _norm_num(str(r["rate"])) for n in NUM.findall(text_of[x]))]
        if hit:
            t["exchange_rates"].append({"currency": r["currency"], "rate": r["rate"], "lines": hit[:2]})
    t["line_items"] = []
    for i, it in enumerate(rt["line_items"]):
        lids = lines_with(index, lambda k, li, i=i: li == i and k in ("li_desc:value", "li_amount:value"))
        texts = [text_of[x] for x in lids]
        amt = read_number(it["amount_inr"], texts)
        stats["items_total"] += 1
        if amt is None:
            continue
        desc = read_value(it["description"], texts) or " ".join(
            u.text for x in lids for u in index[x].units if any(k == "li_desc:value" for k, _ in u.tags)).strip()
        if not desc:
            continue
        t["line_items"].append({"description": desc, "amount_inr": amt, "category": it["category"], "lines": lids})
        stats["items_kept"] += 1
    for key, code, val in (("subtotal_before_tax", "subtotal", rt["subtotal_before_tax"]),
                           ("total_invoice_amount", "total", rt["total_invoice_amount"])):
        lids = lines_with(index, lambda k, li, c=code: k == f"{c}:value")
        got = read_number(val, [text_of[x] for x in lids]) if (val is not None and lids) else None
        t[key] = got
        if got is not None:
            t["evidence"][key] = lids
    return t, stats


def example(row: dict, pages: list[tuple[int, list[Unit]]], source: str) -> tuple[dict, Counter]:
    text, index = serialize(pages)
    target, st = build_target(row, index)
    msg = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message(text)},
           {"role": "assistant", "content": target_json(target)}]
    return {"messages": msg, "meta": {"id": row["id"], "split": row["split"], "source": source,
                                      "family": row["family"], "chars": len(text)}}, st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--ocr", default="")
    ap.add_argument("--out", default="work/reader_v2")
    ap.add_argument("--max-chars", type=int, default=26000)
    a = ap.parse_args()
    root, out = Path(a.root), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = {json.loads(l)["id"]: json.loads(l) for s in ("train", "val", "test") for l in (root / f"{s}.jsonl").open()}
    writers = {s: (out / f"{s}.jsonl").open("w") for s in ("train", "val", "test")}
    stats = defaultdict(Counter)
    # digital (text layer); optional so scan-only builds can run where the PDFs are not present
    pdf_index = root / "pdf_index.jsonl"
    for line in (pdf_index.open() if pdf_index.exists() else []):
        e = json.loads(line)
        row = rows[e["id"]]
        ann = json.loads((root / row["ann"]).read_text())
        pages = []
        for (pno, units, _), pg in zip(pdf_units(str(root / e["pdf"])), ann["pages"]):
            attach_pdf_tags(units, pg["words"])
            pages.append((pno, units))
        ex, st = example(row, pages, "digital")
        if ex["meta"]["chars"] <= a.max_chars:
            writers[row["split"]].write(json.dumps(ex, ensure_ascii=False) + "\n")
            stats[row["split"] + "/digital"].update(st)
            stats[row["split"] + "/digital"]["examples"] += 1
    # scans (PP-OCRv6 output)
    if a.ocr:
        ocr = {}
        for line in open(a.ocr):
            r = json.loads(line)
            if "error" not in r:
                ocr[r["id"]] = r
        for split in ("train", "val", "test"):
            for line in (root / f"scans_{split}.jsonl").open():
                s = json.loads(line)
                row = rows[s["id"]]
                sann = json.loads((root / s["ann"]).read_text())
                pages, ok = [], True
                for j, pg in enumerate(sann["pages"]):
                    res = ocr.get(f"{s['scan_id']}_p{j + 1}")
                    if res is None:
                        ok = False
                        break
                    units = ocr_units(res, j + 1)
                    attach_scan_tags(units, pg["words"])
                    pages.append((j + 1, units))
                if not ok:
                    stats[split + "/scan"]["missing_ocr"] += 1
                    continue
                ex, st = example(row, pages, "scan")
                ex["meta"]["scan_id"] = s["scan_id"]
                ex["meta"]["scan_kind"] = s["degradation"]["kind"]
                if ex["meta"]["chars"] <= a.max_chars:
                    writers[split].write(json.dumps(ex, ensure_ascii=False) + "\n")
                    stats[split + "/scan"].update(st)
                    stats[split + "/scan"]["examples"] += 1
    for w in writers.values():
        w.close()
    summary = {k: dict(v) for k, v in stats.items()}
    for k, v in summary.items():
        if v.get("fields_total"):
            v["field_readable_rate"] = round(v["fields_kept"] / v["fields_total"], 4)
        if v.get("items_total"):
            v["item_readable_rate"] = round(v["items_kept"] / v["items_total"], 4)
    (out / "stats.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
