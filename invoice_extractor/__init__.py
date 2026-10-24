"""Invoice extractor: OmniSR + PP-StructureV3 + PaddleOCR-VL + LAYA.

Lazy re-export: importing the package must not require the LLM client
(ollama), the Paddle stack, or LAYA weights. Heavy modules import on first
attribute access so single-leg scripts (handwriting eval, probes) stay light.
"""

__all__ = ["extract_invoice"]


def __getattr__(name: str):
    if name == "extract_invoice":
        from .extract import extract_invoice

        return extract_invoice
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
