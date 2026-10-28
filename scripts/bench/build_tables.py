"""Pick complex FinTabNet tables (CDLA-Permissive) and write images + gold HTML (rowspan/colspan, cell text)
into work/bench2/set/tables. Usage: .venv/bin/python scripts/bench/build_tables.py [count]"""
import html as H, json, os, random, sys
import pyarrow.parquet as pq

SRC = 'work/bench2/raw/fintabnet/test-00000.parquet'
OUT = 'work/bench2/set/tables'


def otsl_to_html(otsl, texts):
    rows, cur = [], []
    for t in otsl:
        if t == 'nl':
            rows.append(cur); cur = []
        else:
            cur.append(t)
    it, out = iter(texts), []
    for r, row in enumerate(rows):
        out.append('<tr>')
        for c, t in enumerate(row):
            if t not in ('fcel', 'ecel'):
                continue
            cs = 1
            while c + cs < len(row) and row[c + cs] in ('lcel', 'xcel'):
                cs += 1
            rs = 1
            while r + rs < len(rows) and c < len(rows[r + rs]) and rows[r + rs][c] in ('ucel', 'xcel'):
                rs += 1
            a = (f' rowspan="{rs}"' if rs > 1 else '') + (f' colspan="{cs}"' if cs > 1 else '')
            out.append(f'<td{a}>{H.escape(next(it))}</td>')
        out.append('</tr>')
    assert next(it, None) is None, 'cells left over'
    return '<table>' + ''.join(out) + '</table>'


SCALE = 3  # FinTabNet crops are about 460 px wide (roughly 70 dpi); a 200 dpi page render is 3x that, so crops are upscaled 3x (Lanczos)


def add_to_manifest(gold, phone=15):
    """Table crops join work/bench2/set/manifest.json as kind 'table' (score.py skips them; score_tables.py scores them).
    The first `phone` crops also get a generated phone-photo version."""
    import io, sys
    from PIL import Image
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(root, 'UI', 'scripts'))
    from build_samples import photo
    mpath = os.path.join(root, 'work/bench2/set/manifest.json')
    items = [i for i in json.load(open(mpath)) if i['kind'] != 'table']
    for n, g in enumerate(gold):
        im = Image.open(f"{OUT}/{g['id']}.png").convert('RGB')
        im.resize((im.width * SCALE, im.height * SCALE), Image.LANCZOS).save(f"{OUT}/{g['id']}.png")
        for cond in ('digital', 'phone'):
            if cond == 'phone' and n >= phone:
                continue
            path = f"{OUT}/{g['id']}.png" if cond == 'digital' else f"{OUT}/{g['id']}-phone.png"
            if cond == 'phone':
                Image.open(io.BytesIO(photo(open(f"{OUT}/{g['id']}.png", 'rb').read(), seed=n)[0])).convert('RGB').save(path)
            w, h = Image.open(path).size
            items.append(dict(id=f"{g['id']}-{cond}", image=os.path.relpath(path, root), doc=g['id'], kind='table', condition=cond, page=0,
                              truth=None, width=w, height=h))
    json.dump(items, open(mpath, 'w'), indent=1)


def main(n=40):
    os.makedirs(OUT, exist_ok=True)
    f = pq.ParquetFile(SRC)
    cands = []
    for g in range(f.num_row_groups):
        t = f.read_row_group(g, columns=['filename', 'otsl', 'rows', 'cols'])
        for i in range(t.num_rows):
            o, r, c = t['otsl'][i].as_py(), t['rows'][i].as_py(), t['cols'][i].as_py()
            if sum(x in ('lcel', 'ucel', 'xcel') for x in o) >= 3 and 8 <= r <= 28 and 4 <= c <= 12:
                cands.append((g, i))
    random.seed(7)
    need = {}
    for g, i in random.sample(cands, min(n, len(cands))):
        need.setdefault(g, []).append(i)
    gold = []
    for g, idx in need.items():
        t = f.read_row_group(g)
        for i in idx:
            row = {k: t[k][i].as_py() for k in ('filename', 'otsl', 'cells', 'rows', 'cols')}
            texts = ["".join(c['tokens']) for r in row['cells'] for c in r]
            try:
                h = otsl_to_html(row['otsl'], texts)
            except (AssertionError, StopIteration):
                continue
            name = f'ftn{len(gold):02d}'
            open(f'{OUT}/{name}.png', 'wb').write(t['image'][i].as_py()['bytes'])
            gold.append({'id': name, 'src': row['filename'], 'rows': row['rows'], 'cols': row['cols'], 'html': h})
    json.dump(gold, open(f'{OUT}/gold.json', 'w'))
    add_to_manifest(gold)
    print(len(gold), 'tables;', gold[0]['html'][:400])


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
