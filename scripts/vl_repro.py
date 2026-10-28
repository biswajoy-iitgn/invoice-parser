#!/usr/bin/env python3
"""Minimal PaddleOCR-VL 1.6 repro: one PDF, first page only, hang watchdog.

Writes progress to vl_repro.out; faulthandler dumps a traceback if predict
stalls >240s so a hang leaves evidence instead of silence.
"""
import faulthandler
import sys
import time

faulthandler.dump_traceback_later(240, exit=True)

t0 = time.time()
from paddleocr import PaddleOCRVL

print(f"import {time.time() - t0:.1f}s", flush=True)
t0 = time.time()
vl = PaddleOCRVL()
print(f"VL-READY {time.time() - t0:.1f}s", flush=True)
t0 = time.time()
out = vl.predict(sys.argv[1] if len(sys.argv) > 1 else "sample invoices/DSV.pdf")
for i, p in enumerate(out):
    d = dict(p) if hasattr(p, "keys") else {}
    md = ""
    for k in ("markdown_texts", "markdown", "rec_texts"):
        if k in d:
            v = d[k]
            md = "\n".join(v) if isinstance(v, list) else str(v)
            break
    print(f"PAGE {i} keys={sorted(d.keys())[:10]} md_len={len(md)} "
          f"infer={time.time() - t0:.1f}s", flush=True)
    print("MD-HEAD " + md[:600].replace("\n", " | "), flush=True)
    break
print("VL-OK", flush=True)
