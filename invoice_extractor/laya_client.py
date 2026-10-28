"""LAYA semantic layer: field classification over the invoice ontology.

Primary: the invoice fine-tuned LAYA checkpoint
(`checkpoints/laya-invoice/laya-invoice-ft`, trained by
training/train_laya.py from convaiinnovations/laya). Override with
LAYA_MODEL_DIR (a local fine-tuned dir or any Hub id Router accepts).

Fallback: deterministic ontology matcher (token overlap + abbreviation
boost). The extraction loop only ever receives charge fields from here;
header-field votes are constrained by the caller.

This module replaced the CLM client: same Choice-over-ontology contract,
local Apache-2.0 weights, no vLLM/server dependency.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_DIR = ROOT / "checkpoints" / "laya-invoice" / "laya-invoice-ft"

_ONTOLOGY: dict | None = None
_ROUTER = None
# Router model keys must be known names; override the "english" slot with the
# fine-tuned checkpoint path (local paths are accepted as values).
_ROUTER_MODEL = "english"


def ontology() -> dict:
    global _ONTOLOGY
    if _ONTOLOGY is None:
        _ONTOLOGY = json.loads((ROOT / "configs" / "field_ontology.json").read_text())
    return _ONTOLOGY


def _tokens(s: str) -> set[str]:
    import re as _re
    return set(_re.findall(r"[a-z0-9/]+", s.casefold()))


def classify_deterministic(label: str, onto: dict | None = None) -> dict:
    """Offline ontology matcher: token overlap + abbreviation boost, no weights."""
    onto = onto or ontology()
    lab, lab_raw = _tokens(label), label.casefold()
    scored: dict[str, float] = {}
    for name, spec in onto.items():
        best = 0.0
        for alias in [name, *spec["aliases"]]:
            at = _tokens(alias)
            if not at:
                continue
            inter = len(lab & at)
            score = inter / len(at) + 0.5 * inter / max(len(lab), 1)
            if lab_raw == alias.casefold():
                score += 2.0
            best = max(best, score)
        for abbr in spec.get("abbreviations", []):
            if abbr.casefold() in lab_raw.split():
                best += 1.5
        for neg in spec.get("negative_examples", []):
            if _tokens(neg) and _tokens(neg) <= lab:
                best -= 1.0
        scored[name] = best
    best = max(scored, key=scored.get)
    return {"field": best, "scores": scored}


def criteria() -> dict:
    onto = ontology()
    return {n: s["semantic_description"] + ". Aliases: " + ", ".join(s["aliases"][:6])
            for n, s in onto.items()}


def _router():
    global _ROUTER
    if _ROUTER is None:
        from laya import Router
        model_dir = os.environ.get("LAYA_MODEL_DIR", str(DEFAULT_MODEL_DIR))
        if not Path(model_dir).exists():
            raise FileNotFoundError(f"LAYA checkpoint missing: {model_dir}")
        _ROUTER = Router(models={_ROUTER_MODEL: model_dir})
    return _ROUTER


def laya_available() -> bool:
    try:
        _router()
        return True
    except Exception:
        return False


def semantic_field(label: str, context: str = "") -> tuple[str, str]:
    """Fast single-field vote. Returns (field, backend). Never raises."""
    try:
        router = _router()
        state = label if not context else f"{label}\nContext: {context}"
        r = router.predict(
            state,
            {"field": {"type": "choice",
                       "instructions": ("Which canonical invoice field does "
                                        "this document line describe?"),
                       "criteria": criteria()}},
            model=_ROUTER_MODEL,
        )
        field = r["answers"]["field"]["choice"]
        if field in ontology():
            return field, "laya-finetuned"
    except Exception:
        pass
    try:
        return classify_deterministic(label, ontology())["field"], "deterministic"
    except Exception:
        return "Other Charges", "semantic-error"


def classify_label(label: str, context: str = "") -> dict:
    """Full classification with backend provenance (analysis/ablation use)."""
    field, backend = semantic_field(label, context)
    return {"field": field, "backend": backend}
