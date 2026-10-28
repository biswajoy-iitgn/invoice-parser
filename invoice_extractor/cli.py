"""Read documents from the command line, the same way the service does: each file under a profile ("auto": the router picks),
with the models started on demand and stopped at the end.

    python -m invoice_extractor DOC.pdf [MORE.pdf | FOLDER ...] [--profile gate_pass] [--out-dir output]
    python -m invoice_extractor --list-profiles
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .pipeline import IMAGE_EXTS

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


def main(argv: list[str] | None = None) -> int:
    from .profiles import AUTO, extract, get, load_all

    ap = argparse.ArgumentParser(prog="invoice_extractor", description="Read documents under a profile; every value cited to the page.")
    ap.add_argument("inputs", nargs="*", help="PDF/image files or folders")
    ap.add_argument("-o", "--out-dir", default="output", help="where to write JSON (default: ./output)")
    ap.add_argument("--profile", default=AUTO, help="profile id from configs/profiles/ (default: auto, the router picks)")
    ap.add_argument("--model", default=None, help="reader id from configs/models.yaml (default: the profile's)")
    ap.add_argument("--force-ocr", action="store_true", help="OCR every page even when a text layer exists")
    ap.add_argument("--no-audit", action="store_true", help="do not write the *.audit.json sidecar files")
    ap.add_argument("--list-profiles", action="store_true", help="show the available profiles and exit")
    args = ap.parse_args(argv)

    if args.list_profiles:
        for p in load_all().values():
            print(f"{p.id:20} {p.engine:8} {len(p.fields)} fields, {len(p.tables)} tables  {p.name}")
        return 0
    files = _collect(args.inputs)
    if not files:
        print("no input documents found", file=sys.stderr)
        return 1
    profile = args.profile if args.profile == AUTO else get(args.profile).id
    os.makedirs(args.out_dir, exist_ok=True)
    failures = 0
    try:
        for path in files:
            name = os.path.basename(path)
            try:
                res, audit = extract(path, profile, model=args.model, force_ocr=args.force_ocr)
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc}", file=sys.stderr)
                continue
            stem = os.path.splitext(name)[0]
            with open(os.path.join(args.out_dir, f"{stem}.json"), "w") as fh:
                json.dump(res, fh, indent=2, ensure_ascii=False)
            if not args.no_audit:
                with open(os.path.join(args.out_dir, f"{stem}.audit.json"), "w") as fh:
                    json.dump(audit, fh, indent=2, ensure_ascii=False, default=str)
            print(f"[{res.get('decision', 'done'):6}] {name}  ({res['route']['profile']}, {res['route']['reader']})")
            for r in res.get("reasons", []):
                print(f"          - {r}")
    finally:
        from .model_pool import pool

        pool().stop_all()
    return 1 if failures else 0
