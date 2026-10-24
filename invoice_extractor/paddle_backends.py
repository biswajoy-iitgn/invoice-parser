"""PP-StructureV3 + PaddleOCR-VL adapters (gated by availability).

Runs on this Mac under ~/paddle-env (paddlepaddle 3.3.1 CPU + paddleocr 3.7.0
+ paddlex 3.7.2, per Paddle's Apple Silicon guide; needs `paddlex[ocr]`
extras and PADDLE_DEVICE=cpu). Verified 2026-10-02: PP-OCRv6, PP-StructureV3
and PaddleOCR-VL 1.6 all infer on Mac CPU. The project .venv has no paddle,
so paddle entry points must run under paddle-env (see scripts/track_paddle.py);
elsewhere require_paddle() raises a clear error instead of silently degrading.
  - layout_and_tables(): PP-StructureV3 (PP-DocLayoutV3 + table structure +
    OCR) for layout regions, tables, reading order. Bounding boxes are
    preserved for evidence.
  - vl_extract(): PaddleOCR-VL for OCR + visual understanding + structured
    candidate generation.

The production path (ingest.py) prefers this stack when runnable
(INVOICE_INGEST=auto|paddle|legacy) and falls back to Docling + HunyuanOCR.

Blackwell runtime (RTX PRO 6000, sm120): PaddlePaddle >= 3.2.1 from the
cu129 index (driver must support CUDA 12.9+), or the official sm120 Docker
image. The cu126 3.1.1 build fails on Blackwell ("Unsupported GPU
architecture"); require_paddle() enforces the floor on CUDA builds.

GPU-host validation (2026-10-01, RTX PRO 6000 Blackwell):
- PP-OCR (det+rec) runs on CPU and reads invoices correctly; rec_texts /
  rec_boxes / rec_scores contract verified. Used as an OCR leg.
- PP-StructureV3 / PaddleOCR-VL 1.5 with cu126 3.1.1: GPU failed
  (no sm_120a kernels), CPU failed (OneDNN executor bug), VL requires CUDA.
  Retry with cu129 3.2.1+ pending (exact commands in SETUP.md).
"""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def paddle_available() -> bool:
    try:
        import paddle  # noqa: F401
        from paddleocr import PaddleOCRVL  # noqa: F401
        return True
    except Exception:
        return False


def paddle_version() -> tuple[int, ...]:
    import paddle
    return tuple(int(x) for x in str(paddle.__version__).split(".")[:3] if x.isdigit())


