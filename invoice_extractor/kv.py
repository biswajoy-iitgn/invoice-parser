"""Key/value candidates from line-numbered layout text (for semantic header linking).

Two layouts are covered:
  * inline      "Inv. Dt. : 25/10/2026"  /  "H B/L No.   70430050554"   (label then value on a line)
  * stacked     a row of labels with their values on the next line, aligned by column
                ("OCEAN BILL OF LADING   HOUSE BILL OF LADING" over "MEDUAEO12208   AID0019575")
Every candidate keeps its line id(s) so a linked label can be cited and explained.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .layout import Line

_SEG = re.compile(r"\S+(?: \S+)*")          # segments separated by 2+ spaces
_LABEL_END = re.compile(r"^(?P<label>[^:]{1,40}?)\s*(?::-|:|#|-)\s*(?P<value>.+)$")
_HAS_DIGIT = re.compile(r"\d")


@dataclass
class KV:
    label: str
    value: str
    line_ids: list[str]
    layout: str            # inline | stacked


def _segments(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for part in re.split(r"(\s{2,})", text):
        if part and not part.isspace():
            out.append((pos, part.strip()))
        pos += len(part)
    return out


def _looks_label(s: str) -> bool:
    letters = sum(c.isalpha() for c in s)
    return 2 <= len(s) <= 45 and letters >= 0.6 * len(s.replace(" ", "")) and not _HAS_DIGIT.search(s[:2])


def candidates(lines: dict[str, Line]) -> list[KV]:
    out: list[KV] = []
    ids = list(lines)
    texts = {lid: lines[lid].text for lid in ids}
    for idx, lid in enumerate(ids):
        segs = _segments(texts[lid])
        # inline "label: value" inside one segment
        for _, seg in segs:
            m = _LABEL_END.match(seg)
            if m and _looks_label(m.group("label")) and m.group("value").strip():
                out.append(KV(m.group("label").strip(), m.group("value").strip(), [lid], "inline"))
        # inline "LABEL   value" across adjacent segments
        for (p1, s1), (p2, s2) in zip(segs, segs[1:]):
            if _looks_label(s1) and not _looks_label(s2) and not s1.endswith(":"):
                out.append(KV(s1.strip(" :"), s2, [lid], "inline"))
            elif s1.endswith(":") and _looks_label(s1[:-1]):
                out.append(KV(s1[:-1].strip(), s2, [lid], "inline"))
        # stacked: this line is mostly labels and the next line has values at the same columns
        if idx + 1 < len(ids) and len(segs) >= 2 and all(_looks_label(s) for _, s in segs):
            nxt = ids[idx + 1]
            if lines[nxt].page != lines[lid].page:
                continue
            vsegs = _segments(texts[nxt])
            if not vsegs or all(_looks_label(s) for _, s in vsegs):
                continue
            for p1, s1 in segs:
                end1 = p1 + len(s1)
                best = None
                for p2, s2 in vsegs:
                    overlap = min(end1, p2 + len(s2)) - max(p1, p2)
                    if overlap > 0 or abs(p2 - p1) <= 3:
                        if best is None or abs(p2 - p1) < abs(best[0] - p1):
                            best = (p2, s2)
                if best:
                    out.append(KV(s1, best[1], [lid, nxt], "stacked"))
    return out
