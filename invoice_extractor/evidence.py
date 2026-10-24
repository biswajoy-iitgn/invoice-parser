"""Evidence candidates (§8): traceable value hypotheses.

Every non-empty extracted value should map to one Candidate carrying its
source text, location, provenance, and scores. Candidates are built from the
extraction audit (line items + header fields); the hallucination guard
(validat ion.ground_fields) decides support, and the confidence router
decides the route.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Candidate:
    canonical_field: str
    value: str
    confidence: float = 0.0
    source_text: str = ""
    page: int = 0
    bbox: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    source_type: str = "OCR"  # OCR|VLM|LAYA|SR|DERIVED
    model_version: str = ""
    evidence_score: float = 0.0
    semantic_score: float = 0.0
    validation_score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _score_flag(ok: bool) -> float:
    return 1.0 if ok else 0.0


def build_candidates(fields: dict, audit: dict, model_version: str = "",
                     citations: dict | None = None) -> list[Candidate]:
    """Build one Candidate per non-empty field from extraction outputs.

    Line-item charges carry LAYA/lexicon provenance from the audit; header
    fields carry VLM provenance with evidence support from the guard inputs.
    Citation boxes come from `citations` (extract.py audit, measured OCR /
    text-layer hits); fields without a citation keep a zero bbox and must
    not auto-accept on critical fields.
    """
    from . import ontology as _onto
    from .validation import supported, supported_date, supported_number, supported_rate
    from .schema import CHARGE_FIELDS

    evidence = audit.get("evidence", "")
    items = audit.get("line_items", [])
    out: list[Candidate] = []
    for ext_key, value in fields.items():
        if ext_key not in _onto.EXTERNAL_TO_CANONICAL:
            continue
        text = str(value or "")
        if not text:
            continue
        code = _onto.canonical(ext_key)
        if ext_key == "Invoice Date":
            ok = supported_date(text, evidence)
        elif ext_key in CHARGE_FIELDS:
            ok = supported_number(text, evidence)
        elif ext_key == "Ex Rate":
            ok = supported_rate(text, evidence)
        else:
            ok = supported(text, evidence)
        item = next((it for it in items if it.get("category") == ext_key), None)
        if item is not None:
            basis = item.get("category_basis", "")
            if basis.startswith("semantic-override"):
                source, sem = "LAYA", 1.0
            elif "semantic" in basis:
                source, sem = "LAYA", 0.8
            elif "lexicon" in basis:
                source, sem = "VLM", 0.6
            else:
                source, sem = "VLM", 0.5
            conf = 0.9 if item.get("amount_found_in_document") else 0.4
            src_text = item.get("description", "")
        else:
            source, sem, conf, src_text = "VLM", 0.5, 0.7 if ok else 0.2, text
        bbox = [0.0, 0.0, 0.0, 0.0]
        page = 0
        if citations:
            hits = citations.get(ext_key) or []
            if hits:
                h = hits[0] if isinstance(hits[0], dict) else hits[0].__dict__
                bbox = [float(v) for v in h.get("bbox", bbox)]
                page = int(h.get("page", 0))
                src_text = h.get("source_text", src_text) or src_text
        out.append(Candidate(
            canonical_field=code,
            value=text,
            confidence=round(conf, 3),
            source_text=src_text,
            page=page,
            bbox=bbox,
            source_type=source,
            model_version=model_version,
            evidence_score=_score_flag(ok),
            semantic_score=sem,
            validation_score=_score_flag(ok),
        ))
    return out
