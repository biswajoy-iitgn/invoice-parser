"""Scores every pipeline's output against the answer key (PDF text layer + hand-checked tables and fields).

Run with work/bench/venv/bin/python. Writes work/bench/scores.json and prints the summary tables.

Metrics, per page, then averaged by pipeline x document kind x capture condition:
  word_f1 / word_p / word_r  multiset match of words, order ignored (accuracy and completeness)
  num_p / num_r              the same for numbers only (commas, currency signs and trailing dots dropped)
  halluc                     share of output words that match nothing on the page, not even as a near-miss
                             (similarity < 0.8): invented text, not misreads
  num_halluc                 share of output numbers that appear nowhere on the page (misread or invented)
  num_invented               share of output numbers not within one character of any number on the page: invented
  loops                      pages where a 6-word phrase repeats 4+ times and 3+ more than on the page (generation loop)
  cer                        character error rate against the text layer in its own order (order-sensitive)
  rows_intact                table rows whose cells come out contiguous and in order in the text (reading order)
  kv_adjacent                invoice fields whose value follows its printed label within 60 characters
  para_intact                contract paragraphs (12+ words) that come out contiguous and in order
  row_f1                     per hand-made line-item row, best matching output table row by word F1
  cell_acc / num_cell_acc    hand-made cells reproduced exactly in that row (whitespace ignored), all / numeric
  table_found                pages where the pipeline emitted any table at all for a page that has one
  text_coverage              digital pages: words inside some layout region (layout completeness)
  table_region               digital pages: line-item table area covered by a region labelled table
  image_recall               digital pages: embedded images (logos, QR codes) found as image/figure regions
  value_found                invoice field values present verbatim (spaces ignored) in the output
  seconds                    per page, model loaded
"""
import json, os, re, sys, unicodedata
from collections import Counter, defaultdict

from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BENCH, OUT, ROOT, SET, manifest  # noqa: E402

PUNCT = '.,;:()[]{}"\'|*`#<>!?'
IMAGE_LABELS = {'image', 'figure', 'picture', 'logo', 'seal', 'chart', 'qrcode', 'header_image', 'footer_image'}


def norm(s):
    s = unicodedata.normalize('NFKC', s or '')
    # formatting markup is not content: \underline{\text{Section}} -> Section, 3\. -> 3.
    s = re.sub(r'\\[a-zA-Z]+\s*\{', '', s).replace('{', '').replace('}', '')
    s = re.sub(r'\\([^a-zA-Z\s])', r'\1', s)
    s = s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"').replace('–', '-').replace('—', '-')
    return ' '.join(s.split())


def tokens(s):
    out = []
    for t in norm(s).split():
        t = t.strip(PUNCT).lower()
        if t:
            out.append(t)
    return out


def num(t):
    t = t.replace('₹', '').replace('rs.', '').replace(',', '').strip('.-')
    return t if any(c.isdigit() for c in t) else None


NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')


def numbers(text):
    """Every number in the text, commas dropped: a number glued to a word (2.1Subject) still reads as 2.1."""
    return [m.group().replace(',', '') for m in NUMBER.finditer(norm(text))]


def key(s):
    return re.sub(r'\s+', '', norm(s).lower())


def f1(a, b):
    a, b = Counter(a), Counter(b)
    hit = sum((a & b).values())
    p = hit / max(1, sum(b.values()))
    r = hit / max(1, sum(a.values()))
    return (2 * p * r / (p + r) if p + r else 0.0), p, r


def looping(p, g):
    """A generation loop: a phrase repeated back to back five or more times, or a repeated run of 30+ words, that the
    page itself doesn't contain. Tables legitimately repeat a few cells (three tax columns of 'Rate % Amt', spanned
    headers written once per column, 0.00 0.00 0.00), and redacted contract rows repeat [***]."""
    gs, gc = ' ' + ' '.join(g) + ' ', Counter(g)
    for L in range(1, 13):
        for i in range(0, len(p) - 3 * L + 1):
            unit = p[i:i + L]
            n = 1
            while p[i + n * L:i + (n + 1) * L] == unit:
                n += 1
            # a column read top to bottom repeats a value the page really has that many times: reordered, not repeated
            if (n >= 5 or (n >= 3 and n * L >= 30)) and ' ' + ' '.join(unit * n) + ' ' not in gs and not all(gc[t] >= n for t in unit):
                return True
    return False


