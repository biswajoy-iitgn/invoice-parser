"""Document profiles: what to read from a kind of document, as config instead of code.

One YAML file per document type in configs/profiles/ (override the folder with PROFILES_DIR). A profile lists the fields,
the tables (with their columns), the checks and the review policy; `extract` turns it into a reader prompt + JSON schema,
runs the same ingest -> layout -> reader path as the invoice pipeline, cites every value to measured boxes, and routes
to REVIEW when a required value is missing, not printed where the reader says, malformed, or fails a check.

Adding a document type = adding a file. `engine: general` reads any document with no field list (every labelled value, every
table, the full text); every other profile is `engine: generic` and reads the fields and tables it lists.

Printed characters win: VLM readers silently normalise ("0,047" -> "0.047"), and the citation search ignores punctuation, so
a cited value is replaced by the text actually printed in the cited box when they differ only in punctuation or spacing.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import checks as chk
from .identifiers import KINDS, validate as validate_identifier

FIELD_TYPES = {"text", "id", "date", "number", "amount", "choice"} | KINDS
ENGINES = {"generic", "general"}


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"
    required: bool = False
    hint: str = ""
    pattern: str = ""          # the value must fully match this regex (after trimming)
    choices: list[str] = field(default_factory=list)


@dataclass
class Table:
    key: str
    label: str
    columns: list[str]
    required: bool = False
    hint: str = ""


@dataclass
class Profile:
    id: str
    name: str
    description: str = ""
    engine: str = "generic"
    auto_accept: bool = False      # False: every document goes to REVIEW, as the invoice pipeline does today
    min_verified: float = 0.9      # share of values/cells that must be found on the page
    fields: list[Field] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    reread: bool = True            # re-read low-confidence values (handwriting, smudges) from the image and compare
    detect: list[str] = field(default_factory=list)   # phrases that identify this kind of document (router.py)
    images: bool = False           # show the reader the page images as well as the text (dense grids; hurts most documents)
    models: dict = field(default_factory=dict)   # {reader: <readers id>, tables: <tables id>}; empty = the catalog defaults


def profiles_dir() -> Path:
    return Path(os.environ.get("PROFILES_DIR") or Path(__file__).resolve().parents[1] / "configs" / "profiles")


def models_file() -> Path:
    return Path(os.environ.get("MODELS_FILE") or Path(__file__).resolve().parents[1] / "configs" / "models.yaml")


def catalog() -> dict[str, list[dict]]:
    """{"readers": [...], "tables": [...]} from configs/models.yaml."""
    return yaml.safe_load(models_file().read_text())


def model_choice(p: "Profile", kind: str) -> dict:
    """The catalog entry a profile uses for `kind` (readers | tables): its own choice, else the catalog default."""
    entries = catalog()[kind]
    want = (p.models or {}).get("reader" if kind == "readers" else "tables")
    return next((e for e in entries if e["id"] == want), None) or next(e for e in entries if e.get("default"))


def _build(d: dict, where: str) -> Profile:
    def bad(msg):
        raise ValueError(f"{where}: {msg}")

    if not isinstance(d, dict) or not d.get("id") or not d.get("name"):
        bad("needs 'id' and 'name'")
    if d.get("engine", "generic") not in ENGINES:
        bad(f"engine must be one of {sorted(ENGINES)}")
    if isinstance(d.get("models"), dict):      # YAML 1.1 reads an unquoted `off` as false
        d = {**d, "models": {k: "off" if v is False else v for k, v in d["models"].items()}}
    try:
        p = Profile(**{k: v for k, v in d.items() if k not in ("fields", "tables")},
                    fields=[Field(**f) for f in d.get("fields") or []], tables=[Table(**t) for t in d.get("tables") or []])
    except TypeError as e:
        bad(str(e))
    for kind, key in (("readers", "reader"), ("tables", "tables")):
        want = (p.models or {}).get(key)
        if want and want not in {e["id"] for e in catalog()[kind]}:
            bad(f"models.{key}: {want!r} is not in {models_file().name} ({kind})")
    if set(p.models or {}) - {"reader", "tables"}:
        bad("models may only set reader and tables")
    keys = [x.key for x in p.fields] + [t.key for t in p.tables]
    if len(keys) != len(set(keys)):
        bad("duplicate field/table keys")
    for f in p.fields:
        if f.type not in FIELD_TYPES:
            bad(f"field {f.key}: type must be one of {sorted(FIELD_TYPES)}")
        if f.type == "choice" and not f.choices:
            bad(f"field {f.key}: a choice field needs choices")
        if f.pattern:
            try:
                re.compile(f.pattern)
            except re.error as e:
                bad(f"field {f.key}: bad pattern ({e})")
    for t in p.tables:
        if not t.columns:
            bad(f"table {t.key}: needs columns")
    for c in p.checks:
        try:
            chk.validate(c, {t.key: t.columns for t in p.tables}, {f.key for f in p.fields})
        except ValueError as e:
            bad(str(e))
    if p.engine == "generic" and not (p.fields or p.tables):
        bad("a generic profile needs fields or tables")
    return p


def load_all() -> dict[str, Profile]:
    out = {}
    for path in sorted(profiles_dir().glob("*.y*ml")):
        p = _build(yaml.safe_load(path.read_text()), path.name)
        if p.id in out:
            raise ValueError(f"{path.name}: duplicate profile id {p.id}")
        out[p.id] = p
    return out


EDITABLE_MODELS = {"generic": ["reader"], "general": ["reader", "tables"]}   # what each engine can switch


def describe(p: Profile) -> dict:
    """A profile as the UI shows it: fields, tables, and which models it can switch between."""
    return {"id": p.id, "name": p.name, "description": p.description, "engine": p.engine, "min_verified": p.min_verified,
            "fields": [vars(f) for f in p.fields], "tables": [vars(t) for t in p.tables], "checks": p.checks,
            "models": {k: model_choice(p, "readers" if k == "reader" else "tables")["id"] for k in EDITABLE_MODELS[p.engine]},
            "editable_models": EDITABLE_MODELS[p.engine]}


def save_models(profile_id: str, models: dict) -> Profile:
    """Write a profile's model choice into its file: only the `models:` block changes, comments and the rest stay."""
    p = get(profile_id)
    allowed = EDITABLE_MODELS[p.engine]
    if set(models) - set(allowed):
        raise ValueError(f"{p.engine} profiles can only switch: {', '.join(allowed) or 'nothing'}")
    path = next(f for f in profiles_dir().glob("*.y*ml") if (yaml.safe_load(f.read_text()) or {}).get("id") == profile_id)
    lines = path.read_text().splitlines()
    kept, skip = [], False
    for ln in lines:
        if ln.startswith("models:"):
            skip = True
            continue
        if skip and (ln.startswith((" ", "\t")) or not ln.strip()):
            continue
        skip = False
        kept.append(ln)
    while kept and not kept[-1].strip():
        kept.pop()
    block = ["models:"] + [f'  {k}: "{v}"' for k, v in {**(p.models or {}), **models}.items() if v]
    new = "\n".join(kept + (block if len(block) > 1 else [])) + "\n"
    _build(yaml.safe_load(new), path.name)            # validates before anything is written
    path.write_text(new)
    return get(profile_id)


