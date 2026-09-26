from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from .extract import BACKENDS, extract_invoice
from .llm import DEFAULT_MODEL
from .ingest import IMAGE_EXTS

SUPPORTED_EXTS = {".pdf"} | IMAGE_EXTS


def _collect(inputs: list[str]) -> list[str]:
    files = []
    for p in inputs:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if os.path.splitext(name)[1].lower() in SUPPORTED_EXTS:
                    files.append(os.path.join(p, name))
        elif os.path.isfile(p):
            files.append(p)
        else:
            print(f"skip: {p} not found", file=sys.stderr)
    return files


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="invoice_extractor",
        description="Extract freight/logistics invoice fields to JSON.",
    )
    ap.add_argument("inputs", nargs="+", help="PDF/image files or folders")
    ap.add_argument("-o", "--out-dir", default="output", help="where to write JSON (default: ./output)")
    ap.add_argument("--backend", default="layout", choices=BACKENDS,
                    help="layout (default, CPU, grounded) | layout+llm (LLM classifies unplaced charge lines) | llm")
    ap.add_argument("--ocr", default="ensemble", choices=["ensemble", "tesseract", "tesseract-raw", "rapidocr", "hunyuan"],
                    help="OCR for raster pages (default: ensemble = Tesseract x2 + PP-OCRv4 with verification-based "
                         "selection); hunyuan only feeds --backend llm")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model for LLM backends (default: {DEFAULT_MODEL})")
    ap.add_argument("--force-ocr", action="store_true", help="OCR every page even when a text layer exists")
    ap.add_argument("--no-audit", action="store_true", help="do not write the *.audit.json sidecar files")
    args = ap.parse_args(argv)

    files = _collect(args.inputs)
    if not files:
        print("no input documents found", file=sys.stderr)
        return 1
    os.makedirs(args.out_dir, exist_ok=True)

    cache: dict[str, tuple[dict, dict]] = {}
    combined = {}
    failures = 0
    for path in files:
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0]
        digest = _sha256(path)
        try:
            if digest in cache:
                fields, audit = cache[digest]
                audit = {**audit, "file": path, "duplicate_of": cache[digest][1]["file"]}
                note = "duplicate, reused result"
            else:
                fields, audit = extract_invoice(path, backend=args.backend, ocr=args.ocr, model=args.model,
                                                force_ocr=args.force_ocr)
                cache[digest] = (fields, audit)
                note = f"{sum(audit['timing_sec'].values()):.1f}s"
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {exc}", file=sys.stderr)
            continue

        with open(os.path.join(args.out_dir, f"{stem}.json"), "w") as fh:
            json.dump(fields, fh, indent=2, ensure_ascii=False)
        if not args.no_audit:
            with open(os.path.join(args.out_dir, f"{stem}.audit.json"), "w") as fh:
                json.dump(audit, fh, indent=2, ensure_ascii=False)
        combined[name] = fields
        flag = "REVIEW" if audit["needs_review"] else "ok"
        print(f"[{flag:6}] {name}  ({note})")
        for w in audit["warnings"]:
            print(f"          - {w}")

    with open(os.path.join(args.out_dir, "all_invoices.json"), "w") as fh:
        json.dump(combined, fh, indent=2, ensure_ascii=False)
    print(f"\n{len(combined)} extracted, {failures} failed -> {os.path.abspath(args.out_dir)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
