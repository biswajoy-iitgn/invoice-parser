"""PaddleOCR-VL document-parsing worker (runs under the Paddle interpreter, see vl_client.py).

Reads one JSON request per line on stdin ({"image": path}); writes one JSON line per request:
{"width", "height", "elapsed", "blocks": [{"id", "label", "order", "bbox": [x0, y0, x1, y1], "content"}]}.
"""
from __future__ import annotations

import json
import os
import sys
import time


def main() -> None:
    real_out = sys.stdout
    sys.stdout = sys.stderr                      # the library prints progress: keep the reply channel clean
    version = os.environ.get("PADDLEOCR_VL_VERSION", "v1.6")
    from paddleocr import PaddleOCRVL

    server = os.environ.get("VL_SERVER_URL")
    if server:   # the VLM runs on the model pool's GPU server; layout detection stays here on CPU (as benchmarked)
        vl = PaddleOCRVL(pipeline_version=version, vl_rec_backend="mlx-vlm-server", vl_rec_server_url=server,
                         vl_rec_api_model_name=os.environ["VL_SERVER_MODEL"], use_doc_orientation_classify=False,
                         use_doc_unwarping=False, device="cpu")
    else:
        vl = PaddleOCRVL(pipeline_version=version)
    # seal recognition stays off (the shipped default): its own seal-OCR models doubled the page time and misread a stamp the
    # VLM read in full from a crop; the layout step did not find the stamp on a phone photo (reports/reader_bakeoff.md)

    def reply(obj: dict) -> None:
        real_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        real_out.flush()

    reply({"ready": True, "model": f"PaddleOCR-VL {version.lstrip('v')}"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            t0 = time.time()
            res = list(vl.predict(req["image"]))[0]
            j = res.json if isinstance(res.json, dict) else json.loads(res.json)
            j = j.get("res", j)
            blocks = [{"id": b.get("block_id"), "label": b.get("block_label"), "order": b.get("block_order"),
                       "bbox": [float(v) for v in b.get("block_bbox", [])], "content": str(b.get("block_content") or "")}
                      for b in j.get("parsing_res_list", [])]
            reply({"width": j.get("width"), "height": j.get("height"), "elapsed": round(time.time() - t0, 2),
                   "blocks": blocks})
        except Exception as exc:
            reply({"error": f"{type(exc).__name__}: {exc}"[:300]})


if __name__ == "__main__":
    main()
