"""Build the synthetic freight-invoice benchmark from invoice-maker.html.

The maker (logistics doc type) renders the page and emits the 31-field ground
truth; this script drives it headlessly, captures each sheet, applies seeded
scanner physics (resampling, skew, perspective, blur, illumination, noise,
binarisation, JPEG), and writes:

  dataset/invoices/<id>.{png,jpg,pdf}   85% raster (PNG/JPG/image-only PDF), 15% digital PDF
  dataset/ground_truth/<id>.json        31 fields + per-line categories + metadata
  dataset/gold_text/<id>.txt            text actually printed on the page (DOM innerText)
  dataset/manifest.jsonl                one row per document

Split protocol: dev = templates T1-T6; test = new instances of T1-T6 plus
templates T7 and T8, which never appear in dev (unseen-layout generalisation).

    python dataset/build_dataset.py            # default 60 dev + 90 test
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import os
import random
import shutil
import threading
from pathlib import Path

import cv2
import fitz
import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dataset"
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")

TIER_MAKER = {
    "clean": dict(hard=0, grain=0, faint=0, stamp=False, paper=False),
    "medium": dict(hard=35, grain=30, faint=30, stamp=True, paper=False),
    "scan": dict(hard=75, grain=60, faint=50, stamp=True, paper=True),
}
TIER_DSF = {"clean": 2.0, "medium": 1.75, "scan": 1.6}
HIDE_CHROME = """.topbar,.sidenav,.config,.pagehead,.tabs,.statusbar,#dlCenter,.batchbar{display:none!important}
.main{padding:0!important}.stage{padding:0!important;background:#fff!important;border:0!important}
.sheet{box-shadow:none!important;margin:0 0 24px!important}"""

SPLITS = {
    "dev": [("T1", 10), ("T2", 10), ("T3", 10), ("T4", 10), ("T5", 10), ("T6", 10)],
    "test": [("T1", 10), ("T2", 10), ("T3", 10), ("T4", 10), ("T5", 10), ("T6", 10), ("T7", 15), ("T8", 15)],
}


def assign_tier_format(rng: random.Random) -> tuple[str, str]:
    tier = rng.choices(["clean", "medium", "scan"], weights=[30, 40, 30])[0]
    if tier == "clean":
        fmt = rng.choices(["pdf_digital", "png", "pdf_raster"], weights=[50, 35, 15])[0]
    elif tier == "medium":
        fmt = rng.choices(["jpg", "pdf_raster"], weights=[70, 30])[0]
    else:
        fmt = rng.choices(["jpg", "pdf_raster"], weights=[65, 35])[0]
    return tier, fmt


def _rotate(img: np.ndarray, angle: float, bed: tuple[int, int, int]) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=bed)


def degrade(img: np.ndarray, tier: str, rng: np.random.Generator) -> tuple[np.ndarray, dict]:
    info: dict = {}
    if tier == "clean":
        return img, info
    scan = tier == "scan"
    # scan ~150 DPI equivalent (1.25-1.45 px per CSS px); below ~120 DPI the digits stop being human-legible
    scale = float(rng.uniform(0.78, 0.9) if scan else rng.uniform(0.82, 0.95))
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    info["resample"] = round(scale, 3)
    bed = (int(rng.integers(215, 245)),) * 3 if scan else (255, 255, 255)
    angle = float(rng.uniform(-2.5, 2.5) if scan else rng.uniform(-0.8, 0.8))
    img = _rotate(img, angle, bed)
    info["rotation_deg"] = round(angle, 2)
    h, w = img.shape[:2]
    if scan and rng.random() < 0.4:
        j = 0.012
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = src + rng.uniform(-j, j, size=(4, 2)).astype(np.float32) * np.float32([w, h])
        img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=bed)
        info["perspective"] = True
    sigma = float(rng.uniform(0.45, 0.8) if scan else rng.uniform(0.25, 0.55))
    img = cv2.GaussianBlur(img, (0, 0), sigma)
    info["blur_sigma"] = round(sigma, 2)
    f = img.astype(np.float32)
    if scan:
        gx = np.linspace(float(rng.uniform(0.82, 1.0)), float(rng.uniform(0.9, 1.0)), w, dtype=np.float32)
        gy = np.linspace(float(rng.uniform(0.88, 1.0)), float(rng.uniform(0.85, 1.0)), h, dtype=np.float32)
        f *= (gy[:, None] * gx[None, :])[..., None]
        info["illumination"] = True
    noise = float(rng.uniform(8, 14) if scan else rng.uniform(3, 6))
    f += rng.normal(0, noise, f.shape).astype(np.float32)
    img = np.clip(f, 0, 255).astype(np.uint8)
    info["noise_sigma"] = round(noise, 1)
    if scan and rng.random() < 0.2:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12)
        sp = rng.random(b.shape)
        b[sp < 0.002] = 0
        b[sp > 0.998] = 255
        img = cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)
        info["binarised"] = True
    return img, info


def write_raster(img: np.ndarray, path: Path, fmt: str, tier: str, rng: np.random.Generator) -> dict:
    q = {"clean": 92, "medium": int(rng.integers(70, 86)), "scan": int(rng.integers(50, 71))}[tier]
    if fmt == "png":
        cv2.imwrite(str(path), img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        return {"encoding": "png"}
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    assert ok
    if fmt == "jpg":
        path.write_bytes(buf.tobytes())
        return {"encoding": "jpeg", "jpeg_quality": q}
    h, w = img.shape[:2]
    pw = 595.0
    doc = fitz.open()
    page = doc.new_page(width=pw, height=pw * h / w)
    page.insert_image(page.rect, stream=buf.tobytes())
    doc.save(str(path), deflate=True)
    doc.close()
    return {"encoding": "image-only pdf (jpeg)", "jpeg_quality": q}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def build(n_scale: float, seed: int, port: int) -> None:
    for sub in ("invoices", "ground_truth", "gold_text"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
        (OUT / sub).mkdir(parents=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}/"
    css = None
    manifest = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        pages = {dsf: browser.new_page(device_scale_factor=dsf, viewport={"width": 1500, "height": 1200})
                 for dsf in sorted(set(TIER_DSF.values()))}
        for pg in pages.values():
            pg.goto(base + "invoice-maker.html")
            pg.add_style_tag(content=HIDE_CHROME)
        css = pages[2.0].evaluate("document.querySelector('style').textContent")
        pdf_page = browser.new_page()
        for split, groups in SPLITS.items():
            k = 0
            for tpl, n in groups:
                n = max(1, round(n * n_scale))
                rng = random.Random(f"{seed}-{split}-{tpl}")
                plan = [assign_tier_format(rng) for _ in range(n)]
                for tier in ("clean", "medium", "scan"):
                    idx = [i for i, (t, _) in enumerate(plan) if t == tier]
                    if not idx:
                        continue
                    call_seed = int(hashlib.sha256(f"{seed}{split}{tpl}{tier}".encode()).hexdigest()[:8], 16) % 900000 + 1
                    pg = pages[TIER_DSF[tier]]
                    opts = dict(types=["logistics"], count=len(idx), seed=call_seed, template=tpl, mode="mixed",
                                caption=False, rot=0, items=6, miss=15, tstyle="mixed", **TIER_MAKER[tier])
                    pg.evaluate("o => InvoiceLab.run(o)", opts)
                    pg.wait_for_function("[...document.querySelectorAll('#stage img.brandlogo')].every(i => i.complete)")
                    pg.wait_for_timeout(150)
                    gts = pg.evaluate("InvoiceLab.fields31()")
                    recs = pg.evaluate("InvoiceLab.batch().map(r => ({difficulty: r.difficulty, logo: r.logo_file}))")
                    sheets = pg.query_selector_all("#stage .sheet")
                    for j, i in enumerate(idx):
                        k += 1
                        doc_id = f"{split}-{k:03d}"
                        fmt = plan[i][1]
                        sheet, gt = sheets[j], gts[j]
                        gold = sheet.inner_text()
                        nrng = np.random.default_rng(int(hashlib.sha256(f"{seed}{doc_id}".encode()).hexdigest()[:8], 16))
                        ext = {"pdf_digital": "pdf", "pdf_raster": "pdf", "png": "png", "jpg": "jpg"}[fmt]
                        path = OUT / "invoices" / f"{doc_id}.{ext}"
                        deg: dict = {}
                        if fmt == "pdf_digital":
                            box = sheet.bounding_box()
                            html = sheet.evaluate("e => e.outerHTML")
                            pdf_page.set_viewport_size({"width": int(box["width"]) + 2, "height": 1200})
                            pdf_page.emulate_media(media="print")
                            pdf_page.set_content(f"<html><head><base href='{base}'><style>{css}</style></head>"
                                                 f"<body style='margin:0;background:#fff'>{html}</body></html>")
                            pdf_page.wait_for_load_state("networkidle")
                            dims = pdf_page.evaluate("(() => {const r = document.querySelector('.sheet').getBoundingClientRect();"
                                                     " return [Math.ceil(r.width), Math.ceil(r.bottom)]})()")
                            pdf_page.pdf(path=str(path), width=f"{dims[0] + 2}px", height=f"{dims[1] + 24}px",
                                         print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
                            deg = {"encoding": "digital pdf (text layer)"}
                        else:
                            png = sheet.screenshot(type="png")
                            img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
                            img, deg = degrade(img, tier, nrng)
                            deg.update(write_raster(img, path, fmt, tier, nrng))
                            deg["pixels"] = [int(img.shape[1]), int(img.shape[0])]
                        (OUT / "gold_text" / f"{doc_id}.txt").write_text(gold)
                        row = {
                            "id": doc_id, "split": split, "file": f"invoices/{path.name}", "format": fmt,
                            "tier": tier, "template": tpl, "template_name": None, "mode": gt["mode"],
                            "unseen_template": split == "test" and tpl in ("T7", "T8"),
                            "maker_seed": call_seed, "maker_difficulty": recs[j]["difficulty"],
                            "degradation": deg,
                        }
                        record = {**row, "fields": gt["ground_truth"],
                                  "lines": [{"description": x["desc"], "category": x["cat"], "amount_inr": x["inr"],
                                             "currency": x["cur"], "heldout_wording": x["ho"]} for x in gt["lines"]],
                                  "label_heldout": gt["label_heldout"]}
                        (OUT / "ground_truth" / f"{doc_id}.json").write_text(json.dumps(record, indent=2))
                        manifest.append(row)
                        print(f"{doc_id} {tpl} {gt['mode']:9} {tier:6} {fmt}")
        browser.close()
    srv.shutdown()
    names = {"T1": "classic-box", "T2": "banner-kv", "T3": "mono-dotmatrix", "T4": "modern-minimal",
             "T5": "wide-13col", "T6": "sectioned-subtotals", "T7": "kv-grid-inline-fx", "T8": "serif-narrative"}
    with open(OUT / "manifest.jsonl", "w") as fh:
        for row in manifest:
            row["template_name"] = names[row["template"]]
            fh.write(json.dumps(row) + "\n")
    print(f"{len(manifest)} documents -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=1.0, help="multiply documents per template (default 1.0 = 150 docs)")
    ap.add_argument("--seed", type=int, default=20260926)
    ap.add_argument("--port", type=int, default=8791)
    a = ap.parse_args()
    build(a.scale, a.seed, a.port)