def revision(profile_id: str) -> str:
    """Changes whenever what a profile reads with changes (its file, the model catalog, the checks), so a result kept for an
    identical file is reused only when it would come out the same."""
    import hashlib

    files = sorted(profiles_dir().glob("*.y*ml")) + [models_file()]
    from .checks import checks_file

    files.append(checks_file())
    h = hashlib.sha256(profile_id.encode())
    for f in files:
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def get(profile_id: str) -> Profile:
    profiles = load_all()
    if profile_id not in profiles:
        raise KeyError(f"unknown profile {profile_id!r}; available: {', '.join(profiles) or 'none'}")
    return profiles[profile_id]


# ------------------------------------------------------------ reader contract --
def system_prompt(p: Profile) -> str:
    lines = [f"You extract a {p.name}." + (f" {p.description}" if p.description else ""),
             "The input is the document text with one numbered line per printed line ([L#] ...), columns kept in place. "
             "Return ONLY one JSON object with these keys:"]
    for f in p.fields:
        lines.append(f"- {f.key}: {f.label}" + (f" ({f.hint})" if f.hint else "") + (f"; one of {', '.join(f.choices)}" if f.choices else ""))
    for t in p.tables:
        lines.append(f"- {t.key}: list of rows, one per printed row of {t.label}" + (f" ({t.hint})" if t.hint else "")
                     + "; each row an object with the keys " + ", ".join(f'"{c}"' for c in t.columns) + " and lines (the [L#] ids of that row)")
    lines.append("- evidence: for every non-empty field, the list of line ids where its value is printed")
    lines.append("Rules: copy values exactly as printed, including decimal commas, currency signs and spacing; do not correct, "
                 'translate or normalise anything. Use "" for a value that is not printed and [] for a table that is not there. '
                 "Never invent a value.")
    return "\n".join(lines)