def text_metrics(gt_words, pred_text):
    g, p = tokens(' '.join(gt_words)), tokens(pred_text)
    wf, wp, wr = f1(g, p)
    gn, pn = numbers(' '.join(gt_words)), numbers(pred_text)
    _, np_, nr = f1(gn, pn)
    gset, gnum = set(g), set(gn)
    glued = {g[i] + g[i + 1] for i in range(len(g) - 1)}  # two page words run together: a spacing error, not invention
    vocab = list(gset)
    content = [t for t in p if len(t) >= 2 or num(t)]
    unsupported = 0
    for t in content:
        if t in gset or t in glued:
            continue
        ns = numbers(t)
        if ns and ''.join(ns) == t.replace(',', ''):  # a bare number: present or not
            unsupported += any(n not in gnum for n in ns)
            continue
        best = process.extractOne(t, vocab, scorer=fuzz.ratio) if vocab else None
        unsupported += not best or best[1] < 80
    halluc = unsupported / max(1, len(content))
    num_halluc = sum(1 for n in pn if n not in gnum) / max(1, len(pn))
    pn = [n for n in pn if n]
    # invented, not misread: no number on the page within one character of it
    gnums = list(gnum)
    invented = sum(1 for n in pn if n not in gnum and len(n) >= 3
                   and not any(Levenshtein.distance(n, g, score_cutoff=1) <= 1 for g in gnums))
    num_invented = invented / max(1, len(pn))
    loop = looping(p, g)
    return dict(word_f1=wf, word_p=wp, word_r=wr, num_p=np_ if pn else 0.0, num_r=nr, halluc=halluc, num_halluc=num_halluc,
                num_invented=num_invented, loops=float(loop))


def cer(gt_lines, pred_text):
    g, p = norm(' '.join(gt_lines)), norm(pred_text)
    return Levenshtein.distance(g, p) / max(1, len(g))


def contiguous(needle, hay, threshold=90):
    """The needle appears as one contiguous, in-order stretch of the output (partial ratio over normalized text)."""
    n = norm(needle).lower()
    return bool(n) and fuzz.partial_ratio(n, hay) >= threshold


def table_metrics(gold_rows, pred_tables):
    best = None
    for t in pred_tables:
        rows = [list(r) for r in t if any(c.strip() for c in r)]
        if not rows:
            continue
        row_scores, cells, ncells, cell_hits, ncell_hits = [], 0, 0, 0, 0
        for g in gold_rows:
            gt = tokens(' '.join(g))
            s, r = max(((f1(gt, tokens(' '.join(r)))[0], r) for r in rows), key=lambda x: x[0])
            row_scores.append(s)
            keys = [key(c) for c in r]
            joined = set(keys)
            for i in range(len(keys)):  # a gold cell the model split into two or three cells still counts
                for j in (2, 3):
                    if i + j <= len(keys):
                        joined.add(''.join(keys[i:i + j]))
            for c in g:
                if not c.strip():
                    continue
                hit = key(c) in joined
                cells += 1
                cell_hits += hit
                if any(ch.isdigit() for ch in c):
                    ncells += 1
                    ncell_hits += hit
        cand = dict(row_f1=sum(row_scores) / len(row_scores), cell_acc=cell_hits / max(1, cells), num_cell_acc=ncell_hits / max(1, ncells))
        if best is None or cand['row_f1'] + cand['cell_acc'] > best['row_f1'] + best['cell_acc']:
            best = cand
    return best or dict(row_f1=0.0, cell_acc=0.0, num_cell_acc=0.0)


def inside(cx, cy, b):
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def overlap(a, b):
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def table_body_box(truth, rows, f):
    """Pixel box around the hand-made rows: from the first row's first-cell word to the last row's."""
    words = truth['words']
    toks = {t for r in rows for c in r for t in c.split()}
    firsts = [r[0].split()[0] for r in rows if r[0].strip()]
    ys = [w['bbox'][1] for w in words if firsts and w['text'] == firsts[0]]
    ye = [w['bbox'][3] for w in words if firsts and w['text'] == firsts[-1]]
    if not ys or not ye:
        return None
    y0, y1 = min(ys) - 2, max(ye) + 12
    hits = [w['bbox'] for w in words if w['text'] in toks and y0 <= w['bbox'][1] <= y1]
    if not hits:
        return None
    return [min(b[0] for b in hits) * f, y0 * f, max(b[2] for b in hits) * f, y1 * f]


