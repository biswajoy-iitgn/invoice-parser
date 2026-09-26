"""OCR engines behind one interface: image (BGR ndarray) -> word boxes.

  tesseract  Tesseract 5 LSTM, sparse-text mode (psm 11). Best token and digit recall on the
             benchmark's clean/medium tiers and best on 150-DPI scans after preprocessing.
  rapidocr   PP-OCRv4 det+rec as ONNX (weights ship inside the PyPI wheel). Strong digits,
             but the Chinese-English recogniser drops inter-word spaces.
  hunyuan    HunyuanOCR via llama.cpp (Apple Silicon); returns text only, so it can feed the
             LLM backend but not the layout parser.

Preprocessing (all engines): projection-profile deskew, background (illumination) normalisation,
non-local-means denoising when the estimated noise is high, and upscaling to ~2600 px width.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .layout import Word

TARGET_WIDTH = 2600
MAX_UPSCALE = 2.2


def estimate_skew(gray: np.ndarray) -> float:
    s = 1000 / gray.shape[1]
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    b = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = b.shape

    def score(a: float) -> float:
        m = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1)
        r = cv2.warpAffine(b, m, (w, h), flags=cv2.INTER_NEAREST)
        p = r.sum(1).astype(np.float64)
        return float(((p[1:] - p[:-1]) ** 2).sum())

    best = max((score(a), a) for a in np.arange(-5, 5.01, 0.2))
    a0 = best[1]
    best = max((score(a), a) for a in np.arange(a0 - 0.2, a0 + 0.21, 0.04))
    return float(best[1])


def noise_sigma(gray: np.ndarray) -> float:
    """Immerkaer's fast noise variance estimator."""
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
    conv = cv2.filter2D(gray.astype(np.float32), -1, k)
    h, w = gray.shape
    return float(np.sqrt(np.pi / 2) * np.abs(conv[1:-1, 1:-1]).sum() / (6 * (w - 2) * (h - 2)))


def preprocess(img: np.ndarray) -> tuple[np.ndarray, dict]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    meta: dict = {}
    angle = estimate_skew(gray)
    if abs(angle) > 0.1:
        h, w = gray.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
        gray = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
    meta["deskew_deg"] = round(angle, 2)
    sigma = noise_sigma(gray)
    meta["noise_sigma"] = round(sigma, 2)
    if sigma > 4:
        gray = cv2.fastNlMeansDenoising(gray, None, float(min(15, 1.2 * sigma)), 7, 21)
        meta["denoised"] = True
    scale = float(np.clip(TARGET_WIDTH / gray.shape[1], 1.0, MAX_UPSCALE))
    if scale > 1.05:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    meta["upscale"] = round(scale, 2)
    # white-on-dark bands (table headers, banners): closing fills the white glyph strokes and keeps the
    # band's outer edge exact; only solid components (>= 60% dark) are inverted, never ordinary black text
    mask = dark_bands(gray)
    if mask.any():
        gray = np.where(mask, 255 - gray, gray).astype(np.uint8)
        meta["inverted_dark_fraction"] = round(float(mask.mean()), 3)
    k = max(15, int(gray.shape[1] / 60) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    gray = np.clip(gray.astype(np.float32) / np.maximum(bg, 1) * 255, 0, 255).astype(np.uint8)
    return gray, meta


def remove_rules(gray: np.ndarray) -> np.ndarray:
    """Erase long table rules: Tesseract reads them as junk words and inflates boxes of touching text."""
    ink = (cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 25) > 0).astype(np.uint8)
    w = gray.shape[1]
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25), 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, w // 35))))
    rules = cv2.dilate(horiz | vert, np.ones((3, 3), np.uint8)).astype(bool)
    out = gray.copy()
    out[rules] = 255
    return out


def dark_bands(gray: np.ndarray) -> np.ndarray:
    d = (gray < 110).astype(np.uint8)
    k = max(9, int(0.012 * gray.shape[1]) | 1)
    closed = cv2.morphologyEx(d, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=4)
    mask = np.zeros(gray.shape, dtype=bool)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 3 * k or h < 0.8 * k:
            continue
        comp = lab[y:y + h, x:x + w] == i
        # per pixel row: a band row is mostly dark across the component (a text line never is)
        width = comp.sum(1)
        ratio = (d[y:y + h, x:x + w].astype(bool) & comp).sum(1) / np.maximum(width, 1)
        rows = (ratio >= 0.55) & (width >= 3 * k)
        if rows.sum() >= 0.8 * k:
            mask[y:y + h, x:x + w] |= comp & rows[:, None]
    return mask


def tesseract_available() -> bool:
    return bool(shutil.which("tesseract"))


