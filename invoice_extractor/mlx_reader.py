"""MLX reader backend: serve a fine-tuned Qwen3 reader on Apple Silicon.

Selected via READER_BACKEND=mlx (default is ollama). Weights:
  MLX_READER_MODEL   (default: mlx-community/Qwen3-4B-4bit)
  MLX_READER_ADAPTER (default: checkpoints/reader-qwen3-4b)
Generation is greedy (temp 0) with a JSON-repair fallback; schema
conformance is enforced downstream by the evidence guard, same as Ollama.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_model = None
_tokenizer = None


def _load():
    global _model, _tokenizer
    if _model is None:
        from mlx_lm import load

        model_id = os.environ.get("MLX_READER_MODEL", "mlx-community/Qwen3-4B-4bit")
        adapter = os.environ.get("MLX_READER_ADAPTER", str(ROOT / "checkpoints" / "reader-qwen3-4b"))
        kw = {"adapter_path": adapter} if Path(adapter).exists() else {}
        _model, _tokenizer = load(model_id, **kw)
    return _model, _tokenizer


# Must match scripts/build_reader_items.py: the fine-tune only ever saw this
# prompt shape, so inference must use it verbatim (no SYSTEM_PROMPT).
MLX_PROMPT = ("You read the raw text of one invoice and return a JSON object.\n\n"
              "INVOICE TEXT:\n\n{document_text}\n\nReturn the JSON now.")


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Last resort: first balanced {...} span, with literal control chars
    # sanitized (small models emit raw newlines inside strings).
    start = text.find("{")
    if start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    span = re.sub(r"[\x00-\x1f]", " ", text[start:i + 1])
                    return json.loads(span)
    raise RuntimeError(f"MLX reader returned invalid JSON: {text[:200]!r}")


def call_mlx(prompt: str, system: str = "", max_tokens: int = 4096) -> dict:
    """Generate a JSON object from a document prompt. Never returns prose."""
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = _load()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError:
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    out = generate(model, tokenizer, prompt=text, max_tokens=max_tokens,
                   verbose=False, sampler=make_sampler(temp=0.0))
    return _extract_json(out)


def available() -> bool:
    try:
        _load()
        return True
    except Exception:
        return False