def json_schema(p: Profile) -> dict:
    props = {f.key: ({"type": "string", "enum": ["", *f.choices]} if f.choices else {"type": "string"}) for f in p.fields}
    for t in p.tables:
        row = {c: {"type": "string"} for c in t.columns}
        row["lines"] = {"type": "array", "items": {"type": "string"}}
        props[t.key] = {"type": "array", "items": {"type": "object", "properties": row, "required": [*t.columns, "lines"]}}
    props["evidence"] = {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}}
    return {"type": "object", "properties": props, "required": list(props)}


# ------------------------------------------------------------------ assembly --
_number = chk.number


def _typed(value: str, ftype: str):
    """Value as the citation search should look for it: a number for number/amount fields, else the text."""
    n = _number(value) if ftype in ("number", "amount") else None
    return n if n is not None else value


def _printed(value: str, hits: list[dict]) -> str:
    """The text printed in the cited box when it equals `value` up to punctuation/spacing, else `value` itself.

    A hit carries the whole printed segment ("C 0,047 Si 0,017"), so look for the run of words that matches the value."""
    from .pipeline import _an

    want = _an(value)
    for h in hits:
        if h.get("match") != "exact" or not want:
            continue
        words = (h.get("text") or "").split()
        for i in range(len(words)):
            for j in range(i, min(len(words), i + 8)):
                if _an("".join(words[i:j + 1])) == want:
                    return " ".join(words[i:j + 1])
    return value


def _cell_hits(value: str, index: dict, line_ids=None, box=None, page=None) -> list[dict]:
    """Where a table cell is printed, looked up only in the cell's own surroundings: the table's region, else the lines the
    reader cited for its row. A page-wide search lets a cell be "verified" by the same digits printed somewhere else
    (a 3.1 in the title vouching for a misread 31). With neither given, the whole page is searched."""
    from .pipeline import clean_lines, locate

    scope = None
    if box is not None:
        x0, y0, x1, y1 = box
        scope = [ln for ln in index.values() if (page is None or ln.page == page)
                 and x0 - 4 <= (ln.box[0] + ln.box[2]) / 2 <= x1 + 4 and y0 - 4 <= (ln.box[1] + ln.box[3]) / 2 <= y1 + 4]
    elif (ids := clean_lines(line_ids, set(index))):
        scope = [index[i] for i in ids]
    return locate(value, scope if scope is not None else list(index.values()))


OCR_TRUST = float(os.environ.get("PROFILE_OCR_TRUST", "0.97"))   # handwriting and smudges score 0.92-0.96, printed text 0.98+


def _low_ocr(hits: list[dict]) -> float | None:
    """The OCR confidence of the cited text when it is below the trust level. Checking a value against OCR text cannot catch
    an OCR misread (a handwritten 1 read as a bracket), so a doubtful read is flagged for a person instead."""
    score = min((h.get("ocr_score", 1.0) for h in hits[:1]), default=1.0)
    return score if score < OCR_TRUST else None


def _issue(f: Field, value: str) -> str:
    if f.pattern and not re.fullmatch(f.pattern, value.strip()):
        return f"does not match {f.pattern}"
    if f.choices and value not in f.choices:
        return f"not one of {', '.join(f.choices)}"
    if f.type in KINDS:
        return validate_identifier(f.type, value)
    if f.type in ("number", "amount") and _number(value) is None:
        return "not a number"
    return ""


