"""Transformers backend for the fine-tuned reader (GPU hosts; used for validation runs).

READER_HF_BASE    base model (default Qwen/Qwen3-VL-8B-Instruct)
READER_HF_ADAPTER LoRA adapter dir (default checkpoints/reader-qwen3vl-8b/adapter); empty = base only
Greedy decoding, text-only chat template (the production reader path never sends images).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_STATE: dict = {}
ROOT = Path(__file__).resolve().parents[1]


def _load():
    if "model" in _STATE:
        return _STATE["model"], _STATE["tok"]
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor

    base = os.environ.get("READER_HF_BASE", "Qwen/Qwen3-VL-8B-Instruct")
    adapter = os.environ.get("READER_HF_ADAPTER", str(ROOT / "checkpoints" / "reader-qwen3vl-8b" / "adapter"))
    proc = AutoProcessor.from_pretrained(base)
    model = AutoModelForImageTextToText.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cuda")
    if adapter and Path(adapter).exists():
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter)
        model = model.merge_and_unload()
    model.eval()
    _STATE.update(model=model, tok=proc.tokenizer)
    return model, proc.tokenizer


def generate(messages: list[dict], max_new_tokens: int = 6144) -> str:
    import torch

    with _LOCK:
        model, tok = _load()
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        ids = tok(prompt, return_tensors="pt", add_special_tokens=False).to(model.device)
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False, temperature=None,
                                 top_p=None, top_k=None, repetition_penalty=1.0)
        return tok.decode(out[0, ids["input_ids"].shape[1]:], skip_special_tokens=True)
