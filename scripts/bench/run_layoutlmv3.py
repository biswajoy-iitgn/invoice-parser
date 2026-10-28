"""LayoutLMv3 in its proper role, run with work/bench/venv/bin/python: it labels OCR words, it does not find regions or
read pixels into text. Words and boxes come from the PP-OCRv6 run (out/ppocr); the FUNSD fine-tune
(nielsr/layoutlmv3-finetuned-funsd, CC-BY-NC-SA, non-commercial) tags each word HEADER / QUESTION (a key) /
ANSWER (a value) / O.

Scored on the hand-checked invoice fields: a field counts when its printed label comes out as QUESTION and its value
as ANSWER. Writes work/bench/layoutlmv3.json and prints the summary.
"""
import json, os, re, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import OUT, ROOT, SET, image_path, manifest  # noqa: E402

MODEL = 'nielsr/layoutlmv3-finetuned-funsd'
CHUNK = 180  # words per window: keeps each window under LayoutLMv3's 512 tokens


def norm(s):
    return re.sub(r'\s+', '', s).lower().strip('.:;,')


def split_lines(lines):
    """PP-OCR gives text lines; LayoutLMv3 works on words. Each word gets the line's box, narrowed by character position."""
    out = []
    for l in lines:
        x0, y0, x1, y1 = l['bbox']
        text = l['text']
        n = max(1, len(text))
        for m in re.finditer(r'\S+', text):
            out.append(dict(text=m.group(), bbox=[x0 + (x1 - x0) * m.start() / n, y0, x0 + (x1 - x0) * m.end() / n, y1]))
    return out


def main():
    import torch
    from PIL import Image
    from transformers import AutoModelForTokenClassification, LayoutLMv3ImageProcessor, LayoutLMv3Processor, LayoutLMv3TokenizerFast

    dev = 'mps' if torch.backends.mps.is_available() else 'cpu'
    # Built by hand: AutoProcessor (Transformers 5.14) picks a plain RoBERTa tokenizer that silently drops the boxes
    proc = LayoutLMv3Processor(image_processor=LayoutLMv3ImageProcessor(apply_ocr=False), tokenizer=LayoutLMv3TokenizerFast.from_pretrained(MODEL))
    model = AutoModelForTokenClassification.from_pretrained(MODEL).to(dev).eval()
    id2label = model.config.id2label
    answers = json.load(open(os.path.join(SET, 'answers.json')))
    results = []
    for item in manifest(kind='invoice'):
        if item['page'] != 0:
            continue
        src = os.path.join(OUT, 'ppocr', f"{item['id']}.json")
        if not os.path.exists(src):
            print('no PP-OCRv6 words for', item['id'])
            continue
        words = split_lines(json.load(open(src)).get('words') or [])
        img = Image.open(image_path(item)).convert('RGB')
        W, H = img.size
        labels = []
        for i in range(0, len(words), CHUNK):
            chunk = words[i:i + CHUNK]
            boxes = [[max(0, min(1000, int(v))) for v in (w['bbox'][0] / W * 1000, w['bbox'][1] / H * 1000, w['bbox'][2] / W * 1000, w['bbox'][3] / H * 1000)] for w in chunk]
            enc = proc(img, [w['text'] for w in chunk], boxes=boxes, truncation=True, max_length=512, return_tensors='pt')
            assert 'bbox' in enc, 'word boxes must reach the model'
            word_ids = enc.word_ids(0)
            with torch.no_grad():
                logits = model(**{k: v.to(dev) for k, v in enc.items()}).logits[0].cpu()
            first = {}
            for t, wid in enumerate(word_ids):
                if wid is not None and wid not in first:
                    first[wid] = id2label[int(logits[t].argmax())]
            labels += [first.get(j, 'O') for j in range(len(chunk))]
        tagged = [(w['text'], l.split('-')[-1]) for w, l in zip(words, labels)]
        # each field: find the label's words and the value's word among the OCR words, read their tags
        fields = answers['fields'].get(item['doc'], {})
        found = []
        keys = [norm(t) for t, _ in tagged]
        for name, (label, value) in fields.items():
            lab_toks = [norm(t) for t in label.split() if norm(t)]
            val = norm(value)
            lab_tags = []  # the label as a run of consecutive words
            for i in range(len(keys) - len(lab_toks) + 1):
                if keys[i:i + len(lab_toks)] == lab_toks:
                    lab_tags = [tagged[j][1] for j in range(i, i + len(lab_toks))]
                    break
            val_tags = [tag for k, (_, tag) in zip(keys, tagged) if val and (k == val or (len(val) > 6 and val in k))]
            found.append(dict(field=name, label_read=bool(lab_tags), value_read=bool(val_tags),
                              label_is_key=bool(lab_tags) and Counter(lab_tags).most_common(1)[0][0] == 'QUESTION',
                              value_is_answer=bool(val_tags) and 'ANSWER' in val_tags))
        results.append(dict(item=item['id'], condition=item['condition'], fields=found, tags=Counter(l for _, l in tagged)))
        print(item['id'], dict(Counter(l for _, l in tagged)))
    json.dump(results, open(os.path.join(ROOT, 'work', 'bench', 'layoutlmv3.json'), 'w'), indent=1)
    by = defaultdict(list)
    for r in results:
        by[r['condition']] += r['fields']
    print('| capture | fields | label read by OCR | value read by OCR | label tagged as key | value tagged as answer | both |')
    print('|---|---|---|---|---|---|---|')
    for cond, fs in by.items():
        n = len(fs)
        pct = lambda k: f"{100 * sum(f[k] for f in fs) / n:.0f}"
        both = f"{100 * sum(f['label_is_key'] and f['value_is_answer'] for f in fs) / n:.0f}"
        print(f"| {cond} | {n} | {pct('label_read')} | {pct('value_read')} | {pct('label_is_key')} | {pct('value_is_answer')} | {both} |")


if __name__ == '__main__':
    main()