def assemble(p: Profile, raw: dict, index: dict) -> dict:
    """Reader JSON -> cited, validated result. Pure: no model, no files."""
    from .pipeline import cite

    reasons, out_fields, out_tables = [], {}, {}
    ev = raw.get("evidence") or {}
    for f in p.fields:
        v = str(raw.get(f.key) or "").strip()
        if not v:
            out_fields[f.key] = dict(value="", status="missing")
            if f.required:
                reasons.append(f"{f.key}: required value not printed")
            continue
        hits, basis = cite(_typed(v, f.type), ev.get(f.key), index, numeric=f.type in ("number", "amount"))
        shown = _printed(v, hits)
        issue = _issue(f, shown)
        low = _low_ocr(hits)
        status = "invalid" if issue else "unverified" if (low is not None or not hits) else "verified"
        if low is not None and not issue:
            issue = f"read with low OCR confidence ({low:.2f}); check it on the page"
        out_fields[f.key] = dict(value=shown, status=status, basis=basis, boxes=hits[:2], **({"issue": issue} if issue else {}),
                                 **({"model_read": v} if shown != v else {}))
        if status != "verified" and (f.required or issue):
            reasons.append(f"{f.key}: {issue or 'value not found on the page'}")
    for t in p.tables:
        rows, found, total = [], 0, 0
        for r in raw.get(t.key) or []:
            cells = {c: str((r or {}).get(c) or "").strip() for c in t.columns}
            ok = 0
            for c, v in cells.items():
                if v:
                    total += 1
                    hits = _cell_hits(v, index, (r or {}).get("lines"))
                    cells[c] = _printed(v, hits)
                    ok += bool(hits)
                    found += bool(hits)
            rows.append(dict(cells=cells, verified=round(ok / max(1, sum(bool(v) for v in cells.values())), 2)))
        share = found / total if total else None
        out_tables[t.key] = dict(columns=t.columns, rows=rows, verified=None if share is None else round(share, 3))
        if t.required and not rows:
            reasons.append(f"{t.key}: no rows read")
        elif share is not None and share < p.min_verified:
            reasons.append(f"{t.key}: only {share:.0%} of cells found on the page")
    results = chk.run(p.checks, {k: f["value"] for k, f in out_fields.items()},
                      {k: {"columns": t["columns"], "rows": [r["cells"] for r in t["rows"]]} for k, t in out_tables.items()})
    reasons += [f"{r['name']}: {r['detail']}" for r in results if r["ok"] is False]
    if raw.get("_truncated"):
        reasons.append("the reader ran out of room: values at the end of the document may be missing")
    return dict(profile=p.id, fields=out_fields, tables=out_tables, checks=results, reasons=reasons,
                decision="REVIEW" if reasons or not p.auto_accept else "ACCEPT")


# ------------------------------------------------------------ vision re-read --
VISION_PROMPT = ('This is a crop from a scanned document. It shows the value of "{label}", possibly handwritten. Reply with only the text '
                 "exactly as written, nothing else; leave out the label and any colon. {hint}If it is a signature, a scribble or not legible, reply NONE.")
DATE_HINT = "It is a date written day-month-year with hyphens, for example 12-4-26. "


