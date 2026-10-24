#!/usr/bin/env python3
"""Batch PP-OCRv6 over a request list with N worker processes (GPU or CPU), resumable.

Each worker is invoice_extractor/ppocr_worker.py running under the Paddle interpreter.
Requests: JSONL {"id", "image"}; results appended to OUT as JSONL (one line per page).
Pages already present in OUT are skipped, so the run can be restarted after interruption.

  python scripts/run_ppocr_batch.py requests.jsonl ocr_results.jsonl \
      --python /teamspace/studios/this_studio/venv-paddle/bin/python --workers 4 --device gpu
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1] / "invoice_extractor" / "ppocr_worker.py"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("requests")
    ap.add_argument("out")
    ap.add_argument("--python", default=os.path.expanduser("~/paddle-env/bin/python"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--device", default="gpu")
    a = ap.parse_args()
    done = set()
    if Path(a.out).exists():
        for line in open(a.out):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass
    reqs = [json.loads(l) for l in open(a.requests) if l.strip()]
    todo = [r for r in reqs if r["id"] not in done]
    print(f"{len(reqs)} requests, {len(done)} done, {len(todo)} to go", flush=True)
    q: queue.Queue = queue.Queue()
    for r in todo:
        q.put(r)
    lock = threading.Lock()
    out = open(a.out, "a")
    t0, counter = time.time(), [0]

    def serve(k: int) -> None:
        env = dict(os.environ, PADDLE_DEVICE=a.device)
        p = subprocess.Popen([a.python, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
        while True:
            line = p.stdout.readline()
            if not line:
                raise RuntimeError(f"worker {k} died during startup")
            if line.startswith("{") and '"ready"' in line:
                break
        while True:
            try:
                r = q.get_nowait()
            except queue.Empty:
                break
            p.stdin.write(json.dumps(r) + "\n")
            p.stdin.flush()
            res = p.stdout.readline()
            if not res:
                # worker crashed: restart it and requeue
                q.put(r)
                p = subprocess.Popen([a.python, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
                while '"ready"' not in (p.stdout.readline() or '"ready"'):
                    pass
                continue
            with lock:
                out.write(res if res.endswith("\n") else res + "\n")
                out.flush()
                counter[0] += 1
                if counter[0] % 250 == 0:
                    rate = counter[0] / (time.time() - t0)
                    print(f"{counter[0]}/{len(todo)} pages  {rate:.2f} pages/s  eta {(len(todo) - counter[0]) / rate / 60:.1f} min",
                          flush=True)
        p.stdin.close()
        p.wait(timeout=60)

    threads = [threading.Thread(target=serve, args=(k,), daemon=True) for k in range(a.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.close()
    print(f"OCR_DONE {counter[0]} pages in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
