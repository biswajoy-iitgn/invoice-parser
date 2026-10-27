"""Scores table recognition on the FinTabNet crops (kind 'table' in the BENCH_DIR manifest) against gold.json.

Run with work/bench/venv/bin/python, BENCH_DIR=work/bench2. Writes work/bench2/table_scores.json and prints a Markdown table.
Gold and output go through the same parser (common.html_tables / the runners' grids), which drops spans: a spanned cell
counts once, so the comparison is fair to pipelines that repeat or omit span cells.

  teds      tree-edit similarity of table > row > cell trees, cell rename cost = normalised edit distance of the text
  teds_s    the same with every cell's text blanked: structure only
  cell_f1   multiset F1 of non-empty cell texts (order and position ignored): did every value come out
  row_acc   gold rows whose cells appear as one output row in order (text equal after normalisation)
"""
import json, os, re, sys
from collections import Counter, defaultdict

from apted import APTED, Config
from apted.helpers import Tree
from rapidfuzz.distance import Levenshtein

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BENCH, OUT, SET, html_tables, manifest  # noqa: E402


def norm(s):
    s = re.sub(r'<[^>]+>', '', s or '')  # gold keeps footnote markup such as <sup>(1)</sup> as text; drop it everywhere
    return re.sub(r'[\s$,]', '', s).lower()


class Cfg(Config):
    def rename(self, a, b):
        ta, tb = a.name, b.name
        if not (ta.startswith('c:') and tb.startswith('c:')):
            return 0 if ta == tb else 1
        x, y = ta[2:], tb[2:]
        if not x and not y:
            return 0
        return Levenshtein.normalized_distance(x, y)

    def children(self, node):
        return node.children


def tree(rows, content=True):
    t = Tree('table')
    for r in rows:
        tr = Tree('tr')
        for c in r:
            tr.children.append(Tree('c:' + (norm(c) if content else '')))
        t.children.append(tr)
    return t


def size(t):
    return 1 + sum(size(c) for c in t.children)


def teds(a, b, content=True):
    ta, tb = tree(a, content), tree(b, content)
    n = max(size(ta), size(tb))
    return 1 - APTED(ta, tb, Cfg()).compute_edit_distance() / n


def cell_f1(g, p):
    gc, pc = Counter(norm(c) for r in g for c in r if norm(c)), Counter(norm(c) for r in p for c in r if norm(c))
    hit = sum((gc & pc).values())
    if not hit:
        return 0.0
    pr, rc = hit / sum(pc.values()), hit / sum(gc.values())
    return 2 * pr * rc / (pr + rc)


def row_acc(g, p):
    prows = [[norm(c) for c in r if norm(c)] for r in p]
    grows = [[norm(c) for c in r if norm(c)] for r in g]
    grows = [r for r in grows if r]
    return sum(r in prows for r in grows) / max(1, len(grows))


def best_table(gold, tables):
    """The output table that matches best: pipelines may split one table in two or add stray ones."""
    best = None
    for t in tables or []:
        rows = [r for r in t if any(c.strip() for c in r)]
        if rows:
            s = (cell_f1(gold, rows), rows)
            if best is None or s[0] > best[0]:
                best = s
    return best[1] if best else []


def main():
    gold = {g['id']: html_tables(g['html'])[0] for g in json.load(open(os.path.join(SET, 'tables', 'gold.json')))}
    items = {i['id']: i for i in manifest(kind='table')}
    rows = []
    for pipe in sorted(d for d in os.listdir(OUT) if os.path.isdir(os.path.join(OUT, d))):
        for fn in sorted(os.listdir(os.path.join(OUT, pipe))):
            iid = fn[:-5]
            if iid not in items:
                continue
            item, out = items[iid], json.load(open(os.path.join(OUT, pipe, fn)))
            g, p = gold[item['doc']], best_table(gold[item['doc']], out.get('tables'))
            rows.append(dict(pipeline=pipe, item=iid, condition=item['condition'], error=bool(out.get('error')), seconds=out.get('seconds'),
                             found=float(bool(p)), teds=teds(g, p) if p else 0.0, teds_s=teds(g, p, False) if p else 0.0,
                             cell_f1=cell_f1(g, p), row_acc=row_acc(g, p)))
    json.dump(rows, open(os.path.join(BENCH, 'table_scores.json'), 'w'), indent=1)
    groups = defaultdict(list)
    for r in rows:
        groups[(r['pipeline'], r['condition'])].append(r)
    cols = ['found', 'teds', 'teds_s', 'cell_f1', 'row_acc', 'seconds']
    print('| pipeline | capture | n | ' + ' | '.join(cols) + ' |\n|' + '---|' * (3 + len(cols)))
    for (pipe, cond), rs in sorted(groups.items(), key=lambda kv: (kv[0][1], -sum(r['teds'] for r in kv[1]) / len(kv[1]))):
        vals = []
        for c in cols:
            v = [r[c] for r in rs if r[c] is not None]
            m = sum(v) / len(v) if v else None
            vals.append('' if m is None else f'{m:.1f}' if c == 'seconds' else f'{100 * m:.0f}')
        print(f'| {pipe} | {cond} | {len(rs)} | ' + ' | '.join(vals) + ' |')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'selftest':
        g = [['a', '1'], ['b', '2']]
        assert teds(g, g) == 1.0 and teds(g, [['a', '1']]) < 1 and cell_f1(g, g) == 1.0 and row_acc(g, [['b', '2'], ['a', '1']]) == 1.0
        print('ok')
    else:
        main()
