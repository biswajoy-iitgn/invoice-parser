#!/usr/bin/env python3
"""Turn clean InvoiceLab renders into scan-realistic, image-only PDFs (the scanned-PDF path).

Each scan applies a seeded chain of degradations to every page of a document:
  geometry  : resample to a scan DPI (130-300), rotation (+-3 deg), shear/skew, mild keystone
              (phone capture); word boxes are pushed through the same transform
  optics    : gaussian / motion blur, ink spread (min filter) or faded print (max filter)
  scanner   : uneven illumination, paper tone, edge shadow, gaussian noise, speckle,
              grayscale or fax-style binarization, JPEG compression
The pages are written as JPEGs and wrapped into one image-only PDF (no text layer), so the
pipeline must OCR them exactly like a real scanned invoice.

Usage:
  .venv/bin/python scripts/make_scans.py dataset_v2 --fraction-train 0.5 --workers 8
Output: dataset_v2/scans/{images,pdf,ann}/ and dataset_v2/scans_{split}.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import multiprocessing as mp
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

RENDER_DPI = 198.0  # 820 CSS px sheet at device scale 2 over an A4 width


def _homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.asarray(A, dtype=np.float64))
    H = vt[-1].reshape(3, 3)
    return H / H[2, 2]


def _apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.c_[pts, np.ones(len(pts))] @ H.T
    return p[:, :2] / p[:, 2:3]


def plan(rnd: random.Random) -> dict:
    """Sample one scan recipe (shared by all pages of a document)."""
    kind = rnd.choices(["flatbed", "office_mfp", "phone", "fax", "old_copy"], [40, 25, 15, 10, 10])[0]
    p = {"kind": kind, "dpi": rnd.choice([130, 150, 150, 200, 200, 300]) if kind != "fax" else rnd.choice([100, 130]),
         "rotation": round(rnd.uniform(-3.0, 3.0) if kind != "flatbed" else rnd.uniform(-1.5, 1.5), 2),
         "shear": round(rnd.uniform(-0.015, 0.015), 4),
         "keystone": round(rnd.uniform(0.01, 0.045), 3) if kind == "phone" else 0.0,
         "blur": round(rnd.choice([0, 0.2, 0.35]) if kind == "fax" else (rnd.choice([0, 0.3, 0.5, 0.8, 1.1]) if kind != "flatbed" else rnd.choice([0, 0.3, 0.5])), 2),
         "motion": kind == "phone" and rnd.random() < 0.3,
         "ink": "normal" if kind == "fax" else rnd.choice(["normal", "normal", "spread", "faded"]),
         "illum": round(rnd.uniform(0.0, 0.35 if kind == "phone" else 0.15), 3),
         "paper": rnd.choice([(255, 255, 255), (250, 248, 240), (245, 240, 228), (238, 236, 232)]),
         "shadow": kind in ("flatbed", "old_copy") and rnd.random() < 0.4,
         "noise": round(rnd.uniform(1.5, 9.0), 2),
         "speckle": round(rnd.uniform(0, 0.004), 4) if kind in ("fax", "old_copy") else 0.0,
         "gray": kind in ("fax", "office_mfp", "old_copy") or rnd.random() < 0.3,
         "binarize": kind == "fax",
         "jpeg": rnd.choice([35, 50, 65, 75, 85, 92])}
    return p


def degrade(img: Image.Image, words: list[dict], p: dict, rnd: random.Random) -> tuple[Image.Image, list[dict], dict]:
    W, H = img.size
    s = p["dpi"] / RENDER_DPI
    nw, nh = max(64, int(W * s)), max(64, int(H * s))
    # destination corners: scale -> shear -> keystone -> rotate about centre
    src = np.array([[0, 0], [W, 0], [W, H], [0, H]], dtype=np.float64)
    dst = src * [nw / W, nh / H]
    dst[:, 0] += dst[:, 1] * p["shear"]
    if p["keystone"]:
        k = p["keystone"] * nw
        dst[0, 0] += k * rnd.uniform(0.3, 1)
        dst[1, 0] -= k * rnd.uniform(0.3, 1)
        dst[0, 1] += k * rnd.uniform(0, 0.6)
        dst[1, 1] += k * rnd.uniform(0, 0.6)
    a = math.radians(p["rotation"])
    c = dst.mean(axis=0)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    dst = (dst - c) @ R.T + c
    pad_x, pad_y = rnd.randint(8, 40), rnd.randint(8, 40)
    dst -= dst.min(axis=0)
    dst += [pad_x, pad_y]
    out_w, out_h = int(dst[:, 0].max() + pad_x), int(dst[:, 1].max() + pad_y)
    Hm = _homography(src, dst)
    Hinv = np.linalg.inv(Hm)
    coeffs = (Hinv / Hinv[2, 2]).ravel()[:8]
    base = img.convert("RGB")
    # ink effects at render resolution (before downsampling) so strokes stay legible
    if p["ink"] == "spread":
        base = Image.blend(base, base.filter(ImageFilter.MinFilter(3)), 0.55)
    elif p["ink"] == "faded":
        base = Image.blend(base, base.filter(ImageFilter.MaxFilter(3)), 0.6)
    warped = base.transform((out_w, out_h), Image.Transform.PERSPECTIVE, tuple(coeffs),
                            resample=Image.Resampling.BICUBIC, fillcolor=p["paper"])
    # paper tone: multiply white areas toward the paper colour
    arr = np.asarray(warped).astype(np.float32)
    paper = np.array(p["paper"], dtype=np.float32) / 255.0
    arr = arr * paper
    if p["illum"]:
        yy, xx = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
        gx, gy = rnd.uniform(-1, 1), rnd.uniform(-1, 1)
        g = (gx * (xx / out_w - 0.5) + gy * (yy / out_h - 0.5))
        arr *= (1.0 - p["illum"] * (g - g.min()) / max(1e-6, (g.max() - g.min())))[..., None]
    if p["shadow"]:
        band = rnd.randint(10, 60)
        side = rnd.choice(["l", "r", "t", "b"])
        ramp = np.linspace(0.55, 1.0, band, dtype=np.float32)
        if side == "l":
            arr[:, :band] *= ramp[None, :, None]
        elif side == "r":
            arr[:, -band:] *= ramp[::-1][None, :, None]
        elif side == "t":
            arr[:band] *= ramp[:, None, None]
        else:
            arr[-band:] *= ramp[::-1][:, None, None]
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if p["blur"]:
        out = out.filter(ImageFilter.GaussianBlur(p["blur"] * s * 1.2))
    if p["motion"]:
        k = rnd.choice([3, 5])
        kern = [0] * (k * k)
        for i in range(k):
            kern[(k // 2) * k + i] = 1
        out = out.filter(ImageFilter.Kernel((k, k), kern, scale=k))
    arr = np.asarray(out).astype(np.float32)
    nrnd = np.random.default_rng(rnd.randrange(1 << 30))
    arr += nrnd.normal(0, p["noise"], arr.shape)
    if p["speckle"]:
        m = nrnd.random(arr.shape[:2])
        arr[m < p["speckle"] / 2] = 0
        arr[m > 1 - p["speckle"] / 2] = 255
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if p["gray"] or p["binarize"]:
        out = ImageOps.grayscale(out)
    if p["binarize"]:
        g = np.asarray(out, dtype=np.float32)
        thr = int(min(200, max(120, g.mean() - 0.35 * g.std())))
        out = out.point(lambda v: 255 if v > thr else 0).convert("L")
    buf = io.BytesIO()
    out.convert("RGB" if out.mode == "RGB" else "L").save(buf, format="JPEG", quality=p["jpeg"])
    final = Image.open(io.BytesIO(buf.getvalue()))
    final.load()
    # boxes through the same homography
    new_words = []
    for w in words:
        x0, y0, x1, y1 = w["b"]
        q = _apply_h(Hm, np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64))
        nb = [round(float(q[:, 0].min()), 1), round(float(q[:, 1].min()), 1),
              round(float(q[:, 0].max()), 1), round(float(q[:, 1].max()), 1)]
        nw_ = dict(w)
        nw_["b"] = nb
        new_words.append(nw_)
    return final, new_words, {"scale": round(s, 4), "out_size": [out_w, out_h]}


def work(job: tuple) -> dict:
    root, row, k, seed = job
    root = Path(root)
    rnd = random.Random(seed)
    p = plan(rnd)
    ann = json.loads((root / row["ann"]).read_text())
    sid = f"{row['id']}_s{k}"
    pages, page_imgs = [], []
    for j, pg in enumerate(row["pages"]):
        img = Image.open(root / pg["image"])
        out, words, geo = degrade(img, ann["pages"][j]["words"], p, rnd)
        ip = root / "scans" / "images" / f"{sid}_p{j + 1}.jpg"
        out.save(ip, quality=p["jpeg"])
        page_imgs.append(out)
        pages.append({"image": str(ip.relative_to(root)), "width": out.width, "height": out.height,
                      "words": words, **geo})
    pdf = root / "scans" / "pdf" / f"{sid}.pdf"
    if row["split"] != "train":
        rgb = [im.convert("RGB") for im in page_imgs]
        rgb[0].save(pdf, save_all=True, append_images=rgb[1:], resolution=float(p["dpi"]))
    (root / "scans" / "ann" / f"{sid}.json").write_text(json.dumps({"id": sid, "pages": pages}))
    return {"scan_id": sid, "id": row["id"], "split": row["split"], "pdf": str(pdf.relative_to(root)) if pdf.exists() else "",
            "pages": [{k2: v for k2, v in pg.items() if k2 != "words"} for pg in pages],
            "ann": f"scans/ann/{sid}.json", "degradation": p,
            "sha256": hashlib.sha256((root / pages[0]["image"]).read_bytes()).hexdigest()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--fraction-train", type=float, default=0.5)
    ap.add_argument("--per-doc-eval", type=int, default=1, help="scans per val/test document")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261003)
    a = ap.parse_args()
    root = Path(a.root)
    for d in ("images", "pdf", "ann"):
        (root / "scans" / d).mkdir(parents=True, exist_ok=True)
    rnd = random.Random(a.seed)
    jobs = []
    for split in ("train", "val", "test"):
        rows = [json.loads(l) for l in (root / f"{split}.jsonl").open()]
        for row in rows:
            if split == "train":
                if rnd.random() < a.fraction_train:
                    jobs.append((str(root), row, 0, rnd.randrange(1 << 31)))
            else:
                for k in range(a.per_doc_eval):
                    jobs.append((str(root), row, k, rnd.randrange(1 << 31)))
    out = {"train": [], "val": [], "test": []}
    with mp.get_context("spawn").Pool(a.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(work, jobs, chunksize=8)):
            out[r["split"]].append(r)
            if (i + 1) % 1000 == 0:
                print(f"{i + 1}/{len(jobs)} scans", flush=True)
    for split, rs in out.items():
        rs.sort(key=lambda r: r["scan_id"])
        with (root / f"scans_{split}.jsonl").open("w") as f:
            for r in rs:
                f.write(json.dumps(r) + "\n")
    print(json.dumps({s: len(v) for s, v in out.items()}))


if __name__ == "__main__":
    main()
