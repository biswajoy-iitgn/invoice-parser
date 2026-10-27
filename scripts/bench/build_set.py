"""Test set for the parser bake-off: page images plus an answer key taken from each digital PDF's own text layer.

Conditions:
  digital  the PDF page rendered at 200 dpi (answer key matches pixel for pixel)
  office   the real office scan of the same page (eval/real_invoices/scanned/*__office.pdf)
  phone    the real phone capture (invoices) or a generated phone photo (contracts)

The answer key never reads eval/real_invoices/gold.json. Output: work/bench/set/{images,truth}/, manifest.json.
"""
import io, json, os, sys

import fitz
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, 'work', 'bench', 'set')
DPI = 200
INVOICES = ['DACHSER', 'DSV', 'EPT EXIM', 'EPT GLOBAL', 'JEENA', 'KUEHNE NAGEL', 'MEGALO']
# 0-based pages: dense clauses, a signature block with a schedule, and definitions (the schedules' cells are redacted)
CONTRACTS = {'neurobo_manufacturing': [1, 2, 6], 'reynolds_service': [0, 1, 2]}
sys.path.insert(0, os.path.join(ROOT, 'UI', 'scripts'))
from build_samples import photo  # noqa: E402  same phone-photo model as the UI samples


def truth(page):
    """Words with boxes (PDF points), lines in reading order, embedded images, ruled tables."""
    words = [dict(bbox=[round(v, 2) for v in w[:4]], text=w[4], block=w[5], line=w[6]) for w in page.get_text('words', sort=True)]
    lines, cur = [], None
    for w in words:
        key = (w['block'], w['line'])
        if key != cur:
            lines.append([])
            cur = key
        lines[-1].append(w['text'])
    blocks = [dict(bbox=[round(v, 2) for v in b[:4]], text=b[4].strip()) for b in page.get_text('blocks', sort=True) if b[6] == 0 and b[4].strip()]
    images = [[round(v, 2) for v in i['bbox']] for i in page.get_image_info() if (i['bbox'][2] - i['bbox'][0]) * (i['bbox'][3] - i['bbox'][1]) > 400]
    tables = []
    for t in page.find_tables().tables:
        rows = [[(c or '').replace('\n', ' ').strip() for c in r] for r in t.extract()]
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
            tables.append(dict(bbox=[round(v, 2) for v in t.bbox], rows=rows))
    return dict(size=[page.rect.width, page.rect.height], words=words, lines=[' '.join(l) for l in lines], blocks=blocks, images=images, tables=tables)


def png(pix):
    return pix.tobytes('png')


def main():
    os.makedirs(os.path.join(OUT, 'images'), exist_ok=True)
    os.makedirs(os.path.join(OUT, 'truth'), exist_ok=True)
    items = []

    def add(item_id, img, doc, kind, cond, page, truth_id, extra=None):
        path = os.path.join(OUT, 'images', f'{item_id}.png')
        img.save(path)
        items.append(dict(id=item_id, image=os.path.relpath(path, ROOT), doc=doc, kind=kind, condition=cond, page=page,
                          truth=truth_id, width=img.width, height=img.height, **(extra or {})))

    def save_truth(truth_id, page):
        with open(os.path.join(OUT, 'truth', f'{truth_id}.json'), 'w') as f:
            json.dump(truth(page), f)

    for name in INVOICES:
        slug = name.lower().replace(' ', '_')
        pdf = fitz.open(os.path.join(ROOT, 'sample invoices', f'{name}.pdf'))
        for i, page in enumerate(pdf):
            tid = f'{slug}-p{i + 1}'
            save_truth(tid, page)
            img = Image.open(io.BytesIO(png(page.get_pixmap(dpi=DPI)))).convert('RGB')
            add(f'{tid}-digital', img, slug, 'invoice', 'digital', i, tid, dict(pdf=os.path.relpath(pdf.name, ROOT), dpi=DPI))
        for cond in ('office', 'phone'):  # real captures: first page only
            scan = fitz.open(os.path.join(ROOT, 'eval', 'real_invoices', 'scanned', f"REAL_{name.replace(' ', '_')}__{cond}.pdf"))
            info = scan[0].get_images()[0]
            img = Image.open(io.BytesIO(scan.extract_image(info[0])['image'])).convert('RGB')
            add(f'{slug}-p1-{cond}', img, slug, 'invoice', cond, 0, f'{slug}-p1')

    for slug, pages in CONTRACTS.items():
        pdf = fitz.open(os.path.join(ROOT, 'work', 'bench', 'contracts', f'{slug}.pdf'))
        for i in pages:
            tid = f'{slug}-p{i + 1}'
            page = pdf[i]
            save_truth(tid, page)
            raw = png(page.get_pixmap(dpi=DPI))
            add(f'{tid}-digital', Image.open(io.BytesIO(raw)).convert('RGB'), slug, 'contract', 'digital', i, tid,
                dict(pdf=os.path.relpath(pdf.name, ROOT), dpi=DPI))
            add(f'{tid}-phone', Image.open(io.BytesIO(photo(png(page.get_pixmap(dpi=150)), seed=i)[0])).convert('RGB'), slug, 'contract', 'phone', i, tid)

    with open(os.path.join(OUT, 'manifest.json'), 'w') as f:
        json.dump(items, f, indent=1)
    by = {}
    for it in items:
        by[(it['kind'], it['condition'])] = by.get((it['kind'], it['condition']), 0) + 1
    print(len(items), 'images:', by)


if __name__ == '__main__':
    main()
