#!/usr/bin/env python3
"""Debug PaddleOCR-VL native hang: placement, dtypes, tiny embed/generate."""
import time

import paddle

print("cuda_compiled:", paddle.device.is_compiled_with_cuda(), flush=True)
print("cuda_count:", paddle.device.cuda.device_count(), flush=True)
print("default_device:", paddle.device.get_device(), flush=True)

from paddleocr import PaddleOCRVL

vl = PaddleOCRVL()
pipe = vl.paddlex_pipeline
print("pipeline:", type(pipe).__name__, flush=True)

# Walk the object graph to the VLM model.
seen = set()
found = []
stack = [("pipe", pipe)]
while stack and len(found) < 4:
    name, obj = stack.pop()
    if id(obj) in seen:
        continue
    seen.add(id(obj))
    cls = type(obj).__name__
    if cls in ("PaddleOCRVLForConditionalGeneration", "Ernie4_5Model"):
        found.append((name, obj))
        continue
    if len(seen) > 4000:
        break
    try:
        sub = getattr(obj, "__dict__", None)
    except Exception:
        continue
    if isinstance(sub, dict):
        for k, v in sub.items():
            if k.startswith("_") and k not in ("_model", "_models"):
                continue
            if hasattr(v, "__dict__") or isinstance(v, (list, tuple, dict)):
                stack.append((f"{name}.{k}", v))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            stack.append((f"{name}[{i}]", v))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            stack.append((f"{name}[{k}]", v))

for name, model in found:
    print("FOUND", name, type(model).__name__, flush=True)
    try:
        w = model.model.embed_tokens.weight
        print("  embed weight place:", w.place, "dtype:", w.dtype, "shape:", list(w.shape), flush=True)
    except Exception as exc:
        print("  embed inspect failed:", exc, flush=True)
    # Tiny embedding lookup on CPU and GPU.
    for place_name, ids in (("cpu", paddle.to_tensor([[1, 2, 3]], place=paddle.CPUPlace())),
                            ("gpu", paddle.to_tensor([[1, 2, 3]], place=paddle.CUDAPlace(0)))):
        t0 = time.time()
        try:
            with paddle.no_grad():
                out = model.model.embed_tokens(ids)
            print(f"  embed({place_name}) ok {time.time() - t0:.2f}s out_place={out.place}", flush=True)
        except Exception as exc:
            print(f"  embed({place_name}) FAIL: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
print("DEBUG-OK", flush=True)