def _crop(path: str, page: int, bbox: list[float]) -> bytes:
    """PNG of a box (page units: pixels for an image, points for a PDF) with generous room around it, at reading resolution."""
    x0, y0, x1, y1 = bbox
    # room on both sides (an OCR box often starts late or stops short of the last digit), less above and below so a neighbouring line stays out
    px, pl, py = max(6.0, 0.6 * (x1 - x0)), max(6.0, 0.4 * (x1 - x0)), max(5.0, 0.4 * (y1 - y0))
    if os.path.splitext(path)[1].lower() == ".pdf":
        import fitz

        with fitz.open(path) as pdf:
            pg = pdf[page - 1]
            clip = fitz.Rect(x0 - pl, y0 - py, x1 + px, y1 + py) & pg.rect
            return pg.get_pixmap(clip=clip, dpi=300).tobytes("png")
    import io

    from PIL import Image

    with Image.open(path) as im:
        crop = im.convert("RGB").crop((max(0, int(x0 - pl)), max(0, int(y0 - py)), min(im.width, int(x1 + px)), min(im.height, int(y1 + py))))
        if crop.height < 96:                                   # a small crop reads better enlarged
            k = 96 / crop.height
            crop = crop.resize((int(crop.width * k), 96), Image.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        return buf.getvalue()


def vision_read(png: bytes, label: str, model: str) -> str:
    """What the vision model reads in one crop. "" when it cannot say. The reader reads it (pipeline.chat): one model for both."""
    import base64

    from .pipeline import chat

    text = chat([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        {"type": "text", "text": VISION_PROMPT.format(label=label, hint=DATE_HINT if "DATE" in label.upper() else "")}]}],
        model, max_tokens=64, timeout=300.0).strip().strip("`\"' ")
    if not text or text.upper().startswith("NONE"):
        return ""
    return text.splitlines()[0].rsplit(":", 1)[-1].strip()     # a label caught in the crop ("NO.:2602468") is not part of the value


def reread(path: str, result: dict, model: str, read=None) -> dict:
    """Second opinion on the doubtful values: crop each one from the image, let the vision model read it, compare with the OCR text.
    Agreement clears the doubt; a difference is shown next to the OCR value for a person to settle. Mutates and returns `result`."""
    from .pipeline import _an

    read = read or vision_read
    fields = result["fields"]
    items = list(enumerate(fields)) if isinstance(fields, list) else list(fields.items())
    agreed, differ = [], []
    for k, f in items:
        label = f.get("label") or str(k).replace("_", " ")
        if "OCR confidence" not in (f.get("issue") or "") or not f.get("boxes"):
            continue
        b = f["boxes"][0]
        try:
            seen = read(_crop(path, b["page"], b["bbox"]), label, model)
        except Exception as exc:         # a missing vision model leaves the flag where it was
            f["vision_error"] = f"{type(exc).__name__}: {exc}"[:120]
            continue
        if not seen:
            continue
        f["vision_read"] = seen
        if _an(seen) == _an(f["value"]):
            f["status"] = "verified"
            f.pop("issue", None)
            agreed.append(label)
        else:
            f["issue"] = f"the image re-read says {seen}"
            differ.append(f"{label} (OCR {f['value']}, image {seen})")
    # every low-confidence reason (the general summary line, or a profile's "<key>: read with low ...") is rebuilt from what is left
    keep = [r for r in result["reasons"] if not r.startswith("low OCR confidence") and ": read with low OCR confidence" not in r]
    left = [f.get("label") or str(k) for k, f in items if "OCR confidence" in (f.get("issue") or "")]
    if left:
        keep.append(f"low OCR confidence (handwriting, stamps or smudges): {', '.join(left[:6])}{' and more' if len(left) > 6 else ''}")
    if differ:
        keep.append("the image re-read differs from the OCR text: " + "; ".join(differ[:6]))
    result["reasons"] = keep
    result["reread"] = {"agreed": len(agreed), "differ": len(differ)}
    return result


AUTO = "auto"