def layout_metrics(item, truth, out, gold_rows):
    regs = out.get('regions') or []
    # layout means labelled regions; plain OCR engines only group text into blocks
    if not regs or item['condition'] != 'digital' or all(r['label'] == 'text' for r in regs):
        return {}
    f = item['dpi'] / 72
    boxes = [r['bbox'] for r in regs]
    words = truth['words']
    covered = sum(any(inside((w['bbox'][0] + w['bbox'][2]) / 2 * f, (w['bbox'][1] + w['bbox'][3]) / 2 * f, b) for b in boxes) for w in words)
    m = dict(text_coverage=covered / max(1, len(words)))
    if gold_rows:
        body = table_body_box(truth, gold_rows, f)
        if body:
            tbl = [r['bbox'] for r in regs if 'table' in r['label'].lower()]
            m['table_region'] = min(1.0, sum(overlap(body, b) for b in tbl) / max(1.0, area(body)))
    imgs = [[v * f for v in b] for b in truth['images']]
    if imgs:
        pics = [r['bbox'] for r in regs if r['label'].lower() in IMAGE_LABELS or 'image' in r['label'].lower() or 'figure' in r['label'].lower()]
        m['image_recall'] = sum(any(overlap(i, p) >= 0.5 * area(i) for p in pics) for i in imgs) / len(imgs)
    return m


def field_metrics(fields, text):
    text = text.replace('$', '')  # to_text drops currency signs from Markdown/HTML output, so compare without them
    flat = key(text)
    low = norm(text).lower()
    found, adj = [], []
    for label, value in fields.values():
        value = value.replace('$', '')
        found.append(key(value) in flat)
        lab = re.escape(norm(label).lower()).replace('\\ ', r'\s*')
        val = re.escape(norm(value).lower()).replace('\\ ', r'\s*')
        adj.append(bool(re.search(lab + r'.{0,60}?' + val, low, re.S)))
    return dict(value_found=sum(found) / len(found), kv_adjacent=sum(adj) / len(adj))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def main():
    items = {i['id']: i for i in manifest()}
    answers = json.load(open(os.path.join(SET, 'answers.json')))
    truths, rows = {}, []
    for pipe in sorted(d for d in os.listdir(OUT) if os.path.isdir(os.path.join(OUT, d))):
        for fn in sorted(os.listdir(os.path.join(OUT, pipe))):
            iid = fn[:-5]
            if iid not in items:
                continue
            item = items[iid]
            if item['kind'] == 'table':
                continue  # score_tables.py
            out = json.load(open(os.path.join(OUT, pipe, fn)))
            t = truths.setdefault(item['truth'], json.load(open(os.path.join(SET, 'truth', f"{item['truth']}.json"))))
            text = out.get('text') or ''
            m = dict(pipeline=pipe, item=iid, doc=item['doc'], kind=item['kind'], condition=item['condition'], seconds=out.get('seconds'), error=bool(out.get('error')))
            m.update(text_metrics([w['text'] for w in t['words']], text))
            m['cer'] = cer(t['lines'], text)
            hay = norm(text).lower()
            gold = answers['tables'].get(item['truth'])
            if gold:
                m['rows_intact'] = sum(contiguous(' '.join(c for c in r if c), hay) for r in gold) / len(gold)
                m.update(table_metrics(gold, out.get('tables') or []))
                m['table_found'] = float(bool(out.get('tables')))
            if item['kind'] == 'contract':
                paras = [b['text'] for b in t['blocks'] if len(b['text'].split()) >= 12]
                if paras:
                    m['para_intact'] = sum(contiguous(p, hay) for p in paras) / len(paras)
            if item['page'] == 0 and answers['fields'].get(item['doc']):
                m.update(field_metrics(answers['fields'][item['doc']], text))
            m.update(layout_metrics(item, t, out, gold))
            rows.append(m)

    with open(os.path.join(BENCH, 'scores.json'), 'w') as f:
        json.dump(rows, f, indent=1)

    groups = defaultdict(list)
    for r in rows:
        groups[(r['pipeline'], r['kind'], r['condition'])].append(r)
    cols = ['n', 'word_f1', 'num_r', 'num_p', 'halluc', 'num_halluc', 'num_invented', 'loops', 'cer', 'rows_intact', 'row_f1', 'cell_acc', 'num_cell_acc',
            'kv_adjacent', 'value_found', 'para_intact', 'text_coverage', 'table_region', 'image_recall', 'seconds']
    print('| pipeline | kind | capture | ' + ' | '.join(cols) + ' |')
    print('|' + '---|' * (3 + len(cols)))
    for (pipe, kind, cond), rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        vals = []
        for c in cols:
            if c == 'n':
                vals.append(str(len(rs)))
                continue
            v = mean([r.get(c) for r in rs])
            vals.append('' if v is None else f'{v:.1f}' if c == 'seconds' else f'{v:.2f}' if c == 'cer' else f'{100 * v:.0f}')
        print(f'| {pipe} | {kind} | {cond} | ' + ' | '.join(vals) + ' |')


if __name__ == '__main__':
    main()
