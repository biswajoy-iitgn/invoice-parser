#!/usr/bin/env python3
"""GPU-side paddle transcription (runs on the L4 studio).

Reads a manifest of {id, cond, file} rows, runs PP-StructureV3 (backend=s3)
or PP-StructureV3+PaddleOCR-VL (backend=vl) ingest, writes one JSON row per
doc with serializable pages + ingest seconds. The Mac side
(scripts/track_from_transcriptions.py) runs the identical reader + metrics.

Usage (studio, PADDLE_DEVICE=gpu):
  python scripts/paddle_transcribe.py --manifest /tmp/manifest.jsonl \\
      --images /tmp/images --out /tmp/transcribed_s3.jsonl --backend s3
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE / "invoice_extractor").exists() else HERE.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PADDLE_DEVICE", "gpu")
os.environ.setdefault("INVOICE_VL", "off")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", choices=["s3", "vl"], default="s3")
    a = ap.parse_args()

    if a.backend == "vl":
        os.environ["INVOICE_VL"] = "on"
    from invoice_extractor.paddle_backends import paddle_available, pp_structure_pages
    if not paddle_available():
        raise SystemExit("paddlepaddle not importable in this interpreter")
    print(f"transcribe backend={a.backend} device={os.environ['PADDLE_DEVICE']}",
          flush=True)

    rows = [json.loads(l) for l in open(a.manifest) if l.strip()]
    done = set()
    if Path(a.out).exists():
        done = {json.loads(l)["key"] for l in open(a.out) if l.strip()}
        print(f"resuming: {len(done)} done", flush=True)
    with open(a.out, "a") as f:
        for i, r in enumerate(rows):
            if r["key"] in done:
                continue
            t0 = time.time()
            try:
                pages = pp_structure_pages(str(Path(a.images) / r["file"]))
                err = ""
            except Exception as exc:
                pages, err = [], f"{type(exc).__name__}: {exc}"[:300]
            dt = time.time() - t0
            f.write(json.dumps({
                "key": r["key"], "id": r["id"], "cond": r["cond"],
                "backend": a.backend, "ingest_s": round(dt, 2), "error": err,
                "pages": [{"number": p.number, "text": p.text,
                           "raw_text": p.raw_text, "source": p.source,
                           "notes": p.notes} for p in pages],
            }) + "\n")
            f.flush()
            nch = sum(len(p.text) for p in pages)
            print(f"[{a.backend}] {i+1}/{len(rows)} {r['key']} chars={nch} "
                  f"dt={dt:.1f}s {err[:80]}", flush=True)


if __name__ == "__main__":
    main()
