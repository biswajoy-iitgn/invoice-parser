#!/usr/bin/env python3
"""Render the training corpus with InvoiceLab Studio (Table_Parsing/invoice-maker.html).

The app is the generator: this driver only serves it on localhost, calls its agent API
(`InvoiceLab.dataset` -> records, `InvoiceLab.renderRecord(i)` -> DOM sheets + measured
word boxes with field roles), and screenshots every sheet in headless Chromium.

Split policy is decided inside the app (layout family + vendor holdout, alias buckets);
each chunk asks for one split so counts are exact.

Usage:
  .venv/bin/python scripts/build_invoicelab_v2.py --train 12000 --val 1500 --test 1500 --workers 8
  .venv/bin/python scripts/build_invoicelab_v2.py --train 40 --val 10 --test 10 --out work/ilab_pilot   # pilot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKER_DIR = Path(os.environ.get("INVOICELAB_DIR", "/Users/dtadmin/Desktop/DevelopmentProjects/Table_Parsing"))
PORT = 8901
URL = f"http://127.0.0.1:{PORT}/invoice-maker.html"
SCALE = 2  # device pixels per CSS px (~198 DPI for an A4-width 820px sheet)
CHUNK = 250
SEED0 = {"train": 610000, "val": 620000, "test": 630000}


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_server() -> subprocess.Popen | None:
    if _port_open(PORT):
        return None
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
                            cwd=str(MAKER_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if _port_open(PORT):
            return proc
        time.sleep(0.1)
    raise RuntimeError("could not start http.server for InvoiceLab")


def _slim_record(rec: dict) -> dict:
    fam, ven = rec.get("family_spec", {}), rec.get("vendor_spec", {})
    keep_f = ["id", "split", "header", "meta", "parties", "refs", "table", "skin", "totals", "footer", "font",
              "size", "labelCase", "descCase", "amountFmt", "dateFmt", "landscape", "page2"]
    return {
        "id": rec["invoice_id"], "split": rec["split"], "family": rec["template_variant"],
        "vendor_id": rec["vendor_id"], "vendor_name": ven.get("name"), "mode": rec["mode"], "kind": rec["kind"],
        "load": rec.get("load"), "region": rec.get("region"), "incoterm": rec.get("incoterm"),
        "destination_country": rec.get("destination_country"), "n_pages": rec["pages"], "page2": rec.get("page2"),
        "ground_truth": rec["gt"], "reader_target": rec["reader_target"],
        "items": [{k: it[k] for k in ("category", "descMain", "descSub", "ref", "desc", "alias", "alias_bucket",
                                      "qualifier", "hsn", "qty", "rate", "curr", "fx", "amt_fc", "taxable",
                                      "gst", "total")} for it in rec["items"]],
        "labels": rec["labels"], "values": rec["values"], "shown": rec["show"],
        "alias_buckets": rec["alias_buckets"], "missing_fields": rec["missing_fields"],
        "meta_codes": rec["metaCodes"], "ref_codes": rec["refCodes"], "totals": rec["totals"],
        "fx": rec["fx"], "family_spec": {k: fam.get(k) for k in keep_f}, "difficulty": rec.get("difficulty", {}),
    }


def want_pdf(doc_id: str, split: str) -> bool:
    """Text-layer PDFs: every val/test document + a fixed 40% of train (hash-selected)."""
    if split != "train":
        return True
    return int(hashlib.sha256(doc_id.encode()).hexdigest(), 16) % 10 < 4


def write_pdf(page, n_sheets: int, out_pdf: Path) -> None:
    """Print each sheet alone to a page of its own size (Chromium text layer), then merge."""
    import fitz

    merged = fitz.open()
    for j in range(n_sheets):
        w, h = page.evaluate(
            """j => { const ss=[...document.querySelectorAll('#stage .sheet')];
                 ss.forEach((s,k)=>s.style.display=k===j?'':'none');
                 const s=ss[j]; return [Math.ceil(s.offsetWidth), Math.ceil(s.offsetHeight)+2]; }""", j)
        data = page.pdf(width=f"{w}px", height=f"{h}px", print_background=True,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}, page_ranges="1")
        with fitz.open(stream=data, filetype="pdf") as one:
            merged.insert_pdf(one)
    page.evaluate("() => document.querySelectorAll('#stage .sheet').forEach(s=>s.style.display='')")
    merged.save(str(out_pdf), garbage=3, deflate=True)


def worker(job: tuple) -> list[dict]:
    split, seed, offset, count, out_dir, prefix, mode = job
    from playwright.sync_api import sync_playwright

    out = Path(out_dir)
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 1300}, device_scale_factor=SCALE)
        page = ctx.new_page()
        page.goto(URL, wait_until="load")
        page.wait_for_function("window.InvoiceLab && window.InvoiceLab.dataset")
        page.evaluate("o => InvoiceLab.dataset(o)",
                      {"seed": seed, "count": count, "split": split, "offset": offset, "prefix": prefix,
                       "miss": 15, "hard": 30})
        for i in range(count):
            rec = page.evaluate("i => InvoiceLab.record(i)", i)
            if mode == "pdf" and not want_pdf(rec["invoice_id"], rec["split"]):
                continue
            info = page.evaluate("i => InvoiceLab.renderRecord(i)", i)
            if mode == "pdf":
                ann = json.loads((out / "ann" / f"{rec['invoice_id']}.json").read_text())
                n_words = [len(pg["words"]) for pg in ann["pages"]]
                if n_words != [len(pg["words"]) for pg in info["pages"]]:
                    raise RuntimeError(f"non-deterministic re-render for {rec['invoice_id']}")
                write_pdf(page, len(info["pages"]), out / "pdf" / f"{rec['invoice_id']}.pdf")
                rows.append({"id": rec["invoice_id"], "split": rec["split"], "pdf": f"pdf/{rec['invoice_id']}.pdf"})
                continue
            sheets = page.locator("#stage .sheet")
            pages = []
            for j, meas in enumerate(info["pages"]):
                img = out / "images" / f"{rec['invoice_id']}_p{j + 1}.png"
                png = sheets.nth(j).screenshot(path=str(img), animations="disabled", scale="device")
                words = [{"t": w["t"], "b": [round(v * SCALE, 1) for v in w["b"]], "k": w["k"], "li": w["li"],
                          **({"k2": w["k2"]} if w.get("k2") else {})} for w in meas["words"]]
                pages.append({"image": f"images/{img.name}", "width": round(meas["w"] * SCALE),
                              "height": round(meas["h"] * SCALE), "sha256": hashlib.sha256(png).hexdigest(),
                              "words": words})
            row = _slim_record(rec)
            row["seed"] = seed
            row["difficulty"] = info.get("difficulty") or row["difficulty"]
            ann = {"id": row["id"], "pages": pages}
            (out / "ann" / f"{row['id']}.json").write_text(json.dumps(ann, ensure_ascii=False))
            row["pages"] = [{k: v for k, v in pg.items() if k != "words"} for pg in pages]
            row["image"] = pages[0]["image"]
            row["sha256"] = pages[0]["sha256"]
            row["ann"] = f"ann/{row['id']}.json"
            rows.append(row)
        browser.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=12000)
    ap.add_argument("--val", type=int, default=1500)
    ap.add_argument("--test", type=int, default=1500)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="dataset_v2")
    ap.add_argument("--pdf", action="store_true", help="second pass: text-layer PDFs for a subset")
    a = ap.parse_args()
    out = Path(a.out)
    mode = "pdf" if a.pdf else "png"
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "ann").mkdir(parents=True, exist_ok=True)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    server = ensure_server()
    jobs, offset = [], 0
    for split, n in (("train", a.train), ("val", a.val), ("test", a.test)):
        for c, start in enumerate(range(0, n, CHUNK)):
            cnt = min(CHUNK, n - start)
            jobs.append((split, SEED0[split] + c, offset, cnt, str(out), "ILB", mode))
            offset += cnt
    t0 = time.time()
    rows: list[dict] = []
    try:
        with mp.get_context("spawn").Pool(a.workers) as pool:
            for k, part in enumerate(pool.imap_unordered(worker, jobs)):
                rows.extend(part)
                print(f"[{k + 1}/{len(jobs)}] {len(rows)} docs  {time.time() - t0:.0f}s", flush=True)
    finally:
        if server:
            server.terminate()
    rows.sort(key=lambda r: r["id"])
    if mode == "pdf":
        (out / "pdf_index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(json.dumps({"pdfs": len(rows), "seconds": round(time.time() - t0)}))
        return
    for split in ("train", "val", "test"):
        with (out / f"{split}.jsonl").open("w") as f:
            for r in rows:
                if r["split"] == split:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"out": str(out.resolve()), "docs": len(rows),
                      "pages": sum(r["n_pages"] for r in rows),
                      "splits": {s: sum(r["split"] == s for r in rows) for s in ("train", "val", "test")},
                      "seconds": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
