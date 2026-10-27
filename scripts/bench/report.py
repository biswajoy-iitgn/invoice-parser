"""Leaderboards from work/bench/scores.json (score.py), layoutlmv3.json and out_timing/: one table per question,
rows sorted best first. Prints Markdown; the narrative in reports/parser_bakeoff.md is written around these tables.
Run with work/bench/venv/bin/python.
"""
import json, os, statistics, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT  # noqa: E402

BENCH = os.path.join(ROOT, 'work', 'bench')
NAMES = {
    'ppocr': 'PP-OCRv6 (OCR only)', 'tesseract': 'Tesseract 5 (OCR only)', 'easyocr': 'EasyOCR (OCR only)',
    'hunyuan': 'HunyuanOCR 1B (VLM)', 'paddlevl': 'PP-DocLayoutV3 + PaddleOCR-VL-1.6', 'glmocr': 'PP-DocLayoutV3 + GLM-OCR',
    'chandra': 'Chandra OCR 2 (VLM)', 'ppstructure': 'PP-StructureV3', 'docling-easyocr': 'Docling + EasyOCR',
    'docling-tesseract': 'Docling + Tesseract', 'docling-native': 'Docling on the PDF text layer',
}
CAPTURES = ['digital', 'office', 'phone']


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def pct(v):
    return '' if v is None else f'{100 * v:.0f}'


def table(title, rows, cols, kind, key, note='', lower_better=False):
    """rows: scores; cols: [(metric, header)]; one line per pipeline, a column group per capture."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r['kind'] == kind:
            by[r['pipeline']][r['condition']].append(r)
    # captures that have at least one value for these metrics (layout exists for digital pages only)
    caps = [c for c in CAPTURES if any(r.get(m) is not None for d in by.values() for r in d.get(c, []) for m, _ in cols)]
    lines = [f'### {title}', '']
    if note:
        lines += [note, '']
    head = ['Pipeline'] + [f'{h} ({c})' for c in caps for _, h in cols]
    lines += ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    scored = []
    for pipe, d in by.items():
        vals = [mean([r.get(m) for r in d.get(c, [])]) for c in caps for m, _ in cols]
        k = mean([mean([r.get(key) for r in d.get(c, [])]) for c in caps])
        if k is not None:
            scored.append((k, pipe, vals))
    scored.sort(key=lambda x: x[0], reverse=not lower_better)
    for _, pipe, vals in scored:
        lines.append('| ' + ' | '.join([NAMES.get(pipe, pipe)] + [pct(v) for v in vals]) + ' |')
    return '\n'.join(lines) + '\n'


def main():
    rows = json.load(open(os.path.join(BENCH, 'scores.json')))
    out = []
    out.append(table('Text accuracy on invoices (higher is better)', rows,
                     [('word_f1', 'words F1'), ('num_r', 'numbers found'), ('num_p', 'numbers right')], 'invoice', 'word_f1'))
    out.append(table('Hallucination on invoices (lower is better)', rows,
                     [('halluc', 'invented words'), ('num_invented', 'invented numbers'), ('loops', 'pages looping')], 'invoice', 'halluc',
                     note='Invented: no near-match anywhere on the page (misreads are not counted here). Values are % of output words or numbers, or % of pages for loops.',
                     lower_better=True))
    out.append(table('Line-item tables on invoices (higher is better)', rows,
                     [('cell_acc', 'cells exact'), ('num_cell_acc', 'amount cells exact'), ('row_f1', 'row F1')], 'invoice', 'cell_acc',
                     note='Hand-checked line-item tables, page 1 of each of the 7 invoices. OCR-only engines emit no tables and score 0 here.'))
    out.append(table('Reading order on invoices (higher is better)', rows,
                     [('rows_intact', 'table rows in order'), ('kv_adjacent', 'label then value')], 'invoice', 'rows_intact'))
    out.append(table('Invoice fields present in the output (higher is better)', rows,
                     [('value_found', 'values found')], 'invoice', 'value_found',
                     note='53 hand-checked fields across the 7 invoices (invoice number, date, GSTINs, B/L numbers, container, totals), page 1.'))
    out.append(table('Contracts: whole-document extraction (higher is better)', rows,
                     [('word_f1', 'words F1'), ('num_r', 'numbers found'), ('para_intact', 'paragraphs intact'), ('halluc', 'invented words')], 'contract', 'word_f1'))
    out.append(table('Layout analysis on digital pages (higher is better)', rows,
                     [('text_coverage', 'text inside a region'), ('table_region', 'line-item table found'), ('image_recall', 'logos and QR codes found')],
                     'invoice', 'table_region', note='Digital renders only, where every word and image has an exact position.'))

    lp = os.path.join(BENCH, 'layoutlmv3.json')
    if os.path.exists(lp):
        lm = json.load(open(lp))
        out += ['### LayoutLMv3 (FUNSD fine-tune) as a key-value tagger', '',
                'On PP-OCRv6 words. A field counts when its printed label is tagged as a key (QUESTION) and its value as an answer (ANSWER).', '',
                '| Capture | Fields | Label tagged as key | Value tagged as answer | Both |', '|---|---|---|---|---|']
        by = defaultdict(list)
        for r in lm:
            by[r['condition']] += r['fields']
        for c in CAPTURES:
            fs = by.get(c, [])
            if fs:
                n = len(fs)
                out.append(f"| {c} | {n} | {100 * sum(f['label_is_key'] for f in fs) / n:.0f} | {100 * sum(f['value_is_answer'] for f in fs) / n:.0f} | "
                           f"{100 * sum(f['label_is_key'] and f['value_is_answer'] for f in fs) / n:.0f} |")
        out.append('')

    td = os.path.join(BENCH, 'out_timing')
    if os.path.isdir(td):
        out += ['### Speed on this Mac, each pipeline alone (seconds per page, median of runs)', '',
                '| Pipeline | Dense invoice page | Phone photo | Contract page |', '|---|---|---|---|']
        for f in sorted(os.listdir(td)):
            t = json.load(open(os.path.join(td, f)))
            med = lambda k: f'{statistics.median(t[k]):.1f}' if k in t else ''
            out.append(f"| {NAMES.get(f[:-5], f[:-5])} | {med('megalo-p1-digital')} | {med('megalo-p1-phone')} | {med('reynolds_service-p2-digital')} |")
        out.append('')
    print('\n'.join(out))


if __name__ == '__main__':
    main()
