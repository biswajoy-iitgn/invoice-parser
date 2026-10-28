"""Render the repo's sample invoices for the UI demo and locate each chosen value's box.

Values are read by a person from each PDF (not from eval gold); this script only finds where
they are printed. Personal and financial data are redacted before rendering, and the phone photos
are made from the redacted pages. Output: public/samples/*.jpg and src/samples.json.
Run: ../.venv/bin/python scripts/build_samples.py
"""
import io, json, os, random, re

import fitz
from PIL import Image, ImageChops, ImageFilter, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, '..', 'sample invoices')
OUT_IMG = os.path.join(ROOT, 'public', 'samples')
DPI = 160  # digital pages: value crops in Review scale to 2.2x without blurring
SCAN_DPI = 110  # phone photos carry no boxes, so no crops
THUMB_DPI = 48  # first page about 400 px wide: sharp in 190 px cards on 2x screens, about a tenth of the page's weight

# key: (printed text, normalized value, page, occurrence, confidence, note)
DOCS = {
    'DACHSER': ('DACHSER India Pvt. Ltd.', {
        'invoice_no': ('7044012289', '7044012289', 0, 0, .99),
        'invoice_date': ('21-08-2026', '21 Aug 2026', 0, 0, .98, 'Printed 21-08-2026'),
        'vendor': ('DACHSER INDIA PVT. LTD.', 'DACHSER India Pvt. Ltd.', 0, 0, .97),
        'vendor_gstin': ('24AACCA7791D1ZS', '24AACCA7791D1ZS', 0, 0, .98),
        'container': ('HLXU8364248', 'HLXU8364248', 0, 0, .96),
        'hbl': ('70430050554', '70430050554', 0, 0, .95),
        'mbl': ('HLCUBO12608AWQG7', 'HLCUBO12608AWQG7', 0, 0, .81, 'Format not matched for carrier HLCU'),
        'weight': ('10669', '10669 kg', 0, 1, .94),
        'seal': ('1,120.00', '1120.00', 0, 0, .98),
        'ens': ('3,750.00', '3750.00', 0, 0, .98),
        'sea_freight': ('527,698.80', '527698.80', 0, 0, .97),
        'thc': ('17,500.00', '17500.00', 0, 0, .97),
        'bl_fee': ('5,500.00', '5500.00', 0, 0, .96),
        'transport': ('286,033.50', '286033.50', 0, 0, .78, '"Haulage (delivery)" matched to Transportation at 78%'),
        'other': ('34,250.00', '34250.00', 0, 0, .88, 'Carrier charges origin'),
        'ex_rate': ('96.12000', '96.12', 0, 0, .93, 'USD rate; EUR rate 112.17 also printed'),
        'total': ('875,852.30', '875852.30', 0, 0, .99),
        'cgst': ('44,526.29', '44526.29', 1, 0, .97),
        'sgst': ('44,526.29', '44526.29', 1, 1, .97),
        'gross_total': ('964,904.88', '964904.88', 1, 0, .99, 'Matches amount in words'),
    }),
    'DSV': ('DSV Air & Sea Pvt. Ltd.', {
        'invoice_no': ('IN1AMD261031252', 'IN1AMD261031252', 0, 0, .99),
        'invoice_date': ('31-Aug-26', '31 Aug 2026', 0, 0, .97, 'Printed 31-Aug-26'),
        'vendor': ('DSV Air & Sea Pvt. Ltd.', 'DSV Air & Sea Pvt. Ltd.', 0, 0, .97),
        'vendor_gstin': ('24AACCD3848A1Z5', '24AACCD3848A1Z5', 0, 0, .98),
        'container': ('FFAU4143091', 'FFAU4143091', 0, 0, .96),
        'mbl': ('MEDUAEO12208', 'MEDUAEO12208', 0, 0, .93),
        'hbl': ('AID0019575', 'AID0019575', 0, 1, .79, 'Also printed inside shipment number SAID0019575'),
        'weight': ('9895.000', '9895 kg', 0, 0, .92),
        'sea_freight': ('7,09,856.25', '709856.25', 0, 0, .96),
        'thc': ('15,790.00', '15790.00', 0, 0, .96),
        'bl_fee': ('6,700.00', '6700.00', 0, 0, .95),
        'transport': ('29,810.00', '29810.00', 0, 0, .9, 'Inland/Linehaul/Haulage'),
        'seal': ('982.50', '982.50', 1, 0, .95),
        'ex_rate': ('98.250000', '98.25', 0, 0, .94),
        'total': ('7,67,903.75', '767903.75', 1, 0, .98),
        'cgst': ('22,970.69', '22970.69', 1, 0, .97),
        'sgst': ('22,970.69', '22970.69', 1, 1, .97),
        'gross_total': ('8,13,845.13', '813845.13', 1, 0, .99),
    }),
    'EPT EXIM': ('EPT Exim Services Pvt. Ltd.', {
        'invoice_no': ('ERIA261452', 'ERIA261452', 0, 0, .99),
        'invoice_date': ('08 Sep 2026', '08 Sep 2026', 0, 0, .98),
        'vendor': ('EPT EXIM SERVICES PVT LTD', 'EPT Exim Services Pvt. Ltd.', 0, 0, .96),
        'vendor_gstin': ('24AADCE0916H1Z2', '24AADCE0916H1Z2', 0, 0, .98),
        'container': ('ESDU1420273', 'ESDU1420273', 0, 0, .95),
        'agency': ('1,800.00', '1800.00', 0, 1, .94),
        'transport': ('6,126.00', '6126.00', 0, 1, .92),
        'warehouse': ('14,320.00', '14320.00', 0, 1, .83, 'Two warehouse lines; second line 1000.00 not added'),
        'total': ('23,246.00', '23246.00', 0, 0, .98),
        'cgst': ('2,092.14', '2092.14', 0, 0, .97),
        'sgst': ('2,092.14', '2092.14', 0, 1, .97),
        'gross_total': ('27,430.28', '27430.28', 0, 0, .99),
    }),
    'EPT GLOBAL': ('EPT Global Logistics Pvt. Ltd.', {
        'invoice_no': ('GRIA260934', 'GRIA260934', 0, 0, .99),
        'invoice_date': ('21 Jul 2026', '21 Jul 2026', 0, 0, .98),
        'vendor': ('EPT Global Logistics Pvt Ltd', 'EPT Global Logistics Pvt. Ltd.', 0, 0, .96),
        'vendor_gstin': ('24AABCE7934M1ZD', '24AABCE7934M1ZD', 0, 0, .98),
        'container': ('HLXU8386226', 'HLXU8386226', 0, 0, .97),
        'mbl': ('HLCUBO12606CLPC8', 'HLCUBO12606CLPC8', 0, 0, .95),
        'hbl': ('A260400', 'A260400', 0, 1, .74, 'Same value printed as Job No'),
        'thc': ('18,541.00', '18541.00', 0, 0, .95),
        'bl_fee': ('10,500.00', '10500.00', 0, 0, .95),
        'ens': ('10,296.30', '10296.30', 0, 0, .9, 'USD 35.00 x 3 at 98.06'),
        'seal': ('1,120.00', '1120.00', 0, 0, .95),
        'transport': ('34,250.00', '34250.00', 0, 0, .92, 'Inland Haulage Charges (IHC)'),
        'other': ('3,750.00', '3750.00', 0, 0, .88, 'Security manifest charges'),
        'ex_rate': ('98.06', '98.06', 0, 0, .93),
        'total': ('78,457.30', '78457.30', 0, 0, .98),
        'cgst': ('7,061.16', '7061.16', 0, 0, .97),
        'sgst': ('7,061.16', '7061.16', 0, 1, .97),
        'gross_total': ('92,579.62', '92579.62', 0, 0, .99),
    }),
    'JEENA': ('Jeena & Company', {
        'invoice_no': ('IVJCAMD270007254', 'IVJCAMD270007254', 0, 0, .98),
        'invoice_date': ('19/8/2026', '19 Aug 2026', 0, 0, .95, 'Printed 19/8/2026'),
        'vendor': ('JEENA AND COMPANY', 'Jeena & Company', 0, 0, .94),
        'vendor_gstin': ('24AAAFJ1721H1ZX', '24AAAFJ1721H1ZX', 0, 0, .98),
        'container': ('OOLU8876229', 'OOLU8876229', 0, 0, .9, 'Read from remarks line'),
        'hbl': ('5836209', '5836209', 0, 0, .9),
        'agency': ('3,450.00', '3450.00', 0, 0, .95),
        'warehouse': ('26,900.00', '26900.00', 0, 0, .9, 'CFS charges'),
        'other': ('2,500.00', '2500.00', 0, 0, .86, 'Loading and unloading'),
        'total': ('33,100.00', '33100.00', 0, 0, .98),
        'cgst': ('2,979.00', '2979.00', 0, 0, .97),
        'sgst': ('2,979.00', '2979.00', 0, 1, .97),
        'gross_total': ('39,058.00', '39058.00', 0, 0, .99),
    }),
    'KUEHNE NAGEL': ('Kuehne + Nagel Pvt. Ltd.', {
        'invoice_no': ('AMD1142005', 'AMD1142005', 0, 0, .99),
        'invoice_date': ('13/08/2026', '13 Aug 2026', 0, 1, .82, 'IRN date also 13/08/2026'),
        'vendor': ('KUEHNE NAGEL PRIVATE LIMITED', 'Kuehne + Nagel Pvt. Ltd.', 0, 0, .97),
        'vendor_gstin': ('24AAACK2676H1ZM', '24AAACK2676H1ZM', 0, 0, .98),
        'container': ('TGBU6174371', 'TGBU6174371', 2, 0, .93, 'Found on page 3'),
        'mbl': ('HLCUBO12607AZYT9', 'HLCUBO12607AZYT9', 1, 0, .94),
        'hbl': ('BANQ1075870945', 'BANQ1075870945', 2, 0, .76, '3 house bills listed; first used'),
        'bl_fee': ('15,300.00', '15300.00', 0, 0, .96),
        'ens': ('3,750.00', '3750.00', 0, 0, .9, 'AMS security fee export'),
        'thc': ('18,541.00', '18541.00', 0, 0, .96),
        'sea_freight': ('456,533.74', '456533.74', 0, 0, .97),
        'other': ('31,553.09', '31553.09', 0, 0, .85, 'Emergency bunker surcharge'),
        'ex_rate': ('98.6034', '98.6034', 1, 0, .95),
        'total': ('526,797.83', '526797.83', 2, 0, .98),
        'cgst': ('15,686.16', '15686.16', 2, 0, .97),
        'sgst': ('15,686.16', '15686.16', 2, 1, .97),
        'gross_total': ('558,170.15', '558170.15', 2, 0, .99),
    }),
    'MEGALO': ('Megalo Worldwide Logistics Pvt. Ltd.', {
        'invoice_no': ('AMDTI26-27-0289', 'AMDTI26-27-0289', 0, 0, .99),
        'invoice_date': ('22-Aug-2026', '22 Aug 2026', 0, 0, .97),
        'vendor': ('Megalo Worldwide Logistics Pvt. Ltd.', 'Megalo Worldwide Logistics Pvt. Ltd.', 0, 0, .96),
        'vendor_gstin': ('24AAPCA1490A1Z1', '24AAPCA1490A1Z1', 0, 0, .98),
        'container': ('OOCU9862650', 'OOCU9862650', 0, 0, .96),
        'mbl': ('COSU6462596850', 'COSU6462596850', 0, 0, .95),
        'hbl': ('AMDOE0169', 'AMDOE0169', 0, 0, .95),
        'bl_fee': ('5,500.00', '5500.00', 0, 0, .9),
        'total': ('8,70,836.31', '870836.31', 1, 0, .97, 'Page 2 total before tax'),
        'cgst': ('43,910.28', '43910.28', 1, 0, .96),
        'sgst': ('43,910.28', '43910.28', 1, 1, .96),
        'gross_total': ('9,58,657.00', '958657.00', 1, 1, .98, 'Net payable after 0.13 round-off'),
    }),
}


