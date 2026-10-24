#!/usr/bin/env python3
"""Build the raster invoice corpus from InvoiceLab Studio.

 InvoiceLab (Table_Parsing/invoice-maker.html) renders authentic DOM invoices
 with measured field geometry. This script drives its agent API headlessly:

 Phase 1 (--raster): for each run, generate N freight invoices, rasterize all
   sheets via the built-in PNG exporter (24 visible at a time; the batch is
   rotated through the renderer), and save per-run ZIPs + record JSON.
 Phase 2 (--adapt): normalize records + PNGs into dataset/ records
   (ground_truth 31 keys, regions with pixel bboxes, split by variant holdout).

 Usage:
   python scripts/build_invoicelab_dataset.py --raster --runs 2 --per-run 24   # pilot
   python scripts/build_invoicelab_dataset.py --adapt work/ilab --output dataset_ilab
"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKER_DIR = Path("/Users/dtadmin/Desktop/DevelopmentProjects/Table_Parsing")
MAKER_URL = "http://127.0.0.1:8901/invoice-maker.html"

FIELDS = ["Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No",
          "Vendor Name", "Bill of Lading / HBL", "WEIGHT", "Sea Freight",
          "Air Freight", "Terminal Handling Charges", "Air Waybill Charges",
          "BL Charges", "ENS Charges", "Seal Charges", "VGM", "EDI Fees",
          "DAP Charges", "Other Charges", "AMS CHARGES", "Handling Charges",
          "Pre Carriage Haulage", "Ex Rate", "Misc", "Transportation",
          "Warehouse charges Port", "Examination charges", "Fuel Charges",
          "Cargo Screening", "Agency Charges", "Shipping bill charges"]

# InvoiceLab charge description -> our canonical field (mirrors generate_dataset.py).
CHARGE_MAP = {
    "Sea freight FCL": "Sea Freight",
    "THC port of loading": "Terminal Handling Charges",
    "B/L Fee FCL": "BL Charges",
    "Seal Fee FCL P.O.L": "Seal Charges",
    "ENS charge FCL": "ENS Charges",
    "Carrier charges origin": "Pre Carriage Haulage",
    "Haulage (delivery)": "Transportation",
    "AMS Security Fee Export": "AMS CHARGES",
    "Emergency Bunker Surcharge (EBS)": "Fuel Charges",
    "Agency Charges SB": "Agency Charges",
    "Transportation (local)": "Transportation",
    "Warehouse Charge CFS": "Warehouse charges Port",
    "VGM / Weighment": "VGM",
    "Documentation fee": "BL Charges",
}

VENDOR_SLUGS = {
    "DACHSER INDIA PVT. LTD.": "dachser",
    "DSV Air & Sea Pvt. Ltd.": "dsv",
    "KUEHNE + NAGEL Pvt. Ltd.": "kuehne_nagel",
    "EPT EXIM SERVICES PVT. LTD.": "ept_exim",
    "JEENA & CO.": "jeena",
    "MEGALO FREIGHT": "megalo",
}


def split_for_variant(variant: int) -> str:
    if 1 <= variant <= 30:
        return "train"
    if 31 <= variant <= 40:
        return "val"
    return "test"


# ---------------------------------------------------------------- raster ----
def run_raster(out: Path, runs: int, per_run: int, seed0: int, items: int) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8901", "--bind", "127.0.0.1"],
        cwd=str(MAKER_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="chrome")
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000},
                                      accept_downloads=True)
            page = ctx.new_page()
            page.goto(MAKER_URL, wait_until="load")
            page.evaluate("document.getElementById('o_stamp').checked=false")
            for run in range(runs):
                seed = seed0 + run
                t0 = time.time()
                page.evaluate(
                    """(o) => { window.InvoiceLab.done = false;
                       InvoiceLab.run(Object.assign({types:['freight'], items:8,
                         layout:150, miss:0, split:'train'}, o)); }""",
                    {"count": per_run, "seed": seed, "items": items})
                page.wait_for_function("window.InvoiceLab.done === true", timeout=120000)
                run_dir = out / f"run_{run:03d}_seed_{seed}"
                run_dir.mkdir(exist_ok=True)
                chunks = (per_run + 23) // 24
                for c in range(chunks):
                    if c:
                        page.evaluate(
                            """() => { state.batch.push(...state.batch.splice(0,24));
                               renderBatch(collectOpts()); }""")
                        page.wait_for_timeout(500)
                    meta = page.evaluate(
                        """() => state.batch.slice(0,24).map((r,i)=>({rec:r,
                           w:document.querySelectorAll('#stage .sheet')[i].offsetWidth,
                           h:document.querySelectorAll('#stage .sheet')[i].offsetHeight}))""")
                    with page.expect_download(timeout=300000) as dl:
                        page.evaluate("exportRasterPNG()")
                    download = dl.value
                    zp = run_dir / f"chunk_{c:02d}.zip"
                    download.save_as(str(zp))
                    (run_dir / f"chunk_{c:02d}.json").write_text(json.dumps(meta))
                    print(f"run {run + 1}/{runs} chunk {c + 1}/{chunks}: "
                          f"{len(meta)} sheets -> {zp.name}", flush=True)
                print(f"run {run + 1}/{runs} done in {time.time()-t0:.0f}s", flush=True)
            browser.close()
    finally:
        server.terminate()


# ----------------------------------------------------------------- adapt ----
def row_total(it: dict) -> float:
    # Mirror itemsRows(): tax=qty*rate*fx, t=tax*gst/100, total=tax+2*t.
    tax = it["qty"] * it["rate"] * it["fx"]
    return tax + 2 * (tax * it["gst"] / 100)


def money(curr: str, amount: float) -> str:
    amount = round(amount, 2)
    return str(amount) if curr == "INR" else f"{curr} {amount:,.2f}"


def ground_truth(rec: dict) -> dict:
    gt = {k: "" for k in FIELDS}
    doc, sh = rec["doc"], rec["shipment"]
    gt["Invoice No"] = str(doc["no"])
    gt["Invoice Date"] = doc["date"]
    gt["Container Number"] = sh["container"].split(" /")[0].strip()
    gt["Vendor Name"] = rec["seller"]["name"]
    gt["Bill of Lading / HBL"] = "" if sh["hbl"] == "NOT_FOUND" else sh["hbl"]
    m = re.search(r"(\d[\d,]*)\s*kg", sh["goods"], re.I)
    gt["WEIGHT"] = f"{m.group(1).replace(',', '')} KG" if m else ""
    for it in rec["items"]:
        canon = CHARGE_MAP.get(it["desc"])
        if canon and not gt[canon]:
            gt[canon] = money(it["curr"], row_total(it))
    fx = next((it for it in rec["items"] if it["curr"] != "INR"), None)
    gt["Ex Rate"] = f"{fx['curr']} {fx['fx']:g}" if fx else ""
    return gt


def region_texts(rec: dict) -> dict:
    """data-field key -> printed text (must match the rendered sheet)."""
    doc, sh, s = rec["doc"], rec["shipment"], rec["seller"]
    b = rec["buyer"]
    rows = []
    for i, it in enumerate(rec["items"]):
        tax = it["qty"] * it["rate"] * it["fx"]
        rows.append(f"{it['desc']} {it['hsn']} {it['qty']} {it['rate']} "
                    f"{it['curr']} {it['fx']} {tax:.2f} {it['gst']}% "
                    f"{row_total(it):.2f}")
    return {
        "doc_no": str(doc["no"]),
        "doc_date": doc["date"],
        "seller_name": s["name"],
        "seller_address": s["address"],
        "seller_gstin": s["gstin"],
        "irn": str(doc.get("irn", "")),
        "bill_to": f"{b['name']} {b['address']} GSTIN {b['gstin']}",
        "routing": (f"{sh['pol']} - {sh['pod']} ETD {sh['etd']} ETA {sh['eta']} "
                    f"Vessel {sh['vessel']} {sh['voyage']} MBL {sh['mbl']} "
                    f"HBL {sh['hbl']} Cntr {sh['container']} {sh['incoterm']} "
                    f"ES: {', '.join(rec.get('party_invoice_nos', []))} "
                    f"SB/BE: {rec.get('sb_be', '')}"),
        "hbl": sh["hbl"],
        "party_invoice_nos": ", ".join(rec.get("party_invoice_nos", [])),
        "charges_table": " ".join(rows),
        "grand_total": f"{rec['totals']['grand']:.2f}",
        "bank": f"{rec.get('bank', '')} {rec.get('terms', '')}",
        "title_bar": f"{doc['title']} Doc No {doc['no']} Date {doc['date']}",
    }


REGION_FIELD = {
    "doc_no": "Invoice No", "doc_date": "Invoice Date",
    "seller_name": "Vendor Name", "seller_address": "Vendor Name",
    "seller_gstin": "Vendor Name", "irn": "Invoice No",
    "bill_to": "Vendor Name", "routing": "Container Number",
    "hbl": "Bill of Lading / HBL", "party_invoice_nos": "Invoice No",
    "charges_table": "Sea Freight", "grand_total": "Sea Freight",
    "bank": "Vendor Name", "title_bar": "Invoice No",
}


def adapt(raw_dir: Path, out: Path, quotas: dict, want_sr: bool) -> None:
    from PIL import Image, ImageEnhance, ImageFilter

    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "manifests").mkdir(parents=True, exist_ok=True)
    if want_sr:
        (out / "sr" / "lr").mkdir(parents=True, exist_ok=True)
        (out / "sr" / "hr").mkdir(parents=True, exist_ok=True)
    rows, counts = [], {"train": 0, "val": 0, "test": 0}
    needed = dict(quotas)
    seq = 0
    for run_dir in sorted(raw_dir.glob("run_*")):
        for zf in sorted(run_dir.glob("chunk_*.zip")):
            meta = json.loads((run_dir / (zf.stem + ".json")).read_text())
            recs = {m["rec"]["invoice_id"]: m for m in meta}
            with zipfile.ZipFile(zf) as z:
                names = [n for n in z.namelist() if n.endswith(".png")]
                for name in sorted(names):
                    iid = Path(name).stem
                    m = recs.get(iid)
                    if m is None:
                        continue
                    rec = m["rec"]
                    split = split_for_variant(rec["template_variant"])
                    if needed.get(split, 0) <= 0:
                        continue
                    needed[split] -= 1
                    seq += 1
                    img = Image.open(io.BytesIO(z.read(name))).convert("RGB")
                    w, h = img.size
                    iid_new = f"INV_ILB_{seq:06d}"
                    ipath = out / "images" / f"{iid_new}.png"
                    img.save(ipath, optimize=True)
                    digest = hashlib.sha256(img.tobytes()).hexdigest()
                    gt = ground_truth(rec)
                    texts = region_texts(rec)
                    regions = []
                    for key, (bx, by, bw, bh) in (
                            rec.get("layout_bboxes_1000") or {}).items():
                        if key not in REGION_FIELD or not texts.get(key):
                            continue
                        regions.append({
                            "field": REGION_FIELD[key],
                            "bbox": [round(bx / 1000 * w), round(by / 1000 * h),
                                     round((bx + bw) / 1000 * w),
                                     round((by + bh) / 1000 * h)],
                            "text": texts[key],
                        })
                    regions.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
                    vendor = VENDOR_SLUGS.get(rec["seller"]["name"], "vendor_x")
                    row = {
                        "id": iid_new, "image": str(ipath.relative_to(out)),
                        "source": "invoicelab_raster", "source_dataset": None,
                        "generator_reference": "Table_Parsing/invoice-maker.html",
                        "seed": rec.get("seed"),
                        "template_id": f"freight_v{rec['template_variant']:02d}",
                        "vendor_id": vendor,
                        "layout_family": f"layout_{(rec['template_variant'] - 1) // 10}",
                        "split": split, "language": "en", "currency": "INR",
                        "sha256": digest,
                        "field_presence": {k: bool(v) for k, v in gt.items()},
                        "ground_truth": gt, "regions": regions,
                        "render": {"width": w, "height": h, "dpi": 192,
                                   "sheet_css": {"w": m["w"], "h": m["h"]}},
                        "degradation": {},
                    }
                    if want_sr:
                        _sr_pair(out, row, img, seq)
                    rows.append(row)
                    counts[split] += 1
        if all(v <= 0 for v in needed.values()):
            break
    (out / "manifests" / "all.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    for split in counts:
        (out / f"{split}.jsonl").write_text("\n".join(
            json.dumps(r, ensure_ascii=False) for r in rows if r["split"] == split) + "\n")
    (out / "manifests" / "dataset_card.json").write_text(json.dumps(
        {"documents": len(rows), "splits": counts,
         "source_counts": {"invoicelab_raster": len(rows)},
         "split_policy": "template-variant holdout: v01-30 train, v31-40 val, v41-50 test",
         "sha256_unique": len({r["sha256"] for r in rows}),
         "sr_pairs": sum("sr_pair" in r for r in rows)}, indent=2))
    print(json.dumps({"output": str(out.resolve()), "documents": len(rows),
                      "splits": counts, "unfilled": needed}))


def _sr_pair(out: Path, row: dict, img, seq: int) -> None:
    import random

    from PIL import Image, ImageEnhance, ImageFilter

    r = random.Random(10_000 + seq)
    w, h = img.size
    scale = r.choice([2, 3, 4])
    target = min(w // scale, 900)
    size = (target, round(h * target / w))
    lr = img.resize(size, Image.Resampling.LANCZOS)
    degradation = {"scale_factor": scale, "dpi": r.choice([50, 72, 96, 120, 150]),
                   "blur": r.choice(["none", "gaussian", "defocus"]),
                   "noise": r.choice(["none", "gaussian", "scanner"]),
                   "compression": r.choice([20, 30, 50, 70]),
                   "rotation_deg": round(r.uniform(-2, 2), 2),
                   "skew": r.choice(["none", "mild", "medium"])}
    if degradation["blur"] != "none":
        lr = lr.filter(ImageFilter.GaussianBlur(r.choice([.2, .45, .7])))
    lr = ImageEnhance.Contrast(lr).enhance(r.uniform(.7, 1.15))
    buf = io.BytesIO()
    lr.save(buf, format="JPEG", quality=degradation["compression"])
    lr = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    lrpath = out / "sr" / "lr" / (Path(row["image"]).stem + ".jpg")
    hrpath = out / "sr" / "hr" / (Path(row["image"]).stem + ".png")
    lr.save(lrpath)
    img.save(hrpath)
    row["sr_pair"] = {"source_invoice_id": row["id"],
                      "hr_path": str(hrpath.relative_to(out)),
                      "lr_path": str(lrpath.relative_to(out)),
                      "scale_factor": scale, "degradation": degradation}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raster", action="store_true")
    ap.add_argument("--adapt", action="store_true")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--per-run", type=int, default=24)
    ap.add_argument("--seed0", type=int, default=7001)
    ap.add_argument("--items", type=int, default=8)
    ap.add_argument("--raw", default="work/ilab_raw")
    ap.add_argument("--output", default="dataset_ilab")
    ap.add_argument("--train", type=int, default=3000)
    ap.add_argument("--val", type=int, default=1000)
    ap.add_argument("--test", type=int, default=1000)
    ap.add_argument("--sr", action="store_true")
    a = ap.parse_args()
    if a.raster:
        run_raster(Path(a.raw), a.runs, a.per_run, a.seed0, a.items)
    if a.adapt:
        adapt(Path(a.raw), Path(a.output),
              {"train": a.train, "val": a.val, "test": a.test}, a.sr)


if __name__ == "__main__":
    main()

