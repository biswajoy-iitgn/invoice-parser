#!/usr/bin/env python3
"""Build reports/pipeline_report.pdf — professional evaluation report.

Cover page, table of contents, executive summary, methodology (dataset,
pipelines, conditions, metric glossary), results with charts, per-field
appendix, acceptance statement, reproduction appendix.
"""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image as RLImage,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402

NAVY = colors.HexColor("#1f3a5f")
ACCENT = colors.HexColor("#2e7d6f")
LIGHT = colors.HexColor("#eef2f7")
GREY = colors.HexColor("#6b7280")

ARMS = {
    "A/orig": ("Pipeline A · Original", "HunyuanOCR + Qwen3-VL-8B + LAYA on original renders"),
    "A/lr": ("Pipeline A · Degraded (LR)", "Same pipeline on 3× downscaled + JPEG inputs"),
    "A/sr": ("Pipeline A · Super-Resolved (SR)", "Degraded inputs restored with OmniSR ×2 (MPS)"),
    "B/orig": ("Pipeline B · Original", "HunyuanOCR + MLX Qwen3-4B LoRA reader + LAYA"),
    "D/orig": ("Pipeline D · Original", "PP-StructureV3 + Qwen3-VL-8B + LAYA on original renders"),
    "D/lr": ("Pipeline D · Degraded (LR)", "Same Paddle pipeline on 3× downscaled + JPEG inputs"),
    "E/orig": ("Pipeline E · Original", "PP-StructureV3 + PaddleOCR-VL-1.6 + Qwen3-VL-8B + LAYA"),
}
PIPELINES = [
    ("Pipeline A (primary)",
     "Ingest: HunyuanOCR Q8_0 GGUF on Apple Metal (Docling layout/tables for digital PDFs). "
     "Reader: Qwen3-VL-8B-Instruct via Ollama, schema-constrained. "
     "Semantics: fine-tuned LAYA Choice vote + charge lexicon. "
     "Safety: evidence guard, hallucination blanking, citation boxes, REVIEW routing."),
    ("Pipeline B (candidate)",
     "Same ingest and semantics as A; reader replaced by a Qwen3-4B MLX LoRA model "
     "fine-tuned on 3,000 corpus pairs (1,200 iterations, Mac Metal). All other stages identical."),
    ("Pipeline C (exploratory, unscored)",
     "Docling (CPU) layout + tables + Tesseract OCR as ingest, Ollama reader otherwise. "
     "Evaluated for feasibility only — throughput too low for the 50-document track (0 docs)."),
    ("Pipeline D (Paddle)",
     "Ingest: PP-StructureV3 (PP-DocLayoutV3 + PP-OCRv6) with reading-ordered line output; "
     "reader, LAYA semantics, and safety stages identical to Pipeline A, so D-vs-A isolates "
     "ingest quality. Runs on Mac CPU under paddlepaddle 3.3.1."),
    ("Pipeline E (Paddle VL)",
     "Same as D with PaddleOCR-VL-1.6 block parsing as the primary transcription leg "
     "(StructureV3 fallback). Slowest arm (~2 min/doc on CPU)."),
]


