"""Every hand-transcribed answer must exist in the PDF's own text layer: catches typos in work/bench/set/answers.json."""
import json, os, sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SET = os.path.join(ROOT, 'work', 'bench', 'set')
ans = json.load(open(os.path.join(SET, 'answers.json')))
bad = 0
for page, rows in ans['tables'].items():
    seq = [w['text'] for w in json.load(open(os.path.join(SET, 'truth', f'{page}.json')))['words']]
    words = Counter(seq)
    need = Counter(t for r in rows for c in r for t in c.split())
    # a number wrapped onto two lines (18541.0 / 0) is one value in its cell
    for tok, n in need.items():
        if words[tok] < 1 and not any(words[tok[:i]] and words[tok[i:]] for i in range(1, len(tok))):
            bad += 1
            print(f'{page}: table token not on page: {tok!r}')
for doc, fields in ans['fields'].items():
    text = ' '.join(w['text'] for p in sorted(os.listdir(os.path.join(SET, 'truth'))) if p.startswith(doc + '-p')
                    for w in json.load(open(os.path.join(SET, 'truth', p)))['words'])
    for k, (label, value) in fields.items():
        for part, what in ((value, 'value'), (label, 'label')):
            if part not in text:
                bad += 1
                print(f'{doc}.{k}: {what} not in text: {part!r}')
print('problems:', bad)
sys.exit(1 if bad else 0)