def extract(path: str, profile: str | Profile, model: str | None = None, force_ocr: bool = False) -> tuple[dict, dict]:
    """(result, audit) for one document under a profile, or under the one the router picks when `profile` is "auto".

    The pages are read once (text layer, else PP-OCRv6); the router then picks the profile from that text and the models
    the profile needs, and only those are loaded. result["route"] says what was chosen and why."""
    from . import pipeline, router

    pages = pipeline.load_pages(path, force_ocr=force_ocr)
    text, index = pipeline.serialize([(pg.number, pg.units) for pg in pages])
    chosen = not (isinstance(profile, str) and profile == AUTO)
    scores, reason = {}, ""
    if chosen:
        p = get(profile) if isinstance(profile, str) else profile
    else:
        pid, scores, reason = router.detect(text, load_all())
        p = get(pid)
    route = router.plan(p, text, index, chosen)
    route.scores = scores
    if reason:
        route.why.insert(0, reason)
    if model:
        route.reader = router.reader_id(model)
    audit = {"file": path, "profile": p.id, "pages": [pg.source for pg in pages], "route": route.as_dict(),
             "models": {"reader": route.reader, "tables": route.tables}}
    if p.engine == "general":
        raw = pipeline.call_reader(text, route.reader, system=GENERAL_PROMPT, schema=GENERAL_SCHEMA, images=_page_images(path, p))
        blocks, audit["doc_parser"] = _parsed_tables(path) if route.tables == "document-parser" else ([], {"used": False, "reason": route.tables})
        if route.tables == "off":
            raw = {**raw, "tables": []}
        audit["reader_raw"] = raw
        result = assemble_general(p, raw, index, pages, blocks)
    else:
        raw = pipeline.call_reader(text, route.reader, system=system_prompt(p), schema=json_schema(p), images=_page_images(path, p))
        audit["reader_raw"] = raw
        result = assemble(p, raw, index)
    if p.reread and _can_reread():
        # crops go to the default reader, the one measured as re-reader (93 of 106 crops, reports/reader_bakeoff.md), whichever
        # reader the profile uses; it starts only if a value is doubtful
        result = reread(path, result, router.reader_id())
    return {**result, "route": route.as_dict()}, audit


READER_IMAGES = os.environ.get("READER_IMAGES", "0") == "1"
IMAGE_PAGES, IMAGE_DPI = 3, 100


def _page_images(path: str, p: Profile) -> list[bytes] | None:
    """The first pages as PNG for the reader to see alongside the text, for profiles with `images: true` (or every profile
    with READER_IMAGES=1), else None."""
    if not (p.images or READER_IMAGES):
        return None
    import io

    if os.path.splitext(path)[1].lower() == ".pdf":
        import fitz

        with fitz.open(path) as pdf:
            return [pdf[i].get_pixmap(dpi=IMAGE_DPI).tobytes("png") for i in range(min(IMAGE_PAGES, len(pdf)))]
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1700, 1700))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return [buf.getvalue()]


def _can_reread() -> bool:
    """The image re-read needs a reader that sees images: the pool's (Qwen3-VL) or a llama.cpp server with a vision projector."""
    return os.environ.get("READER_BACKEND", "pool") in ("pool", "llamacpp")


# ------------------------------------------------------------ the general profile --
GENERAL_PROMPT = (
    "You read any document. The input is the document text with one numbered line per printed line ([L#] ...), columns kept in "
    "place. Return ONLY one JSON object with keys: document_type (a short name such as invoice, certificate, form, roster, "
    "contract, letter, report), title (the document's own title or \"\"), fields, tables.\n"
    "fields: every labelled value that is printed (a label and its value: identifiers, dates, names, addresses, amounts, "
    "totals, references, stamps' text), one {label, value, lines} each, label as printed.\n"
    "tables: every table, one {title, columns, rows, lines} each; columns are the printed column titles (multi-line titles "
    "joined with a space), rows is a list of rows, each a list of cell texts in column order (\"\" for an empty cell), lines are "
    "the [L#] ids the table spans.\n"
    "Rules: copy values exactly as printed, including decimal commas, currency signs and spacing; do not correct, translate, "
    'normalise or summarise anything. Never invent a value. Skip nothing that is printed as a label with a value or inside a table.')

GENERAL_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"}, "title": {"type": "string"},
        "fields": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "value": {"type": "string"}, "lines": {"type": "array", "items": {"type": "string"}}},
            "required": ["label", "value", "lines"]}},
        "tables": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            "lines": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "columns", "rows", "lines"]}},
    },
    "required": ["document_type", "title", "fields", "tables"],
}


