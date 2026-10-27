"""Second bake-off set: quality certificates, a roster grid, gate passes. Same layout as build_set.py so score.py runs on it
with BENCH_DIR=work/bench2.

  quality   real public PDFs (Yucel Boru EN 10204 3.1 mill certificates x2, Haas first-article inspection sample)
  roster    Smartsheet weekly staff roster example (16 employees x 7 days)
  gatepass  SYNTHETIC: generated here with known values (no filled real gate pass could be found online)

Answer keys come from each PDF's text layer (fields and table rows are asserted against it). Conditions: digital render at
200 dpi, and a generated phone photo (same model as the UI samples). Output: work/bench2/set/{images,truth}, manifest.json, answers.json.
"""
import io, json, os, random, re, sys, zlib

import fitz
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'work', 'bench2', 'set')
RAW = os.path.join(ROOT, 'work', 'bench2', 'raw', 'docs')
sys.path.insert(0, os.path.join(ROOT, 'UI', 'scripts'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_set import truth  # noqa: E402
from build_samples import photo  # noqa: E402


def lines_of(page, tol=3):
    """Words grouped into visual lines (top to bottom), each left to right."""
    rows = {}
    for w in page.get_text('words'):
        rows.setdefault(round(w[3] / tol), []).append(w)
    return [sorted(r, key=lambda w: w[0]) for _, r in sorted(rows.items())]


def mtc(pdf):
    """Mill certificate: header fields and the test-results table (rows start with a size like 1.2x1250x2500)."""
    page = fitz.open(pdf)[0]
    ls = [[w[4] for w in l] for l in lines_of(page)]
    f = {}
    for l in ls:
        s = ' '.join(l)
        m = re.match(r'Customer (.+?) Order(?:/PO)? (.+?) Date (\S+)$', s)
        if m:
            f['customer'] = ['Customer', m[1]]
            f['order'] = ['Order', m[2]]
            f['date'] = ['Date', m[3]]
        m = re.match(r'Sending (.+?) Report (\S+) Page', s)
        if m:
            f['report'] = ['Report', m[2]]
    rows = []
    for l in ls:
        if re.match(r'^\d+(\.\d+)?x\d+', l[0]) and len(l) >= 20:
            rows.append(l)
    # the standard ("EN 10219-1:2006") is one cell; the heat number is the cell before it
    cells = [r[:-2] + [' '.join(r[-2:])] if r[-2] == 'EN' else r for r in rows]
    for i, r in enumerate(cells):
        f[f'coil_{i}'] = ['Ref.No.', next(c for c in r if re.fullmatch(r'\d{10}', c))]
        f[f'heat_{i}'] = ['Heat Number', r[-2]]
    return f, cells


def haas():
    f = {'part_number': ['Part Number', '20-5537'], 'part_name': ['Part Name', 'C-Axis Engage Sensor Mount'],
         'revision': ['Revision', 'A'], 'document_no': ['Document No.', '95-61132']}
    text = ' '.join(w[4] for w in fitz.open(os.path.join(RAW, 'qual_haas_fai.pdf'))[0].get_text('words'))
    return {k: v for k, v in f.items() if v[1].replace(' ', '') in text.replace(' ', '')}, None


def roster():
    page = fitz.open(os.path.join(RAW, 'roster_smartsheet.pdf'))[0]
    words = page.get_text('words')
    days = {w[4]: (w[0] + w[2]) / 2 for w in words if w[4] in ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')}
    order = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    xs = [days[d] for d in order]
    edges = [(xs[i] + xs[i + 1]) / 2 for i in range(6)]
    day_of = lambda x: sum(x > e for e in edges)
    ids = sorted([w for w in words if re.fullmatch(r'E10\d\d', w[4])], key=lambda w: w[1])
    shifts = sorted([w for w in words if w[4] == 'Shift' and w[0] < 260 and w[1] > 160], key=lambda w: w[1])
    hours = sorted([w for w in words if w[4] == 'Hours' and w[0] < 260 and w[1] > 160], key=lambda w: w[1])
    assert len(ids) == len(shifts) == len(hours) == 16, (len(ids), len(shifts), len(hours))
    f, rows = {}, []
    body = [w for w in words if w[0] > 236 and w[0] < 1048]
    for n, (i, s, h) in enumerate(zip(ids, shifts, hours)):
        def line(y, w_):
            return [w for w in body if abs((w[1] + w[3]) / 2 - (y[1] + y[3]) / 2) < 5]
        cells_s = ['' for _ in range(7)]
        for w in sorted(line(s, body), key=lambda w: w[0]):
            cells_s[day_of((w[0] + w[2]) / 2)] += (' ' if cells_s[day_of((w[0] + w[2]) / 2)] else '') + w[4]
        cells_h = ['' for _ in range(7)]
        for w in sorted(line(h, body), key=lambda w: w[0]):
            cells_h[day_of((w[0] + w[2]) / 2)] += (' ' if cells_h[day_of((w[0] + w[2]) / 2)] else '') + w[4]
        # the employee's own columns: rate, total hours, total pay sit on the id / pay line
        eline = [w for w in words if abs((w[1] + w[3]) / 2 - (i[1] + i[3]) / 2) < 5]
        rate = next(w[4] for w in eline if w[4].startswith('$') and w[0] < 236)
        tail = sorted([w for w in words if w[0] >= 1048 and abs((w[1] + w[3]) / 2 - (i[1] + i[3]) / 2) < 6], key=lambda w: w[0])
        tot_h, tot_p = tail[0][4], tail[1][4]
        f[f'{i[4]}_rate'] = [f'{i[4]} Hourly Rate', rate]
        f[f'{i[4]}_hours'] = [f'{i[4]} Total Hours', tot_h]
        f[f'{i[4]}_pay'] = [f'{i[4]} Total Pay', tot_p]
        rows.append([i[4]] + cells_s)
        rows.append([i[4]] + cells_h)
    f['department'] = ['Department Name', 'Research and Development']
    f['week_total_hours'] = ['Weekly Total Hours', '568']
    f['week_total_pay'] = ['Weekly Total Pay', '$26,855.48']
    f['scheduler'] = ['Scheduler', 'Diana Kennedy']
    return f, rows


# ---- synthetic gate passes ------------------------------------------------------------------------------------
NAMES = ['Ramesh Patel', 'Sunil Kumar Yadav', 'Mohammed Irfan', 'Vijay Rathod', 'Anil Desai', 'Karan Solanki', 'Prakash Chauhan', 'Imran Shaikh']
ITEMS = [('Hydraulic cylinder 80x400', 'Nos'), ('Bearing 6205-2RS', 'Nos'), ('Gear box repair', 'Nos'), ('MS flat 50x6', 'Kg'),
         ('Calibration gauge set', 'Set'), ('Welding electrodes 3.15mm', 'Box'), ('Test sample bar', 'Nos'), ('Coolant drum 200L', 'Nos')]
DEPTS = ['Maintenance', 'Stores', 'Quality Assurance', 'Fabrication', 'Dispatch']
DESTS = ['Shree Engineering Works, Rajkot', 'Anand Calibration Lab, Vadodara', 'Om Hydraulics, Ahmedabad', 'Siddhi Steel Traders, Sanand']


def gatepass_pdf(seed):
    rnd = random.Random(seed)
    layout = seed % 2
    no = f'GP/{rnd.choice(["24", "25", "26"])}/{rnd.randint(1000, 9999)}'
    date = f'{rnd.randint(1, 28):02d}/{rnd.randint(1, 12):02d}/2026'
    veh = f'GJ-{rnd.randint(1, 38):02d}-{rnd.choice("ABCDEFGHJKLMNPRSTUVXYZ")}{rnd.choice("ABCDEFGHJKLMNPRSTUVXYZ")}-{rnd.randint(1000, 9999)}'
    drv, mob = rnd.choice(NAMES), f'9{rnd.randint(100000000, 999999999)}'
    issuer, dept, dest = rnd.choice(NAMES), rnd.choice(DEPTS), rnd.choice(DESTS)
    kind = rnd.choice(['RETURNABLE', 'NON-RETURNABLE'])
    ret = f'{rnd.randint(1, 28):02d}/{rnd.randint(1, 12):02d}/2026' if kind == 'RETURNABLE' else ''
    items = [(n, str(rnd.randint(1, 40)), u) for n, u in rnd.sample(ITEMS, rnd.randint(2, 4))]
    rows = ''.join(f'<tr><td>{i + 1}</td><td>{n}</td><td>{q}</td><td>{u}</td></tr>' for i, (n, q, u) in enumerate(items))
    css = ('body{font-family:Helvetica;font-size:10pt} table{border-collapse:collapse;width:100%} td,th{border:1px solid #000;padding:4px;text-align:left}'
           ' h2{text-align:center;margin:0} .k{font-weight:bold} p{margin:3px 0}')
    head = '<h2>HARSHA INDUSTRIAL WORKS (SAMPLE)</h2><p style="text-align:center">Sarkhej-Bavla Highway, Ahmedabad</p><h2>MATERIAL GATE PASS - ' + kind + '</h2>'
    if layout == 0:
        meta = (f'<table><tr><td class="k">Gate Pass No.</td><td>{no}</td><td class="k">Date</td><td>{date}</td></tr>'
                f'<tr><td class="k">Vehicle No.</td><td>{veh}</td><td class="k">Driver Name</td><td>{drv}</td></tr>'
                f'<tr><td class="k">Driver Mobile</td><td>{mob}</td><td class="k">Department</td><td>{dept}</td></tr>'
                f'<tr><td class="k">Destination</td><td colspan="3">{dest}</td></tr>'
                + (f'<tr><td class="k">Expected Return</td><td colspan="3">{ret}</td></tr>' if ret else '') + '</table>')
    else:
        meta = (f'<p><span class="k">Pass No:</span> {no} &nbsp;&nbsp; <span class="k">Date:</span> {date}</p>'
                f'<p><span class="k">Issued To (Driver):</span> {drv} ({mob})</p><p><span class="k">Vehicle Number:</span> {veh}</p>'
                f'<p><span class="k">Dept:</span> {dept}</p><p><span class="k">Going To:</span> {dest}</p>'
                + (f'<p><span class="k">Return By:</span> {ret}</p>' if ret else ''))
    body = (head + meta + '<p>&nbsp;</p><table><tr><th>Sr</th><th>Description of Material</th><th>Qty</th><th>Unit</th></tr>' + rows + '</table>'
            f'<p>&nbsp;</p><table><tr><td class="k">Issued By</td><td>{issuer}</td><td class="k">Security Sign</td><td>&nbsp;</td></tr></table>')
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_htmlbox(fitz.Rect(36, 36, 559, 806), body, css=css)
    fields = {'gate_pass_no': ['Gate Pass No.', no], 'date': ['Date', date], 'vehicle': ['Vehicle No.', veh], 'driver': ['Driver Name', drv],
              'mobile': ['Driver Mobile', mob], 'department': ['Department', dept], 'destination': ['Destination', dest], 'issued_by': ['Issued By', issuer]}
    if ret:
        fields['return'] = ['Expected Return', ret]
    tab = [[str(i + 1), n, q, u] for i, (n, q, u) in enumerate(items)]
    return doc, fields, tab


def main():
    os.makedirs(os.path.join(OUT, 'images'), exist_ok=True)
    os.makedirs(os.path.join(OUT, 'truth'), exist_ok=True)
    items, answers = [], dict(tables={}, fields={})

    def add(doc_id, kind, page, f, rows, label):
        tid = f'{doc_id}-p1'
        with open(os.path.join(OUT, 'truth', f'{tid}.json'), 'w') as fh:
            json.dump(truth(page), fh)
        text = ' '.join(w[4] for w in page.get_text('words')).replace(' ', '')
        for k, (_, v) in f.items():
            assert v.replace(' ', '') in text, (doc_id, k, v)
        if f:
            answers['fields'][doc_id] = f
        if rows:
            answers['tables'][tid] = rows
        for cond, raw in (('digital', page.get_pixmap(dpi=200).tobytes('png')), ('phone', photo(page.get_pixmap(dpi=150).tobytes('png'), seed=zlib.crc32(doc_id.encode()) % 97)[0])):
            img = Image.open(io.BytesIO(raw)).convert('RGB')
            path = os.path.join(OUT, 'images', f'{tid}-{cond}.png')
            img.save(path)
            items.append(dict(id=f'{tid}-{cond}', image=os.path.relpath(path, ROOT), doc=doc_id, kind=kind, condition=cond, page=0, truth=tid,
                              width=img.width, height=img.height, label=label, **({'dpi': 200} if cond == 'digital' else {})))

    for n in (1, 2):
        f, rows = mtc(os.path.join(RAW, f'qual_mtc_{n}.pdf'))
        add(f'mtc{n}', 'quality', fitz.open(os.path.join(RAW, f'qual_mtc_{n}.pdf'))[0], f, rows, 'EN 10204 3.1 mill test certificate')
    f, _ = haas()
    add('haas_fai', 'quality', fitz.open(os.path.join(RAW, 'qual_haas_fai.pdf'))[0], f, None, 'first-article inspection report (sample)')
    f, rows = roster()
    add('roster', 'roster', fitz.open(os.path.join(RAW, 'roster_smartsheet.pdf'))[0], f, rows, 'weekly staff roster grid')
    for s in range(6):
        doc, f, rows = gatepass_pdf(s)
        add(f'gatepass{s + 1}', 'gatepass', doc[0], f, rows, 'synthetic gate pass')
    json.dump(items, open(os.path.join(OUT, 'manifest.json'), 'w'), indent=1)
    json.dump(answers, open(os.path.join(OUT, 'answers.json'), 'w'), indent=1)
    print(len(items), 'images;', {k: len(v) for k, v in answers.items()})


if __name__ == '__main__':
    main()
