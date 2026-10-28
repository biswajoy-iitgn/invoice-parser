"""Plain OCR engines, run with work/bench/venv/bin/python.

  tesseract  Tesseract 5 LSTM (--oem 1), automatic page segmentation (--psm 3); its own block and line order
  easyocr    EasyOCR (CRAFT detector + english_g2 recognizer); lines ordered top to bottom, left to right
Usage: run_ocr.py ENGINE
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import image_path, reading_order, run  # noqa: E402


def main():
    engine = sys.argv[1]
    if engine == 'tesseract':
        import pytesseract
        from PIL import Image

        def fn(item):
            d = pytesseract.image_to_data(Image.open(image_path(item)), lang='eng', config='--oem 1 --psm 3', output_type=pytesseract.Output.DICT)
            lines, words, blocks = {}, [], {}
            for i, t in enumerate(d['text']):
                if not t.strip():
                    continue
                key = (d['block_num'][i], d['par_num'][i], d['line_num'][i])
                lines.setdefault(key, []).append(t)
                x, y, w, h = d['left'][i], d['top'][i], d['width'][i], d['height'][i]
                words.append(dict(bbox=[x, y, x + w, y + h], text=t))
                b = blocks.setdefault(d['block_num'][i], [x, y, x + w, y + h])
                blocks[d['block_num'][i]] = [min(b[0], x), min(b[1], y), max(b[2], x + w), max(b[3], y + h)]
            return dict(text='\n'.join(' '.join(v) for v in lines.values()), tables=[], words=words,
                        regions=[dict(bbox=b, label='text') for b in blocks.values()])
    elif engine == 'easyocr':
        import easyocr
        reader = easyocr.Reader(['en'], gpu=True)

        def fn(item):
            res = reader.readtext(image_path(item))
            boxes = [(min(p[0] for p in b), min(p[1] for p in b), max(p[0] for p in b), max(p[1] for p in b), t) for b, t, _ in res if t.strip()]
            boxes = [tuple(float(v) for v in b[:4]) + (b[4],) for b in boxes]
            return dict(text=reading_order(boxes), tables=[], regions=[], words=[dict(bbox=list(b[:4]), text=b[4]) for b in boxes])
    else:
        raise SystemExit(f'unknown engine {engine}')
    run(engine, fn)


if __name__ == '__main__':
    main()
