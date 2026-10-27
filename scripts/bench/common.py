"""Shared by every runner: the manifest, resumable per-page outputs, timing, and turning HTML or Markdown into
plain text and table rows. Standard library only, so it runs in the paddle env and the bench venv alike.

Each runner writes work/bench/out/<pipeline>/<item>.json:
  text     the page as plain text, in the pipeline's reading order (tables row by row)
  tables   [[cell, ...], ...] per table, body and header rows as emitted
  regions  [{"bbox": [x0, y0, x1, y1] in image pixels, "label": str}] when the pipeline finds layout
  seconds  wall time for the page, model already loaded
"""
import json, os, re, sys, time
from html import unescape
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, os.environ.get('BENCH_DIR', 'work/bench'))  # BENCH_DIR=work/bench2 runs the second set
SET = os.path.join(BENCH, 'set')
OUT = os.path.join(BENCH, 'out')


def manifest(kind=None, condition=None):
    items = json.load(open(os.path.join(SET, 'manifest.json')))
    return [i for i in items if (kind is None or i['kind'] == kind) and (condition is None or i['condition'] in condition)]


def image_path(item):
    return os.path.join(ROOT, item['image'])


TIMING_PAGES = ['megalo-p1-digital', 'megalo-p1-phone', 'reynolds_service-p2-digital']  # dense table, phone photo, contract


def run(pipeline, fn, items=None, warmup=True):
    """Calls fn(item) -> dict for each page not done yet; the first call warms the model up and is timed separately.

    BENCH_TIMING=1 runs only TIMING_PAGES, three times each, with nothing else running, and writes seconds to
    out_timing/<pipeline>.json: the accuracy runs share the machine, so their seconds are not comparable.
    """
    items = items if items is not None else manifest()
    if os.environ.get('BENCH_TIMING'):
        pages = [i for i in items if i['id'] in TIMING_PAGES]
        fn(pages[0])  # warm-up
        secs = {}
        for item in pages:
            ts = []
            for _ in range(3):
                t = time.time()
                fn(item)
                ts.append(round(time.time() - t, 2))
            secs[item['id']] = ts
            print(f"{pipeline}: {item['id']} {ts}", flush=True)
        os.makedirs(os.path.join(OUT, '..', 'out_timing'), exist_ok=True)
        with open(os.path.join(OUT, '..', 'out_timing', f'{pipeline}.json'), 'w') as f:
            json.dump(secs, f)
        return
    os.makedirs(os.path.join(OUT, pipeline), exist_ok=True)
    todo = [i for i in items if not os.path.exists(os.path.join(OUT, pipeline, f"{i['id']}.json"))]
    print(f'{pipeline}: {len(todo)} of {len(items)} pages to run', flush=True)
    if todo and warmup:
        t = time.time()
        fn(todo[0])
        print(f'{pipeline}: warm-up {time.time() - t:.1f} s', flush=True)
    for n, item in enumerate(todo, 1):
        t = time.time()
        try:
            res = fn(item)
            res['seconds'] = round(time.time() - t, 2)
        except Exception as e:  # one failing page is a result, not a crash
            res = dict(text='', tables=[], regions=[], error=f'{type(e).__name__}: {e}'[:500], seconds=round(time.time() - t, 2))
        with open(os.path.join(OUT, pipeline, f"{item['id']}.json"), 'w') as f:
            json.dump(res, f)
        print(f"{pipeline}: {n}/{len(todo)} {item['id']} {res['seconds']} s{' ERROR ' + res['error'][:80] if res.get('error') else ''}", flush=True)


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.row, self.cell, self.depth = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
        elif tag == 'tr' and self.depth == 1:
            self.row = []
        elif tag in ('td', 'th') and self.depth == 1 and self.row is not None:
            self.cell = []
        elif tag == 'br' and self.cell is not None:
            self.cell.append(' ')

    def handle_endtag(self, tag):
        if tag == 'table':
            self.depth -= 1
        elif tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None and self.depth == 1:
            self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def html_tables(html):
    p = _Tables()
    p.feed((html or '').replace('\\n', ' '))  # some VLMs write line breaks inside cells as a literal backslash-n
    return [t for t in p.tables if t]


def md_tables(md):
    """GitHub pipe tables in Markdown, separator rows dropped."""
    tables, cur = [], []
    for line in (md or '').splitlines() + ['']:
        s = line.strip()
        if s.startswith('|') and s.count('|') >= 2:
            cells = [c.strip() for c in s.strip('|').split('|')]
            if not all(re.fullmatch(r':?-{2,}:?', c) or c == '' for c in cells) or not cells:
                cur.append(cells)
        elif cur:
            tables.append(cur)
            cur = []
    return tables


def to_text(markup):
    """Markdown or HTML to plain text in emitted order; table rows become lines of cells."""
    s = (markup or '').replace('\\n', '\n')
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', s)
    s = re.sub(r'(?i)</t[dh]>', ' ', s)
    s = re.sub(r'(?i)<br\s*/?>|</(tr|p|div|li|h[1-6]|table)>', '\n', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = unescape(s)
    lines = []
    for line in s.splitlines():
        t = line.strip()
        if re.fullmatch(r'\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?', t):
            continue  # markdown table separator
        t = t.strip('|').replace('|', ' ')
        t = re.sub(r'^\s*#{1,6}\s+', '', t)
        t = re.sub(r'(\*\*|__|`)', '', t)
        t = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', t)  # images
        t = re.sub(r'\$+', ' ', t)
        t = ' '.join(t.split())
        if t:
            lines.append(t)
    return '\n'.join(lines)


def reading_order(boxes):
    """[(x0, y0, x1, y1, text)] from engines that give no order: lines top to bottom, left to right within a line."""
    boxes = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    lines = []
    for b in boxes:
        cy, h = (b[1] + b[3]) / 2, max(1.0, b[3] - b[1])
        if lines and abs(cy - lines[-1]['cy']) < 0.5 * min(h, lines[-1]['h']):
            lines[-1]['items'].append(b)
            lines[-1]['cy'] = (lines[-1]['cy'] + cy) / 2
        else:
            lines.append(dict(cy=cy, h=h, items=[b]))
    return '\n'.join(' '.join(i[4] for i in sorted(l['items'], key=lambda i: i[0])) for l in lines)


if __name__ == '__main__':
    assert html_tables('<table><tr><th>a</th><td>1<br>2</td></tr></table>') == [[['a', '1 2']]]
    assert md_tables('x\n| a | b |\n|---|---|\n| 1 | 2 |\n') == [[['a', 'b'], ['1', '2']]]
    assert to_text('# T\n| a | b |\n|---|---|\n| 1 | 2 |') == 'T\na b\n1 2'
    assert reading_order([(50, 0, 60, 10, 'b'), (0, 1, 10, 11, 'a'), (0, 30, 10, 40, 'c')]) == 'a b\nc'
    print('ok')
    sys.exit(0)
