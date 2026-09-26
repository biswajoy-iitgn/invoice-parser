"""Geometry primitives: words -> segments (cells) -> visual rows, plus reading-order text."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 1.0

    @property
    def h(self) -> float:
        return max(1.0, self.y1 - self.y0)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Segment:
    """Horizontally contiguous words: one table cell or one label/value phrase."""
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def conf(self) -> float:
        return min(w.conf for w in self.words)


@dataclass
class Row:
    words: list[Word]
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "  ".join(s.text for s in self.segments)

    @property
    def cy(self) -> float:
        return statistics.median(w.cy for w in self.words)

    @property
    def h(self) -> float:
        return statistics.median(w.h for w in self.words)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)


def build_rows(words: list[Word], gap_factor: float = 0.9) -> list[Row]:
    """Chain words left-to-right into visual lines (tolerates residual skew), then split cells on wide gaps."""
    if not words:
        return []
    med_h = statistics.median(w.h for w in words)
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.x0):
        best, best_cost = None, None
        for r in rows:
            last = r[-1]
            if last.x1 > w.x0 + 0.5 * (w.x1 - w.x0):   # noisy OCR boxes may overlap a little
                continue
            dy = abs(last.cy - w.cy)
            if dy > 0.5 * min(w.h, last.h, 1.6 * med_h):
                continue
            cost = dy + 0.02 * (w.x0 - last.x1)
            if best_cost is None or cost < best_cost:
                best, best_cost = r, cost
        if best is None:
            rows.append([w])
        else:
            best.append(w)
    rows = _merge_overlapping(rows, med_h)
    out = []
    for r in sorted(rows, key=lambda r: statistics.median(w.cy for w in r)):
        r.sort(key=lambda w: w.x0)
        # cell gap: wider than ~1.5 character widths (monospace word spaces are a full character wide)
        cws = [(w.x1 - w.x0) / len(w.text) for w in r if len(w.text) >= 2]
        cw = statistics.median(cws) if cws else None
        segs, cur = [], [r[0]]
        for a, b in zip(r, r[1:]):
            hh = min(statistics.median([a.h, b.h]), 1.4 * med_h)
            thr = min(max(1.5 * cw, 0.5 * hh), 1.6 * hh) if cw else gap_factor * hh
            if b.x0 - a.x1 > thr:
                segs.append(Segment(cur))
                cur = []
            cur.append(b)
        segs.append(Segment(cur))
        out.append(Row(r, segs))
    return out


def _merge_overlapping(rows: list[list[Word]], med_h: float) -> list[list[Word]]:
    """Two chains on the same visual line that never touched horizontally (e.g. separate columns)."""
    rows = sorted(rows, key=lambda r: statistics.median(w.cy for w in r))
    merged: list[list[Word]] = []
    for r in rows:
        if merged:
            prev = merged[-1]
            pcy = statistics.median(w.cy for w in prev)
            rcy = statistics.median(w.cy for w in r)
            ph = statistics.median(w.h for w in prev)
            rh = statistics.median(w.h for w in r)
            overlap_x = any(not (a.x1 < b.x0 or b.x1 < a.x0) for a in prev for b in r)
            if abs(pcy - rcy) < 0.35 * min(ph, rh) and not overlap_x:
                prev.extend(r)
                continue
        merged.append(r)
    return merged


def reading_order_text(rows: list[Row]) -> str:
    return "\n".join(r.text for r in rows)