def require_paddle() -> None:
    if not paddle_available():
        raise RuntimeError(
            "paddlepaddle is not installed (no wheel for this Mac/Python); "
            "run PP-StructureV3 / PaddleOCR-VL on the Linux/CUDA host.")
    try:
        import paddle
        if paddle.device.is_compiled_with_cuda():
            import subprocess
            smi = subprocess.run(["nvidia-smi", "--query-gpu=name",
                                  "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=30)
            if "blackwell" in (smi.stdout or "").casefold() and paddle_version() < (3, 2, 1):
                raise RuntimeError(
                    f"Blackwell GPU needs paddlepaddle-gpu>=3.2.1 (cu129); "
                    f"found {paddle.__version__}. See SETUP.md.")
    except RuntimeError:
        raise
    except Exception:
        pass


def _pipeline(name: str):
    from paddlex import create_pipeline  # type: ignore
    import os as _os
    device = _os.environ.get("PADDLE_DEVICE", "gpu")
    try:
        return create_pipeline(pipeline=name, device=device)
    except Exception:
        return create_pipeline(pipeline=name, device="cpu")


_S3 = None
_VL = None


def _s3():
    global _S3
    if _S3 is None:
        _S3 = _pipeline("PP-StructureV3")
    return _S3


def _vl():
    global _VL
    if _VL is None:
        import os as _os
        from paddleocr import PaddleOCRVL  # type: ignore
        ver = _os.environ.get("PADDLE_VL_VERSION", "v1.6")
        _VL = PaddleOCRVL(pipeline_version=ver)
    return _VL


_OCR = None


def ocr_engine():
    """Shared PP-OCR engine (latest rec/det, v6 line in PaddleOCR 3.7)."""
    require_paddle()
    global _OCR
    if _OCR is None:
        import os as _os
        from paddleocr import PaddleOCR  # type: ignore
        _OCR = PaddleOCR(device=_os.environ.get("PADDLE_DEVICE", "gpu"))
    return _OCR


_HW = None


def hw_engine():
    """Crop-tuned PP-OCRv6 engine for handwriting annotation crops.

    The document preprocessor (orientation classify + UVDoc unwarping) is
    designed for full pages: on small crops it distorts strokes and the v6
    recognizer collapses (0/30 exact, CER 0.98 in the RTX6000 eval). With
    the preprocessor disabled the same engine reads script handwriting at
    ~1.0 confidence, so the handwriting leg must use this engine, never the
    shared page engine.
    """
    require_paddle()
    global _HW
    if _HW is None:
        import os as _os
        from paddleocr import PaddleOCR  # type: ignore
        _HW = PaddleOCR(
            device=_os.environ.get("PADDLE_DEVICE", "gpu"),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _HW


def _norm(value):
    """Recursively convert numpy arrays/scalars to plain Python types."""
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            return value
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def _as_dict(page):
    if isinstance(page, dict):
        return _norm(dict(page))
    d = {}
    for attr in ("markdown", "layout", "texts", "boxes", "tables", "parsing_results",
                 "parsing_res_list", "ocr", "rec_texts", "rec_boxes", "overall_ocr_res"):
        if hasattr(page, attr):
            try:
                d[attr] = getattr(page, attr)
            except Exception:
                pass
    if hasattr(page, "get"):
        try:
            d.update(page)
        except Exception:
            pass
    return _norm(d)


def _box_list(items, default_label: str) -> list[dict]:
    out = []
    for b in items or []:
        if isinstance(b, str):
            out.append({"label": default_label, "bbox": None, "text": b, "score": None})
        elif isinstance(b, dict):
            out.append({
                "label": b.get("label") or b.get("type") or default_label,
                "bbox": b.get("bbox") or b.get("box") or b.get("coordinate"),
                "text": b.get("text") or b.get("content") or "",
                "score": b.get("score") or b.get("confidence"),
            })
        else:
            # PaddleOCRVLBlock and similar result objects (attribute access).
            get = lambda *names: next(
                (getattr(b, n) for n in names if getattr(b, n, None) is not None), None)
            out.append({
                "label": get("label", "type") or default_label,
                "bbox": get("bbox", "box", "coordinate"),
                "text": get("text", "content") or "",
                "score": get("score", "confidence"),
            })
    return out


def _blocks_of(d: dict) -> list[dict]:
    """Normalize PP-StructureV3 / VL page dicts (verified 2026-10-02, cu129).

    S3 top level: layout_det_res.boxes, region_det_res.boxes,
    overall_ocr_res (rec_texts/rec_boxes/rec_polys/rec_scores),
    table_res_list (cell_box_list + table_ocr_pred).
    """
    blocks: list[dict] = []
    for key in ("layout_det_res", "region_det_res"):
        sub = d.get(key)
        if isinstance(sub, dict):
            blocks.extend(_box_list(sub.get("boxes"), key))
    ocr = d.get("overall_ocr_res")
    if isinstance(ocr, dict):
        texts = ocr.get("rec_texts", []) or []
        boxes = ocr.get("rec_boxes", []) or ocr.get("rec_polys", []) or []
        scores = ocr.get("rec_scores", []) or []
        for i, t in enumerate(texts):
            blocks.append({"label": "ocr_text",
                           "bbox": boxes[i] if i < len(boxes) else None,
                           "text": t,
                           "score": scores[i] if i < len(scores) else None})
    for ti, table in enumerate(d.get("table_res_list", []) or []):
        if not isinstance(table, dict):
            continue
        cells = table.get("cell_box_list", []) or []
        pred = table.get("table_ocr_pred", {}) or {}
        ctexts = pred.get("rec_texts", []) or []
        for ci, cb in enumerate(cells):
            blocks.append({"label": f"table_{ti}_cell",
                           "bbox": cb,
                           "text": ctexts[ci] if ci < len(ctexts) else "",
                           "score": None})
    # PaddleOCR-VL 1.6 page shape: parsing_res_list of PaddleOCRVLBlock.
    prl = d.get("parsing_res_list")
    if isinstance(prl, list):
        blocks.extend(_box_list(prl, "vl_block"))
    # Generic fallbacks (older/alternate result shapes).
    for key in ("layout", "parsing_results", "blocks", "ocr"):
        items = d.get(key)
        if isinstance(items, dict):
            items = items.get("blocks", items.get("results", []))
        if isinstance(items, list):
            blocks.extend(_box_list(items, key))
    texts = d.get("texts") or d.get("rec_texts") or []
    boxes = d.get("boxes") or d.get("rec_boxes") or []
    for i, t in enumerate(texts):
        blocks.append({"label": "text", "bbox": boxes[i] if i < len(boxes) else None,
                       "text": t, "score": None})
    return blocks


def _markdown_of(d: dict, blocks: list[dict]) -> str:
    md = d.get("markdown")
    if isinstance(md, str) and md.strip():
        return md
    if isinstance(md, dict):
        for v in md.values():
            if isinstance(v, str) and v.strip():
                return v
    return "\n".join(b["text"] for b in blocks if b["text"])


def _ordered_ocr_text(d: dict) -> str:
    """Full-page OCR lines in reading order (adjacent-deduped).

    PP-StructureV3 returns overlapping text legs (region boxes, table cells,
    overall OCR). Joining all of them repeats table text ~3x in detection
    order (1555 chars vs 421 GT, CER > 2 on invoices). The transcription
    uses overall_ocr_res lines only, sorted top-to-bottom/left-to-right;
    the structure legs stay in `blocks` for evidence.
    """
    ocr = d.get("overall_ocr_res")
    if not isinstance(ocr, dict):
        return ""
    texts = ocr.get("rec_texts", []) or []
    boxes = ocr.get("rec_boxes", []) or ocr.get("rec_polys", []) or []
    lines = []
    for i, t in enumerate(texts):
        if not (t or "").strip():
            continue
        x, y = 0.0, 0.0
        if i < len(boxes):
            try:
                pts = boxes[i]
                if isinstance(pts[0], (list, tuple)):
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                else:
                    xs, ys = [pts[0], pts[2]], [pts[1], pts[3]]
                x, y = min(xs), min(ys)
            except Exception:
                pass
        lines.append((y, x, t.strip()))
    lines.sort(key=lambda l: (round(l[0] / 10), l[1]))
    out = []
    for _, _, t in lines:
        if not out or out[-1] != t:
            out.append(t)
    return "\n".join(out)


def _s3_pages(path: str) -> list[tuple[str, list[dict]]]:
    pages = []
    for raw in _s3().predict(path):
        d = _as_dict(raw)
        pages.append((_ordered_ocr_text(d) or _markdown_of(d, _blocks_of(d)),
                      _blocks_of(d)))
    return pages


def _vl_pages(path: str, **kwargs) -> list[str]:
    out = []
    for raw in _vl().predict(path, **kwargs):
        d = _as_dict(raw)
        out.append(_markdown_of(d, _blocks_of(d)))
    return out


def layout_and_tables(path: str) -> dict:
    """PP-StructureV3: regions, tables, cells, reading order with bboxes."""
    require_paddle()
    regions, tables, order, texts = [], [], [], []
    for md, blocks in _s3_pages(path):
        texts.append(md)
        for b in blocks:
            order.append(b)
            (tables if "table" in str(b["label"]).casefold() else regions).append(b)
    return {"regions": regions, "tables": tables, "reading_order": order,
            "markdown": "\n\n".join(texts), "page_markdowns": texts}


def vl_page_markdown(path: str, **kwargs) -> str:
    """PaddleOCR-VL markdown for one document (candidate generation input)."""
    require_paddle()
    return "\n\n".join(p for p in _vl_pages(path, **kwargs) if p)


def _vl_enabled() -> bool:
    import os as _os
    return _os.environ.get("INVOICE_VL", "off").lower() in {"1", "on", "true", "yes"}


def pp_structure_pages(path: str) -> list:
    """PP-StructureV3 (+ optional PaddleOCR-VL) pages as ingest.Page records.

    Structure comes from PP-StructureV3 (layout/tables/reading order).
    VL markdown is used only when INVOICE_VL=on: the native VL backend
    hung on the Blackwell/cu129 stack (spins inside the first ``generate``
    forward, paddle 3.2.1; faulthandler trace in work/vl_hang_trace.txt),
    and a hang cannot be caught by try/except. On Mac CPU (paddle 3.3.1)
    VL runs but is slow (~80 s/page), so it stays opt-in; for production
    speed use the mlx-vlm-server backend per Paddle's Apple Silicon guide.
    """
    require_paddle()
    from .ingest import Page
    struct = layout_and_tables(path)
    vl_pages: list[str] = []
    if _vl_enabled():
        try:
            vl_pages = _vl_pages(path)
        except Exception:
            vl_pages = []
    md_pages = struct["page_markdowns"]
    pages = []
    for i, md in enumerate(md_pages):
        text = vl_pages[i] if i < len(vl_pages) and vl_pages[i].strip() else md
        source = "pp-structurev3+paddleocr-vl" if text is not md else "pp-structurev3"
        pages.append(Page(i + 1, text.strip(), (md or text).strip(), source,
                          [f"regions={len(struct['regions'])}",
                           f"tables={len(struct['tables'])}"]))
    return pages


def vl_extract(image_path: str, prompt: str = "", **kwargs) -> str:
    """PaddleOCR-VL visual extraction (candidate generation).

    The VL pipeline produces layout-aware markdown; `prompt` selects the
    block-label focus where supported (prompt_label), else full markdown.
    """
    require_paddle()
    if prompt:
        kwargs.setdefault("prompt_label", prompt)
    return vl_page_markdown(image_path, **kwargs)


def ocr_only(image_path: str) -> list[dict]:
    """PP-OCRv6 text + boxes (higher-precision OCR leg for dual-path checks)."""
    ocr = ocr_engine()
    out = []
    for page in ocr.predict(image_path):
        texts = page.get("rec_texts", []) if isinstance(page, dict) else []
        boxes = page.get("rec_boxes", []) if isinstance(page, dict) else []
        scores = page.get("rec_scores", []) if isinstance(page, dict) else []
        for t, b, s in zip(texts, boxes, scores):
            out.append({"text": t, "bbox": list(b) if hasattr(b, "tolist") else b,
                        "score": float(s)})
    return out