def _parsed_tables(path: str) -> tuple[list[dict], dict]:
    """Tables read by the document parser (PaddleOCR-VL, the strongest table reader in reports/parser_bakeoff.md), when it is
    available: [{"title", "columns", "rows", "lines"}] with rows as cell lists. ([], note) when it is off or fails."""
    try:
        from . import vl_client

        if not vl_client.enabled():
            return [], {"used": False, "reason": "off"}
        from .facts import _html_rows

        tables = []
        for pg in vl_client.parse_document(path):
            for b in pg["blocks"]:
                if b.get("label") == "table" and "<t" in (b.get("content") or ""):
                    rows = _html_rows(b["content"])
                    if len(rows) >= 2:
                        tables.append({"title": "", "columns": rows[0], "rows": rows[1:], "lines": [], "page": pg["page"], "bbox": b.get("bbox")})
        return tables, {"used": True, "model": vl_client.MODEL, "tables": len(tables)}
    except Exception as exc:     # the reader's tables stand alone
        return [], {"used": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def assemble_general(p: Profile, raw: dict, index: dict, pages: list, parsed_tables: list[dict] | None = None) -> dict:
    """Any document: every labelled value, every table, the full text, each value cited and its printed form kept."""
    from .pipeline import cite

    reasons, fields, tables = [], [], []
    seen, repeats = set(), 0
    for f in raw.get("fields") or []:
        label, v = str((f or {}).get("label") or "").strip(), str((f or {}).get("value") or "").strip()
        if not (label and v):
            continue
        if (label, v) in seen:         # a looping reader repeats one line hundreds of times
            repeats += 1
            continue
        seen.add((label, v))
        hits, basis = cite(v, f.get("lines"), index)
        shown = _printed(v, hits)
        low = _low_ocr(hits)
        fields.append(dict(label=label, value=shown, status="verified" if hits and low is None else "unverified", basis=basis, boxes=hits[:2],
                           **({"model_read": v} if shown != v else {}),
                           **({"issue": f"read with low OCR confidence ({low:.2f}); check it on the page"} if low is not None else {})))
    unverified = [f["label"] for f in fields if f["status"] != "verified" and "issue" not in f]
    doubtful = [f["label"] for f in fields if "issue" in f]
    if fields and 1 - len(unverified) / len(fields) < p.min_verified:
        reasons.append(f"fields: {len(unverified)} of {len(fields)} values not found on the page")
    if doubtful:
        reasons.append(f"low OCR confidence (handwriting, stamps or smudges): {', '.join(doubtful[:6])}{' and more' if len(doubtful) > 6 else ''}")
    if repeats > 3:
        reasons.append(f"the reader repeated itself ({repeats} repeats dropped): check that nothing is missing")
    if raw.get("_truncated"):
        reasons.append("the reader ran out of room: values at the end of the document may be missing")
    source = "document-parser" if parsed_tables else "reader"
    for t in (parsed_tables or raw.get("tables") or []):
        rows, found, total = [], 0, 0
        for r in t.get("rows") or []:
            row = []
            for v in (str(c or "").strip() for c in r):
                if v:
                    total += 1
                    hits = _cell_hits(v, index, t.get("lines"), t.get("bbox"), t.get("page"))
                    found += bool(hits)
                    v = _printed(v, hits)
                row.append(v)
            if any(row):
                rows.append(row)
        share = found / total if total else None
        tables.append(dict(title=t.get("title") or "", columns=[str(c) for c in t.get("columns") or []], rows=rows,
                           verified=None if share is None else round(share, 3), source=source))
        if share is not None and share < p.min_verified:
            reasons.append(f"table {len(tables)}: only {share:.0%} of cells found on the page")
    text = [{"page": pg.number, "source": pg.source,
             "text": "\n".join(ln.text for ln in index.values() if ln.page == pg.number)} for pg in pages]
    results = chk.run_identifiers(fields) + chk.run_automatic(tables)
    for r in results:
        if "field" in r and r["ok"] is False:                       # a value that fails its own shape or check digit is invalid, not just doubtful
            fields[r["field"]]["status"], fields[r["field"]]["issue"] = "invalid", r["detail"]
    reasons += [f"{r['name']}: {r['detail']}" for r in results if r["ok"] is False]
    return dict(profile=p.id, document_type=str(raw.get("document_type") or ""), title=str(raw.get("title") or ""), fields=fields,
                tables=tables, checks=results, text=text, reasons=reasons, decision="REVIEW" if reasons or not p.auto_accept else "ACCEPT")