def chart_bar(labels, series, title, ylabel, path, ylim=None):
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    x = range(len(labels))
    w = 0.78 / max(1, len(series))
    palette = ["#1f3a5f", "#2e7d6f", "#c08a3c", "#8b9bb4"]
    for i, (name, vals) in enumerate(series):
        ax.bar([p + (i - len(series) / 2 + 0.5) * w for p in x], vals, w,
               label=name, color=palette[i % len(palette)], edgecolor="white")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.set_title(title, fontsize=9, fontweight="bold", color="#1f3a5f")
    ax.legend(fontsize=7.5, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    if ylim:
        ax.set_ylim(ylim)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


class Doc(BaseDocTemplate):
    def __init__(self, path, **kw):
        super().__init__(path, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                         topMargin=2.2 * cm, bottomMargin=2 * cm, **kw)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates([PageTemplate(id="cover", frames=frame, onPage=self._cover),
                               PageTemplate(id="inner", frames=frame, onPage=self._inner)])

    def _cover(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 1.2 * cm, A4[0], 1.2 * cm, stroke=0, fill=1)
        canvas.restoreState()

    def _inner(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 1.2 * cm, A4[0], 1.2 * cm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(2 * cm, A4[1] - 0.78 * cm, "Invoice Extraction — Pipeline Evaluation")
        canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 0.78 * cm, f"Page {doc.page}")
        canvas.setFillColor(GREY)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(2 * cm, 1.2 * cm, "Confidential — internal evaluation · 2026-10-02 · corpus: val-50 (synthetic)")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            name = flowable.style.name
            if name in ("H1", "H2"):
                self.notify("TOCEntry", (0 if name == "H1" else 1, flowable.getPlainText(), self.page))


def main() -> None:
    dash = json.load(open(ROOT / "reports" / "dashboard.json"))
    fm = json.load(open(ROOT / "reports" / "field_metrics.json"))
    order = ["A/orig", "A/lr", "A/sr", "B/orig", "D/orig", "D/lr", "E/orig"]
    arms = [(k, dash["pipelines"][k]) for k in order if k in dash["pipelines"]]
    a = dash["pipelines"]["A/orig"]

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=13, leading=16,
                        textColor=NAVY, spaceBefore=10, spaceAfter=6)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=10, leading=13,
                        textColor=NAVY, spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=8.5, leading=12)
    small = ParagraphStyle("Small", parent=body, fontSize=7.5, leading=10, textColor=colors.black)
    bullet = ParagraphStyle("Bullet", parent=body, leftIndent=14, bulletIndent=6, spaceAfter=2)
    cell = ParagraphStyle("Cell", parent=body, fontSize=7, leading=8.5)
    hdr = ParagraphStyle("Hdr", parent=body, fontSize=7, leading=8.5, textColor=colors.white)
    cover_title = ParagraphStyle("CoverTitle", parent=styles["Title"], fontSize=26, leading=30, textColor=NAVY)
    cover_sub = ParagraphStyle("CoverSub", parent=styles["Normal"], fontSize=12, leading=16, textColor=GREY)

    def hd(t):
        return Paragraph(f"<b>{t}</b>", hdr)

    def td(t):
        return Paragraph(str(t), cell)

    def htable(rows, widths, repeat=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if repeat else 0)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return t

    def finding(text):
        return Table([[Paragraph(f"<b>Finding.</b> {text}", body)]],
                     colWidths=[17 * cm],
                     style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e8f0ec")),
                                       ("BOX", (0, 0), (-1, -1), 0.6, ACCENT),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                       ("TOPPADDING", (0, 0), (-1, -1), 6),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))

    story = []
    # Cover (template 0)
    story.append(Spacer(1, 4 * cm))
    import datetime as _dt
    today = _dt.date.today().isoformat()
    interim = any(dash["pipelines"].get(k, {}).get("n", 50) != 50 for k in
                  [k for k, _ in arms])
    story.append(Paragraph("Invoice Extraction", cover_title))
    story.append(Paragraph("Pipeline Evaluation Report — 50 Documents" +
                           (" (Interim)" if interim else ""), cover_sub))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph("HunyuanOCR + Qwen3-VL + LAYA system · MLX reader candidate · "
                           "PaddleOCR candidates · degradation & super-resolution study", body))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(f"{today} · Corpus: synthetic val-50 · Hardware: Apple M5 Max (36 GB) · "
                           "Status: NOT PRODUCTION READY" +
                           (" · INTERIM: Paddle arms at reduced n" if interim else ""), small))
    story.append(PageBreak())

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC1", parent=body, fontSize=10, leading=14, textColor=NAVY),
        ParagraphStyle("TOC2", parent=body, fontSize=8.5, leading=12, leftIndent=16),
    ]
    story.append(Paragraph("Contents", h1))
    story.append(toc)
    story.append(PageBreak())

    story.append(Paragraph("Executive summary", h1))
    story.append(Paragraph(
        "Four evaluation arms (two readers × three input conditions) were tracked on the same "
        "50 validation invoices across 10 vendors. The primary system — <b>Pipeline A on original "
        "inputs</b> — achieves 0.56 invoice exact-match and 0.84 critical-field exact-match with "
        "<b>zero hallucinated fields</b>, 0.99 citation coverage, and micro-F1 0.97. It fails "
        "production acceptance on whole-invoice correctness (target 0.90), critical-field "
        "correctness (0.84 vs 0.995), and latency (p95 30.6 s vs 10 s).", body))
    story.append(Spacer(1, 0.2 * cm))
    for text in [
        "Super-resolution does not help: the OmniSR arm scores <i>below</i> the degraded arm "
        "on CER, coverage, and micro-F1. SR is rejected for this operating point.",
        "The MLX 4B reader fails outright on 64% of documents (malformed JSON); where it runs, "
        "transcription matches Pipeline A but extraction recall collapses (0.30 vs 0.95).",
        "Degraded input collapses every metric (field recall 0.21) — robustness to poor scans "
        "is the largest quality gap after latency.",
    ]:
        story.append(finding(text))
        story.append(Spacer(1, 0.15 * cm))
    dp = dash["pipelines"].get("D/orig")
    if dp:
        story.append(finding(
            f"Paddle interim (n={dp['n']}): PP-StructureV3 reads the words (CER {dp['cer']['mean']:.3f} "
            f"vs HunyuanOCR {a['cer']['mean']:.3f}) but loses the layout: reading-order score "
            f"{dp['read_order']:.2f} vs {a['read_order']:.2f}, micro-F1 {dp['micro']['f1']:.3f} vs "
            f"{a['micro']['f1']:.3f}, invoice EM {dp['invoice_em']:.2f} vs {a['invoice_em']:.2f}. "
            f"Header IDs and charge rows suffer most — table structure and block order are the gap."))
        story.append(Spacer(1, 0.15 * cm))

    story.append(Paragraph("1 &nbsp; Methodology", h1))
    story.append(Paragraph("1.1 &nbsp; Dataset", h2))
    story.append(Paragraph(
        "50 invoices sampled from the 1,000-document validation split (layout family "
        "<i>layout_03</i>), stratified 5 per vendor across 10 vendors with natural "
        "field-presence variation. The validation split is sea-freight only and uses a held-out "
        "layout family, so results measure generalisation rather than template memorisation. "
        "Ground truth comprises per-field values, ordered text regions with bounding boxes, and "
        "paired degraded (3× downscale + JPEG) images for the robustness arms.", body))
    story.append(Paragraph("1.2 &nbsp; Pipelines under test", h2))
    for name, desc in PIPELINES:
        story.append(Paragraph(f"<b>{name}.</b> {desc}", bullet, bulletText="•"))
    story.append(Paragraph("1.3 &nbsp; Input conditions", h2))
    conds = [[hd("Code"), hd("Condition"), hd("Description")],
             [td("orig"), td("Original"), td("Rendered invoice at native resolution (1275×1650).")],
             [td("lr"), td("Degraded (low-resolution)"),
              td("Ground-truth paired variant: 3× downscale + JPEG compression.")],
             [td("sr"), td("Super-resolved"),
              td("Degraded variant restored with the fine-tuned OmniSR ×2 model on MPS.")]]
    story.append(htable(conds, [1.6 * cm, 4.2 * cm, 11.2 * cm], repeat=False))
    story.append(Paragraph("1.4 &nbsp; Metric glossary", h2))
    glossary = [
        ("CER / WER", "Character / word error rate between ground-truth and system transcription; "
         "reported raw and with approved normalisation (Unicode, whitespace, OCR markup). Lower better."),
        ("Coverage", "Share of ground-truth tokens recovered. Hallucination (token): share of "
         "generated tokens with no ground-truth support. Lower hallucination better."),
        ("Field P / R / F1 / F2", "Micro-averaged precision, recall, F1, and recall-weighted F2 "
         "over the 31 canonical fields. F2 is retained alongside F1 per the evaluation spec."),
        ("Field accuracy", "Support-weighted exact-match rate over all field slots."),
        ("Invoice EM", "Share of invoices with <i>every</i> field correct. Critical EM: share with "
         "all eight critical fields correct (Invoice No/Date, Vendor, Container, MAWB, HAWB, HBL, Weight)."),
        ("Field hallucination", "Share of non-empty predictions with no document evidence "
         "(evidence-guard verdict). Must be ~0 for production."),
        ("NED", "Normalised edit distance of transcription vs ground truth. Reading order: "
         "pairwise ordering accuracy of ground-truth regions in the transcription."),
        ("Latency / citations", "End-to-end seconds (mean, p95) with ingest/LLM/cite stages; "
         "citation coverage = predicted values with a measured bounding box."),
    ]
    grows = [[hd("Metric"), hd("Definition")]]
    for k, v in glossary:
        grows.append([td(f"<b>{k}</b>"), td(v)])
    story.append(htable(grows, [3.6 * cm, 13.4 * cm]))

    story.append(Paragraph("2 &nbsp; Results", h1))
    story.append(Paragraph("2.1 &nbsp; Arm comparison (n per arm)", h2))
    cols = ["Evaluation arm", "n", "CER↓", "WER↓", "F1", "F2", "Acc", "Inv EM", "Crit EM",
            "Cover", "t-Hall↓", "f-Hall↓", "NED↓", "p95", "Cite"]
    rows = [[hd(c) for c in cols]]
    a_acc = sum(d["em"] for d in fm.values()) / sum(d["n"] for d in fm.values())

    def arm_acc(key):
        import glob as _glob

        sys.path.insert(0, str(ROOT / "scripts"))
        from aggregate_track import GT as _GT, recompute as _recompute

        if not _GT:
            for line in open(ROOT / "dataset" / "val.jsonl"):
                rec = json.loads(line)
                _GT[rec["id"]] = rec["ground_truth"]
        tot = hit = 0
        for f in _glob.glob(str(ROOT / "work" / "track*") + f"/pred_{key.replace('/', '_')}.jsonl"):
            for line in open(f):
                row = json.loads(line)
                _recompute(row)
                for v in row["field_metrics"].values():
                    tot += 1
                    hit += v["em"]
        return hit / tot if tot else 0.0
    for key, s in arms:
        m = s["micro"]
        rows.append([td(ARMS[key][0]), td(f"{s['n']}"), td(f"{s['cer']['mean']:.3f}"), td(f"{s['wer_mean']:.3f}"),
                     td(f"{m['f1']:.3f}"), td(f"{m['f2']:.3f}"),
                     td(f"{arm_acc(key):.3f}"),
                     td(f"{s['invoice_em']:.2f}"), td(f"{s['critical_em']:.2f}"),
                     td(f"{s['coverage']:.3f}"), td(f"{s['tok_hall']:.3f}"),
                     td(f"{s['field_hall_rate']:.3f}"), td(f"{s['ned']:.3f}"),
                     td(f"{s['total_s']['p95']:.1f}s"), td(f"{s['citation_coverage']:.2f}")])
    story.append(htable(rows, [2.9 * cm] + [1.0 * cm] * 14))
    story.append(Paragraph("↓ lower is better. t-Hall = token hallucination; f-Hall = field hallucination; "
                           "Cite = citation coverage; p95 = end-to-end seconds.", small))

    short = {"A/orig": "A·Original", "A/lr": "A·Degraded", "A/sr": "A·SR", "B/orig": "B·Original",
             "D/orig": "D·Original", "D/lr": "D·Degraded", "E/orig": "E·Original"}
    names = [short[k] for k, _ in arms]
    tmp = Path("/tmp/pdf_charts")
    tmp.mkdir(exist_ok=True)
    chart_bar(names, [("Invoice EM", [s["invoice_em"] for _, s in arms]),
                      ("Critical EM", [s["critical_em"] for _, s in arms])],
              "Exact match by arm", "rate", tmp / "em.png", ylim=(0, 1))
    chart_bar(names, [("CER", [s["cer"]["mean"] for _, s in arms])],
              "Character error rate by arm (lower is better)", "CER", tmp / "cer.png")
    chart_bar(names, [("Micro-F1", [s["micro"]["f1"] for _, s in arms]),
                      ("Micro-F2", [s["micro"]["f2"] for _, s in arms])],
              "Micro F1 / F2 by arm", "score", tmp / "f.png", ylim=(0, 1))
    chart_bar(names, [("Mean", [s["total_s"]["mean"] for _, s in arms]),
                      ("p95", [s["total_s"]["p95"] for _, s in arms])],
              "End-to-end latency by arm, seconds (lower is better)", "seconds", tmp / "lat.png")
    for f in ("em.png", "cer.png", "f.png", "lat.png"):
        story.append(RLImage(str(tmp / f), width=16.4 * cm, height=5.5 * cm))
        story.append(Spacer(1, 0.1 * cm))

    story.append(Paragraph("2.2 &nbsp; Reading the results", h2))
    story.append(Paragraph(
        "Pipeline A on originals dominates every quality metric while keeping field "
        "hallucination at zero — the safety property the system is designed around. The cost is "
        "latency: HunyuanOCR reloads weights per call and the 8B reader averages ~6 s per invoice. "
        "Pipeline B shares A's transcription (CER 0.27 vs 0.26) but its 4B reader fails on 64% of "
        "documents, which also explains its low citation coverage (0.35). The degraded arms show "
        "that neither the OCR nor the SR stage survives 3× downscaling: coverage falls below 0.40 "
        "and extraction recall below 0.21.", body))
    if dp:
        dl = dash["pipelines"].get("D/lr", {})
        ev = dash["pipelines"].get("E/orig", {})
        extra = (
            f"Pipeline D is the ingest-controlled comparison: identical reader, LAYA, and safety "
            f"stages, with PP-StructureV3 replacing HunyuanOCR. At n={dp['n']} the OCR itself is "
            f"close (CER {dp['cer']['mean']:.3f} vs {a['cer']['mean']:.3f}, coverage "
            f"{dp['coverage']:.3f} vs {a['coverage']:.3f}), but flattening the page to unordered "
            f"lines costs the reader dearly: reading-order {dp['read_order']:.2f} vs "
            f"{a['read_order']:.2f}, micro-F1 {dp['micro']['f1']:.3f} vs {a['micro']['f1']:.3f}, "
            f"invoice EM {dp['invoice_em']:.2f} vs {a['invoice_em']:.2f}, critical EM "
            f"{dp['critical_em']:.2f} vs {a['critical_em']:.2f}. Per-field gaps concentrate on "
            f"Bill of Lading, Container Number, and charge rows — values detached from labels.")
        if dl:
            extra += (f" On degraded inputs D collapses harder than A (coverage {dl['coverage']:.3f} "
                      f"vs {dash['pipelines']['A/lr']['coverage']:.3f}): PP-OCR detection fails on "
                      f"3× downscaled scans where HunyuanOCR still recovers a third of tokens.")
        if ev:
            extra += (f" Pipeline E (PaddleOCR-VL-1.6, n={ev['n']}) currently shows CER "
                      f"{ev['cer']['mean']:.3f} at ~2 min/doc; too slow and too few docs to judge yet.")
        story.append(Paragraph(extra, body))

    story.append(Paragraph("3 &nbsp; Per-field detail — Pipeline A, original inputs", h1))
    frows = [[hd("Canonical field"), hd("EM"), hd("Precision"), hd("Recall"), hd("F1"), hd("F2")]]
    for k in OUTPUT_FIELDS:
        d = fm[k]
        frows.append([td(k), td(f"{d['em_rate']:.2f}"), td(f"{d['precision']:.2f}"),
                      td(f"{d['recall']:.2f}"), td(f"{d['f1']:.2f}"), td(f"{d['f2']:.2f}")])
    story.append(htable(frows, [8 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm]))

    story.append(Paragraph("4 &nbsp; Acceptance statement", h1))
    acc_rows = [[hd("Metric"), hd("Production target"), hd("Actual (A/orig)"), hd("Status")],
                ["Invoice EM", "≥ 0.90", f"{a['invoice_em']:.2f}", "FAIL"],
                ["Critical-field EM", "≥ 0.995", f"{a['critical_em']:.2f}", "FAIL"],
                ["Field hallucination", "≤ 0.005", f"{a['field_hall_rate']:.3f}", "PASS"],
                ["JSON schema compliance", "1.00", "1.00", "PASS"],
                ["p95 latency", "≤ 10 s", f"{a['total_s']['p95']:.1f} s", "FAIL"],
                ["Throughput", "≥ 1 page/s", f"{a['throughput_pages_sec']:.2f} page/s", "FAIL"],
                ["Citation coverage", "measured", f"{a['citation_coverage']:.2f}", "MEASURED"]]
    story.append(htable([[td(c) for c in r] for r in acc_rows],
                        [5 * cm, 4 * cm, 4 * cm, 4 * cm], repeat=False))
    story[story.index(story[-1])]  # no-op keep
    story.append(Spacer(1, 0.2 * cm))
    story.append(finding("<b>Verdict: NOT PRODUCTION READY.</b> Blockers in priority order: "
                         "critical-field correctness, whole-invoice correctness, latency/throughput, "
                         "zero straight-through processing (all REVIEW), no production database, "
                         "synthetic-only evidence."))
    story.append(Paragraph("5 &nbsp; Limitations & reproduction", h1))
    for text in [
        "Validation split is sea-freight only with a single layout family; air-freight and "
        "layout-04 generalisation are unmeasured here (frozen test numbers exist separately).",
        "Table-structure F1 is NOT MEASURED (corpus has no table ground truth); chart metrics "
        "are NOT APPLICABLE (no charts). Pipeline C unscored for throughput reasons.",
        "Interim arms: D at n=30 (top-up 20 pending); E excluded (partial, rerunning after an "
        "OOM kill). D/orig latency is overstated — part of the track ran under CPU contention "
        "with the VL arm (ingest p95 115 s vs ~20 s uncontended).",
        "All training is currently paused (Qwen3-VL-8B QLoRA before iteration 1; MLX 4B reader at "
        "iteration 1,200 with 64% failure).",
        "Reproduce: <font face=\"Courier\" size=\"7\">scripts/track_pipelines.py --count 50</font> + "
        "<font face=\"Courier\" size=\"7\">scripts/track_paddle.py</font> (paddle-env) → "
        "<font face=\"Courier\" size=\"7\">scripts/aggregate_track.py</font> → "
        "<font face=\"Courier\" size=\"7\">scripts/build_pdf_report.py</font>. Raw rows: "
        "<font face=\"Courier\" size=\"7\">work/track*/pred_*.jsonl</font>.",
    ]:
        story.append(Paragraph(text, bullet, bulletText="•"))

    out = ROOT / "reports" / "pipeline_report.pdf"
    doc = Doc(str(out))
    doc.multiBuild(story)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
