"""PP-OCRv6 worker process (runs under a Python that has paddlepaddle + paddleocr).

Protocol: one JSON object per stdin line -> one JSON object per stdout line.
  request : {"id": "...", "image": "/abs/path.png"}
  response: {"id": "...", "width": W, "height": H, "elapsed": s, "models": [...],
             "lines": [{"text": str, "score": float, "box": [x0, y0, x1, y1], "poly": [[x, y] * 4]}]}
Errors come back as {"id": ..., "error": "..."}; the worker keeps serving.

Unwarping and orientation rotation are disabled on purpose: they change the coordinate
frame, and every OCR box here is used as a citation on the ORIGINAL page image.

Standalone on purpose (no invoice_extractor imports): launched by ppocr_client with the
interpreter from PPOCR_PYTHON (default ~/paddle-env/bin/python). Also usable for batch runs:
  ~/paddle-env/bin/python invoice_extractor/ppocr_worker.py < requests.jsonl > results.jsonl
"""
from __future__ import annotations

import json
import os
import sys
import time


def build_engine():
    from paddleocr import PaddleOCR  # type: ignore

    kw = dict(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
              device=os.environ.get("PADDLE_DEVICE", "cpu"))
    det = os.environ.get("PPOCR_DET_MODEL", "PP-OCRv6_medium_det")
    rec = os.environ.get("PPOCR_REC_MODEL", "PP-OCRv6_medium_rec")
    if det:
        kw["text_detection_model_name"] = det
    if rec:
        kw["text_recognition_model_name"] = rec
    if os.environ.get("PPOCR_REC_MODEL_DIR"):
        kw["text_recognition_model_dir"] = os.environ["PPOCR_REC_MODEL_DIR"]
    if os.environ.get("PPOCR_DET_MODEL_DIR"):
        kw["text_detection_model_dir"] = os.environ["PPOCR_DET_MODEL_DIR"]
    # Large invoices: keep the detector at native resolution up to a cap.
    kw["text_det_limit_side_len"] = int(os.environ.get("PPOCR_DET_LIMIT", "2560"))
    kw["text_det_limit_type"] = "max"
    return PaddleOCR(**kw), [det, rec]


def prepare(path: str, limit: int):
    """Page as a BGR array whose sides are multiples of 32 and <= the detector limit.

    PaddleOCR would otherwise resize the page to such a size itself with cv2.resize, and OpenCV 5's
    KleidiCV resize segfaults on Apple Silicon for some page sizes (e.g. 1734x2437 -> 1728x2432).
    Scaling is done with Pillow and the rest is white padding at the right/bottom, so box coordinates
    only need dividing by `scale` to be back in original pixels.
    """
    import numpy as np
    from PIL import Image

    lim = max(32, limit // 32 * 32)
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = 1.0
        if max(w, h) > lim:
            scale = lim / max(w, h)
            im = im.resize((max(32, int(w * scale)), max(32, int(h * scale))), Image.Resampling.LANCZOS)
        cw, ch = -(-im.width // 32) * 32, -(-im.height // 32) * 32
        if (cw, ch) != im.size:
            canvas = Image.new("RGB", (cw, ch), (255, 255, 255))
            canvas.paste(im, (0, 0))
            im = canvas
        arr = np.ascontiguousarray(np.asarray(im)[:, :, ::-1])
    return arr, w, h, scale


def run(engine, path: str) -> dict:
    arr, w, h, scale = prepare(path, int(os.environ.get("PPOCR_DET_LIMIT", "2560")))
    res = engine.predict(arr)
    r = res[0]
    get = (lambda k: r[k]) if isinstance(r, dict) or hasattr(r, "__getitem__") else (lambda k: getattr(r, k))
    texts, scores = list(get("rec_texts")), [float(s) for s in get("rec_scores")]
    boxes = [[float(v) / scale for v in b] for b in get("rec_boxes")]
    polys = [[[float(x) / scale, float(y) / scale] for x, y in p] for p in get("rec_polys")]
    lines = [{"text": t, "score": round(s, 4), "box": [round(v, 1) for v in b], "poly": p}
             for t, s, b, p in zip(texts, scores, boxes, polys) if t.strip()]
    return {"width": int(w), "height": int(h), "lines": lines}


def main() -> None:
    # OpenCV 5's KleidiCV resize can crash on Apple Silicon when it runs from OpenCV's thread pool, and the
    # crash then spins forever inside Paddle's signal handler. One OpenCV thread avoids it; Paddle's own
    # inference threads are unaffected. CV_THREADS=default restores OpenCV's setting.
    n = os.environ.get("CV_THREADS", "1")
    if n != "default":
        import cv2

        cv2.setNumThreads(int(n))
    engine, models = build_engine()
    # Paddle installs a crash handler that can spin forever; Python's makes a native crash exit at once,
    # which the client reports as "worker died" and replaces the worker.
    import faulthandler

    faulthandler.enable(file=sys.stderr, all_threads=False)
    sys.stdout.write(json.dumps({"ready": True, "models": models}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        t0 = time.time()
        try:
            out = run(engine, req["image"])
            out.update({"id": req.get("id"), "elapsed": round(time.time() - t0, 3), "models": models})
        except Exception as exc:  # keep serving
            out = {"id": req.get("id"), "error": f"{type(exc).__name__}: {exc}"[:500]}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