def tesseract_words(gray: np.ndarray, psm: int = 11) -> list[Word]:
    ok, buf = cv2.imencode(".png", gray)
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", str(psm), "--oem", "1", "-l", "eng", "--dpi", "300", "tsv"],
        input=buf.tobytes(), capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed: {proc.stderr.decode()[-300:]}")
    words = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        text, conf = parts[11].strip(), float(parts[10])
        if not text or conf < 0:
            continue
        x, y, w, h = map(float, parts[6:10])
        words.append(Word(text, x, y, x + w, y + h, conf / 100.0))
    return drop_junk(words)


def drop_junk(words: list[Word]) -> list[Word]:
    """Noise tokens: implausibly flat boxes, or very low confidence non-numeric fragments."""
    if not words:
        return words
    med = float(np.median([w.h for w in words]))
    return [w for w in words
            if w.h >= 0.35 * med and not (w.conf < 0.2 and not re.search(r"\d", w.text))]


_rapid = None


def rapidocr_words(gray: np.ndarray) -> list[Word]:
    global _rapid
    if _rapid is None:
        from rapidocr_onnxruntime import RapidOCR
        _rapid = RapidOCR(max_side_len=4000)
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if gray.ndim == 2 else gray
    result, _ = _rapid(img)
    words = []
    for box, text, score in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        text = text.strip()
        if not text:
            continue
        # split the line box into words proportionally to character offsets
        n = max(1, len(text))
        for m in re.finditer(r"\S+", text):
            wx0 = x0 + (x1 - x0) * m.start() / n
            wx1 = x0 + (x1 - x0) * m.end() / n
            words.append(Word(m.group(0), wx0, y0, wx1, y1, float(score)))
    return words


HUNYUAN_MODEL = Path(os.environ.get("HUNYUAN_GGUF", Path.home() / "models/HunyuanOCR-GGUF/HunyuanOCR-Q8_0.gguf"))
HUNYUAN_MMPROJ = Path(os.environ.get("HUNYUAN_MMPROJ", Path.home() / "models/HunyuanOCR-GGUF/mmproj-HunyuanOCR-Q8_0.gguf"))
# Vendor document-parsing prompt; the model was trained on these exact strings.
HUNYUAN_PARSE_PROMPT = (
    "提取文档图片中正文的所有信息用markdown格式表示，其中页眉、页脚部分忽略，"
    "表格用html格式表达，文档中公式用latex格式表示，按照阅读顺序组织进行解析。"
)
HUNYUAN_TOKEN_JUNK = re.compile(r"<｜hy_[^｜]*｜>(\(\d+,\d+\),\(\d+,\d+\))?")


def hunyuan_available() -> bool:
    return bool(shutil.which("llama-mtmd-cli")) and HUNYUAN_MODEL.exists() and HUNYUAN_MMPROJ.exists()


def hunyuan_text(png_path: str, timeout: float = 600.0) -> str:
    if not hunyuan_available():
        raise RuntimeError(f"HunyuanOCR unavailable (need llama-mtmd-cli and {HUNYUAN_MODEL})")
    proc = subprocess.run(
        ["llama-mtmd-cli", "-m", str(HUNYUAN_MODEL), "--mmproj", str(HUNYUAN_MMPROJ),
         "--image", png_path, "-p", HUNYUAN_PARSE_PROMPT, "-ngl", "99", "-c", "8192",
         "--temp", "0", "--repeat-penalty", "1.08", "-n", "4096", "--no-warmup"],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"llama-mtmd-cli failed: {(proc.stderr or '')[-400:]}")
    return HUNYUAN_TOKEN_JUNK.sub("", proc.stdout).strip()


# Readings: an engine plus whether table rules are erased first. Rule erasure helps Tesseract on bordered
# tables and hurts it on monospace/dot-matrix layouts, so the ensemble keeps both and lets verification decide.
READINGS = {
    "tesseract": (tesseract_words, True),
    "tesseract-raw": (tesseract_words, False),
    "rapidocr": (rapidocr_words, False),
}
ENSEMBLE = ["tesseract", "tesseract-raw"]
ENSEMBLE_FALLBACK = ["rapidocr"]


def ocr_readings(img: np.ndarray, readings: list[str]) -> dict[str, tuple[list[Word], int, int, dict]]:
    gray, meta = preprocess(img)
    ruled = None
    out = {}
    for name in readings:
        fn, rules = READINGS[name]
        if rules and ruled is None:
            ruled = remove_rules(gray)
        g = ruled if rules else gray
        try:
            words = fn(g)
            note = {}
        except subprocess.TimeoutExpired:
            # a pathological page (e.g. speckle noise) must not fail the document; other readings still count
            words, note = [], {"timeout": True}
        out[name] = (words, g.shape[1], g.shape[0], {**meta, "reading": name, **note})
    return out


def ocr_image(img: np.ndarray, engine: str = "tesseract") -> tuple[list[Word], int, int, dict]:
    return ocr_readings(img, [engine])[engine]
