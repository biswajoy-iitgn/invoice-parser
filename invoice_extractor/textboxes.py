"""Text-match bounding boxes for the sheet engines (mineru/paddle/docling).

The sheet engines return values without coordinates. For PDFs with a text
layer, each printed value is located with a word search and returned in the
shapes the detail view already renders:

- citations: ``{"Invoice No": [{"page": 1, "bbox": [x0, y0, x1, y1],
  "text": "7044012289", "match": "exact"}]}`` (PDF points; the service scales
  them to display pixels)
- explained: ``{"Invoice No": {"basis": "relocated", "citations": [...]}}``
  for the field table's Why? disclosure.

At most MAX_BOXES boxes per field; values shorter than 3 chars are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_BOXES = 4


def _grouped(num: str, indian: bool = False) -> str:
    intpart, dot, frac = num.partition(".")
    if len(intpart) <= 3:
        return num
    if not indian:
        out = ""
        while len(intpart) > 3:
            out = "," + intpart[-3:] + out
            intpart = intpart[:-3]
        return intpart + out + (dot + frac if dot else "")
    head, last3 = intpart[:-3], intpart[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + last3 + (dot + frac if dot else "")


def variants(value: str) -> list[str]:
    """Printable forms of one stored value to search for."""
    v = (value or "").strip().rstrip(";")
    if len(v) < 3:
        return []
    out: list[str] = []
    parts = [p.strip() for p in re.split(r"[;\n]+", v)]
    for p in parts:
        p = re.sub(r"^(HBL|MBL)\s+", "", p, flags=re.IGNORECASE).strip()
        if len(p) < 3:
            continue
        out.append(p)
        m = re.fullmatch(r"([\d,]+(?:\.\d+)?)\s*(kg|cbm|INR)?", p, re.I)
        if m:
            plain = m.group(1).replace(",", "")
            if "," in m.group(1):
                out.append(plain)
            else:
                out.append(_grouped(plain))
                out.append(_grouped(plain, indian=True))
            if m.group(2):
                out.append(plain + " " + m.group(2))
        elif " " in p and len(p) > 24:
            out.append(" ".join(p.split()[:4]))
    seen, uniq = set(), []
    for o in out:
        if o not in seen:
            seen.add(o)
            uniq.append(o)
    return uniq[:6]


def citations_for(path: str | Path, fields: dict) -> dict:
    """Map each filled field to its printed hits ([]-valued fields omitted)."""
    import fitz

    out: dict = {}
    try:
        doc = fitz.open(path)
    except Exception:
        return out
    try:
        for field, value in (fields or {}).items():
            if not value or not str(value).strip():
                continue
            hits: list = []
            for needle in variants(str(value)):
                try:
                    for page in doc:
                        for r in page.search_for(needle):
                            hits.append({"page": page.number + 1,
                                         "bbox": [r.x0, r.y0, r.x1, r.y1],
                                         "text": needle, "match": "exact"})
                            if len(hits) >= MAX_BOXES:
                                break
                        if len(hits) >= MAX_BOXES:
                            break
                except Exception:
                    continue
                if hits:
                    break
            if hits:
                out[field] = hits
    finally:
        doc.close()
    return out


def explained_for(citations: dict) -> dict:
    """fields_explained entries so text-match boxes get Why? disclosures."""
    return {f: {"basis": "relocated",
                "citations": [{"page": h.get("page"), "bbox": list(h.get("bbox") or []),
                               "text": (h.get("text") or "")[:80], "match": "exact"}
                              for h in hits[:2] if h.get("bbox")]}
            for f, hits in (citations or {}).items() if hits}