# ---- privacy: what ships in the app must not carry personal or financial data ----
# Redacted for real (PyMuPDF removes the text, not just covers it): people's names and signature stamps, email addresses, phone numbers,
# bank account numbers, IFSC, SWIFT and MICR codes. Kept: company names, GSTINs and the invoice values the app extracts.
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
PERSON = re.compile(r'\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?')
SPLIT_PHONE = re.compile(r'\+91(?:[ -]\d{2,5}){2,4}')
PHONE_LABEL = re.compile(r'(?i)\b(?:phone|tel|telephone|mob|mobile|fax|ph|contact)\b')
BANK_LABEL = re.compile(r'(?i)(?:\ba/?c\b|acc\.?\s*no|account|ifsc|swift|micr)')
IFSC = re.compile(r'[A-Z]{4}0[A-Z0-9]{6}')
SWIFT = re.compile(r'^[A-Z]{4}IN[A-Z0-9]{2}(?:[A-Z0-9]{3})?$')  # Indian banks' SWIFT codes
REDACT_FILL = (0.86, 0.87, 0.88)


def private_rects(page):
    """Rects of personal and financial data on a page, with what they are."""
    found = []
    words = page.get_text('words')
    lines = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    for ws in lines.values():
        text = ' '.join(w[4] for w in ws)
        for w in ws:
            token = w[4].strip(',;:()')
            digits = sum(ch.isdigit() for ch in token)
            if EMAIL.search(token):
                found.append((fitz.Rect(w[:4]), 'email', token))
            elif token.startswith('+91') and digits >= 10:
                found.append((fitz.Rect(w[:4]), 'phone', token))
            elif PHONE_LABEL.search(text) and digits >= 8:
                found.append((fitz.Rect(w[:4]), 'phone', token))
            elif BANK_LABEL.search(text) and (digits >= 8 or IFSC.search(token) or (re.search(r'(?i)swift', text) and SWIFT.match(token))):
                found.append((fitz.Rect(w[:4]), 'bank', token))
    text = page.get_text()
    for m in PERSON.finditer(text):
        for r in page.search_for(m.group(0)):
            found.append((r, 'name', m.group(0)))
    for x0, y0, x1, y1, block, *_ in page.get_text('blocks'):  # signature stamps carry the signer's name and time
        if re.search(r'(?i)signed by', block):
            found.append((fitz.Rect(x0, y0, x1, y1), 'signer', ' '.join(block.split())))
    for m in SPLIT_PHONE.finditer(text):  # numbers printed as separate words: +91 22 4232 8200
        for r in page.search_for(m.group(0)):
            found.append((r, 'phone', m.group(0)))
    return found


