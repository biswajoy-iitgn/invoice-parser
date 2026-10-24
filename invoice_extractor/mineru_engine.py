"""MinerU 4 sheet engine (default): ``mineru parse`` -> markdown -> OUTPUT_FIELDS.

Runs the MinerU CLI (``MINERU_BIN``, default ``mineru`` on PATH with a fallback
to the project's sibling venv) against the local managed server, then maps the
markdown tables onto the sheet schema. No reader LLM involved.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import sheet_fields

NAME = "mineru"


def resolve_bin() -> str:
    if os.environ.get("MINERU_BIN"):
        return os.environ["MINERU_BIN"]
    found = shutil.which("mineru")
    if found:
        return found
    sibling = Path("/Users/dtadmin/Desktop/DevelopmentProjects/Table_Parsing/sample/.venv/bin/mineru")
    if sibling.exists():
        return str(sibling)
    raise RuntimeError("mineru CLI not found (set MINERU_BIN)")


WAIT_S = int(os.environ.get("MINERU_WAIT_S", "120"))

# Disk cache for parsed markdown, keyed by file SHA-256: repeats and
# byte-identical duplicates skip the MinerU subprocess entirely.
CACHE_DIR = Path(os.environ.get("MINERU_CACHE_DIR", Path(__file__).resolve().parents[1] / "work" / "mineru_cache"))


def _cache_key(path: Path) -> Path:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return CACHE_DIR / f"{h.hexdigest()}.md"


def _run_parse(binpath: str, path: Path, out_md: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binpath, "parse", str(path), "-o", str(out_md), "--wait", str(WAIT_S)],
        capture_output=True, text=True, timeout=WAIT_S + 120)


def parse_markdown(path: str | Path) -> str:
    """Parse one file with MinerU 4; returns the markdown text."""
    src = Path(path)
    try:
        key = _cache_key(src)
        if key.exists():
            return key.read_text(encoding="utf-8")
    except OSError:
        key = None
    out_md = src.parent / f"{src.stem}.mineru.md"
    try:
        proc = _run_parse(resolve_bin(), src, out_md)
    except FileNotFoundError as exc:
        raise RuntimeError(f"mineru CLI not found (set MINERU_BIN): {exc}") from exc
    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and "server is not running" in combined:
        # First parse of the boot: bring up the managed server once, then retry.
        subprocess.run([resolve_bin(), "server", "start"], capture_output=True, text=True, timeout=120)
        time.sleep(10)
        proc = _run_parse(resolve_bin(), src, out_md)
        combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or not out_md.exists():
        raise RuntimeError(f"mineru parse failed: {combined.strip()[-400:]}")
    try:
        text = out_md.read_text(encoding="utf-8")
    finally:
        try:
            out_md.unlink()
        except OSError:
            pass
    if key is not None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = key.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(key)
        except OSError:
            pass
    return text


# --------------------------------------------------------------------------
# Markdown -> kv / charge items
# --------------------------------------------------------------------------

def md_tables(text: str) -> list[list[list[str]]]:
    tables, current = [], []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells):
                continue
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def html_tables(text: str) -> list[list[list[str]]]:
    tables = []
    for tbl in re.findall(r"<table.*?>(.*?)</table>", text, re.S | re.I):
        rows = []
        for tr in re.findall(r"<tr.*?>(.*?)</tr>", tbl, re.S | re.I):
            cells = [html.unescape(re.sub(r"\s+", " ", c).strip())
                     for c in re.findall(r"<td.*?>(.*?)</td>", tr, re.S | re.I)]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def html_rows(text: str) -> list[list[str]]:
    return [row for tbl in html_tables(text) for row in tbl]


def cell_below(tables: list, label: str) -> tuple[str | None, str | None]:
    # Normalized equality/word-boundary match (like headers_from_kv): a plain
    # substring hit lets "Date" match "Update"/"Candidate".
    want = sheet_fields._norm(label)
    for t in tables:
        for i, row in enumerate(t[:-1]):
            for j, cell in enumerate(row):
                normed = sheet_fields._norm(cell)
                if (normed == want or re.search(r"\b" + re.escape(want) + r"\b", normed)) \
                        and len(cell) < 60:
                    nxt = t[i + 1]
                    if j < len(nxt) and nxt[j].strip():
                        return cell.strip(), nxt[j].strip()
    return None, None


def reduce_markdown(md: str) -> tuple[dict, list, str, str, list]:
    """MinerU markdown -> (kv, charge items, plain text, vendor, segments)."""
    tables = md_tables(md)
    kv: dict[str, str] = {}
    for label in ("Document No.", "Invoice No.", "Inv No.", "Inv No", "Invoice #",
                  "Bill No.", "Date", "MAWB No.", "HAWB No.",
                  "M B/L No.", "H B/L No.", "Freight Terms", "S/B No.",
                  "Customer Name", "Consignee"):
        h, v = cell_below(tables, label)
        if h and v:
            kv[h] = v
    # pipe-table header/data pairs (Document No. | Customer No. | Date ...)
    for t in tables:
        if t and any("document no" in (c or "").lower() for c in t[0]):
            hdr = t[0]
            for row in t[1:]:
                for h, c in zip(hdr, row):
                    if h and c and len(h) < 40:
                        kv.setdefault(h, c)
    # plain "Label: value" lines (docx-style parses)
    for k, v in sheet_fields.kv_from_lines(md.splitlines()).items():
        kv.setdefault(k, v)
    # glued "Label valueLabel value" runs: table cells without newlines
    # ("Invoice No : ERIA261452Invoice Date : 08 Sep 2026...") plus md lines
    for tbl in html_tables(md):
        for row in tbl:
            for cell in row:
                for k, v in sheet_fields.glued_kv(cell).items():
                    kv.setdefault(k, v)
    for ln in md.splitlines():
        for k, v in sheet_fields.glued_kv(ln).items():
            kv.setdefault(k, v)
    items: list[dict] = []
    # 2-column (description, amount) rows, per-row so mixed tables still yield
    # their charge lines (a totals/footer row is dropped by is_total_row later).
    for t in tables:
        for row in t:
            if len(row) != 2:
                continue
            desc, amt = row
            if not re.search(r"[A-Za-z]{3,}", desc or ""):
                continue
            try:
                val = float((amt or "").replace(",", "").strip())
            except ValueError:
                continue
            if val:
                items.append({"description": desc.strip(), "amount_inr": val})
    rest: list[list[str]] = []
    for cells in html_rows(md):
        if len(cells) >= 3 and re.fullmatch(r"\d{3,5}", cells[0].strip()):
            amts = re.findall(r"[\d,]+\.\d{2}", cells[2])
            if not amts:
                continue
            try:
                amt = float(amts[-1].replace(",", ""))
            except ValueError:
                continue
            tax = cells[3].strip() if len(cells) > 3 and re.fullmatch(r"[A-Z]", cells[3].strip()) else ""
            items.append({"description": f"{cells[0].strip()} {cells[1].strip()}",
                          "amount_inr": amt, "tax_code": tax})
        else:
            rest.append(cells)
    # Pipe-grid rows (| S. No. | Description | ... |) carry the same charge
    # shapes as HTML rows (serial charge grids, 2-cell label/value pairs).
    pipe_rows = [row for t in tables for row in t]
    gkv, gitems = sheet_fields.generic_html_kv_items(rest + pipe_rows)
    for k, v in gkv.items():
        kv.setdefault(k, v)
    items.extend(gitems)
    tax_codes: dict = {}
    tax_totals: dict = {}
    for tbl in html_tables(md):
        codes, totals = sheet_fields.tax_info_from_grid(tbl)
        tax_codes.update(codes)
        tax_totals.update(totals)
    for tbl in tables + html_tables(md):
        for k, v in sheet_fields.invoice_total_from_rows(tbl).items():
            tax_totals.setdefault(k, v)
    text = html.unescape(re.sub(r"<[^>]+>", " ", md))
    text = re.sub(r"[ \t]+", " ", text)
    segments = [ln for ln in text.splitlines() if ln.strip()]
    for t in tables:
        for row in t:
            segments.extend(c for c in row if c)
    for cells in html_rows(md):
        segments.extend(c for c in cells if c)
    vendor = ""
    for line in md.splitlines()[:40]:
        t = re.sub(r"^#+\s*", "", line).strip()
        if len(t) < 6 or len(t) > 80 or "|" in t or "<" in t:
            continue
        if re.search(r"\b(PVT|LTD|LIMITED|GMBH|INC|LLC|SDN|PTE|BV|GROUPE?|FORWARDING|LOGISTICS)\b", t, re.I):
            vendor = sheet_fields.clean_vendor(re.sub(r"\s+", " ", t))
            break
    if not vendor:  # letterhead without a company keyword: first ALL-CAPS line
        for line in md.splitlines()[:15]:
            t = re.sub(r"^#+\s*", "", line).strip()
            if len(t) > 4 and t == t.upper() and any(c.isalpha() for c in t) and "|" not in t:
                vendor = sheet_fields.clean_vendor(t.title() if t.isupper() else t)
                break
    extra = sheet_fields.make_extra(text, tax_codes, tax_totals)
    return kv, items, text, vendor, segments, extra


READING_CHUNKS = 60
READING_CHARS = 1500


def md_reading(md: str) -> list:
    """MinerU markdown -> reading entries (no boxes: markdown carries none).

    Split on headings and capped line runs; tables keep a table label."""
    chunks: list = []
    current: list[str] = []
    label = "text"

    def flush() -> None:
        text = "\n".join(current).strip()
        current.clear()
        if text:
            lines = text.splitlines()
            is_table = "<table" in text[:500].lower() or sum(
                1 for ln in lines if ln.strip().startswith("|")) >= 2
            chunks.append({"page": None, "label": "table" if is_table else label,
                           "content": text[:READING_CHARS]})

    for line in (md or "").splitlines():
        s = line.strip()
        if not s or (s.startswith("<!--") and s.endswith("-->")):
            continue
        if s.startswith("#"):
            flush()
            label = re.sub(r"^#+\s*", "", s)[:60] or "section"
            continue
        current.append(line)
        if len(current) >= 40:
            flush()
    flush()
    return chunks[:READING_CHUNKS]


def extract(path: str | Path) -> tuple[dict, dict]:
    """Returns (fields, audit) with fields covering exactly OUTPUT_FIELDS."""
    started = time.time()
    try:
        md = parse_markdown(path)
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        # Sync path is time-boxed by WAIT_S: fail fast to REVIEW, never hang
        # the HTTP worker on a stuck parse.
        fields = sheet_fields.empty_fields()
        audit = sheet_fields.finalize_extract(
            NAME, fields, [], "", {"tax_codes": {}, "totals": {}}, started,
            note=f"mineru parse timed out — review: {exc}"[:160])
        audit["reading"] = []
        audit["reading_note"] = "MinerU parse timed out — nothing was read"
        return fields, audit
    kv, items, text, vendor, segments, extra = reduce_markdown(md)
    fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
    audit = sheet_fields.finalize_extract(NAME, fields, items, text, extra, started)
    audit["reading"] = md_reading(md)
    audit["reading_note"] = "MinerU markdown chunks in document order (no boxes)"
    return fields, audit