def redact(doc, name, protected):
    """Apply redactions, never over a box the app shows as an extracted value."""
    for i, page in enumerate(doc):
        for w in list(page.widgets()):  # signature fields draw the signer's name from their own appearance
            if w.field_type_string == 'Signature':
                page.add_redact_annot(w.rect, fill=REDACT_FILL)
                page.delete_widget(w)
                print(f'  {name} p{i + 1}: signature field')
        keep = [fitz.Rect(b) for b in protected.get(i, [])]
        for rect, kind, text in private_rects(page):
            if any(rect.intersects(k) for k in keep):
                print(f'  kept {kind} on {name} p{i + 1}: it is an extracted value')
                continue
            page.add_redact_annot(rect + (-1, -1, 1, 1), fill=REDACT_FILL)
            print(f'  {name} p{i + 1}: {kind} {text[:24]}')
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)


def photo(png, seed):
    """A phone photo made from the redacted page: paper tint, slight tilt and perspective, uneven light, a desk."""
    rnd = random.Random(seed)
    page = Image.open(io.BytesIO(png)).convert('RGB')
    w, h = page.size
    paper = ImageChops.multiply(page, Image.new('RGB', page.size, (246, 241, 230)))
    shade = Image.linear_gradient('L').rotate(rnd.choice([30, 45, 60]), expand=False).resize(page.size)
    paper = Image.composite(paper, ImageChops.multiply(paper, Image.new('RGB', page.size, (214, 206, 192))), ImageOps.invert(shade))
    W, H = int(w * 1.08), int(h * 1.035)
    d = lambda: rnd.uniform(0.012, 0.03)
    quad = [(-d() * w, -d() * h), (w * (1 + d()), -d() * h), (w * (1 + d() * 0.5), h * (1 + d())), (-d() * w * 0.5, h * (1 + d()))]
    coeffs = _perspective([(0, 0), (w, 0), (w, h), (0, h)], quad)
    warped = paper.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(0, 0, 0))
    mask = Image.new('L', (w, h), 255).transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=0)
    desk = Image.new('RGB', (W, H), (88, 82, 74))
    desk.paste(warped.rotate(rnd.uniform(-1.4, 1.4), Image.BICUBIC, fillcolor=(88, 82, 74)), ((W - w) // 2, (H - h) // 2),
               mask.rotate(0))
    out = desk.filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    out.save(buf, 'JPEG', quality=72)
    return buf.getvalue(), out.size


def _perspective(src, dst):
    """Coefficients for PIL's PERSPECTIVE transform mapping dst corners back to src."""
    import numpy as np
    a = []
    for (x, y), (u, v) in zip(src, dst):
        a.append([u, v, 1, 0, 0, 0, -x * u, -x * v])
        a.append([0, 0, 0, u, v, 1, -y * u, -y * v])
    b = [c for xy in src for c in xy]
    return list(np.linalg.solve(np.array(a, float), np.array(b, float)))


def render(doc, slug, prefix, dpi=DPI):
    pages = []
    for i, page in enumerate(doc):
        name = f'{prefix}{slug}-{i + 1}.jpg'
        page.get_pixmap(dpi=dpi).save(os.path.join(OUT_IMG, name), jpg_quality=82)
        pages.append({'src': f'/samples/{name}', 'w': round(page.rect.width, 2), 'h': round(page.rect.height, 2)})
        if i == 0:
            thumb = f'{prefix}{slug}-thumb.jpg'
            page.get_pixmap(dpi=THUMB_DPI).save(os.path.join(OUT_IMG, thumb), jpg_quality=80)
            pages[0]['thumb'] = f'/samples/{thumb}'
    return pages


def render_photos(doc, slug):
    pages = []
    for i, page in enumerate(doc):
        jpg, (pw, ph) = photo(page.get_pixmap(dpi=SCAN_DPI).tobytes('png'), f'{slug}-{i}')
        name = f'phone-{slug}-{i + 1}.jpg'
        open(os.path.join(OUT_IMG, name), 'wb').write(jpg)
        pages.append({'src': f'/samples/{name}', 'w': round(page.rect.width * 1.08, 2), 'h': round(page.rect.height * 1.035, 2)})
        if i == 0:
            thumb = Image.open(io.BytesIO(jpg)).resize((round(pw * THUMB_DPI / SCAN_DPI), round(ph * THUMB_DPI / SCAN_DPI)), Image.LANCZOS)
            tname = f'phone-{slug}-thumb.jpg'
            thumb.save(os.path.join(OUT_IMG, tname), 'JPEG', quality=80)
            pages[0]['thumb'] = f'/samples/{tname}'
    return pages


def main():
    os.makedirs(OUT_IMG, exist_ok=True)
    for f in os.listdir(OUT_IMG):
        if f.endswith('.jpg'):
            os.remove(os.path.join(OUT_IMG, f))
    out = []
    for name, (vendor, fields) in DOCS.items():
        slug = name.lower().replace(' ', '-')
        doc = fitz.open(os.path.join(SRC, f'{name}.pdf'))
        rec = {'id': f'doc-{slug}', 'name': f'{name}.pdf', 'vendor': vendor, 'kind': 'vector', 'fields': []}
        protected = {}
        for key, spec in fields.items():
            text, value, page, occ, conf, *note = spec
            hits = doc[page].search_for(text)
            assert len(hits) > occ, f'{name}/{key}: "{text}" found {len(hits)}x on page {page + 1}'
            r = hits[occ]
            protected.setdefault(page, []).append(r)
            rec['fields'].append({'key': key, 'value': value, 'conf': conf, 'page': page,
                                  'box': [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)],
                                  **({'note': note[0]} if note else {})})
        redact(doc, name, protected)
        rec['pages'] = render(doc, slug, '')
        out.append(rec)
        # The phone photo is made from the redacted page, so no real photo (or held-out eval data) ships.
        out.append({'id': f'scan-{slug}', 'name': f"{name.replace(' ', '_')}_phone.jpg", 'vendor': vendor, 'kind': 'scan',
                    'pages': render_photos(doc, slug), 'twin': rec['id']})
    json.dump(out, open(os.path.join(ROOT, 'src', 'samples.json'), 'w'), indent=1)
    print(len(out), 'documents')


if __name__ == '__main__':
    main()
