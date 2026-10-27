# Parser bake-off: layout, OCR and VLM parsers on invoices, contracts, quality certificates, gate passes, rosters and complex tables

27–28 Oct 2026. Apple M5 Max, 36 GB, everything run locally, one model at a time. Only aggregate numbers appear here: the
invoices are real and unredacted. Scripts: `scripts/bench/`. Raw outputs and scores stay in the gitignored `work/bench/`
and `work/bench2/`.

**Read this first.** n is small (7 invoices, 2 contracts, 3 quality certificates, 1 roster, 6 synthetic gate passes), so
differences of a few points are noise. The 7 invoices are the held-out set in `eval/real_invoices/` (the user allowed it;
`gold.json` was never opened), so that set has been used for selection and **should be refreshed**.

## 1. What was tested

| Pipeline | What it is | Licence | Commercial use |
|---|---|---|---|
| Docling on the PDF text layer | text layer + Heron layout + TableFormer | MIT / Apache-2.0 | yes |
| PP-OCRv6 | text detection + recognition only, no tables | Apache-2.0 | yes |
| PP-StructureV3 | PaddleOCR layout + OCR + table-recognition pipeline (PP-OCRv6 text models) | Apache-2.0 | yes |
| PaddleOCR-VL-1.6 | PP-DocLayoutV3 layout, VLM reads each region | Apache-2.0 | yes |
| LightOnOCR-2-1B | 1B image-to-text VLM | Apache-2.0 | yes |
| Qwen3-VL-8B-Instruct (8-bit MLX) | general VLM, "Markdown, tables as HTML" prompt | Apache-2.0 | yes |
| Granite-Docling-258M | DocTags VLM | Apache-2.0 | yes |
| Docling + Tesseract / EasyOCR | Docling on page images with that OCR | MIT / Apache-2.0 | yes |
| Tesseract 5, EasyOCR | plain OCR | Apache-2.0 | yes |
| Chandra OCR 2 | VLM | modified OpenRAIL-M, free only under $2M revenue/funding | **no** (research only) |
| HunyuanOCR | VLM | Tencent community licence, void in EU/UK/South Korea | **no** |
| LayoutLMv3 | FUNSD key/value tagger on PP-OCRv6 words | CC-BY-NC-SA | **no** |

Dropped: GLM-OCR (MIT). Through its SDK it lost a whole document-details table on one invoice page, reproducibly and with
nothing logged, while the model itself read that crop fine. Dropped by decision after 4 of 42 pages.
Not run: Table Transformer (downloaded, no wiring), DeepSeek-OCR, MinerU (AGPL), Surya (non-commercial weights),
Chandra on the second set.

## 2. Test sets

**Bake-off 1 (`work/bench/`):** 42 page images. 7 real freight invoices (16 digital pages at 200 dpi, 7 office scans,
7 phone captures) and 2 public CUAD contracts (CC BY 4.0), 6 digital and 6 generated phone-photo pages. Answer key: the
PDF text layer (words with boxes), plus 53 hand-checked invoice fields and hand-transcribed line-item tables.

**Bake-off 2 (`work/bench2/`):** 75 images.
- *quality*: two public Yücel Boru EN 10204 3.1 mill certificates (24 and 35 columns) and the Haas first-article
  inspection sample. Answer key from the PDF text layer.
- *roster*: the Smartsheet weekly staff roster example, 16 employees x 7 days x start/end/hours. Key from the text layer.
- *gatepass*: **6 synthetic** gate passes in two layouts (returnable and non-returnable), generated with known values.
  No filled real gate pass could be found online. No handwriting, stamps or signatures.
- *tables*: 40 FinTabNet test tables (CDLA-Permissive) with merged cells, 8-28 rows, 4-12 columns, 15 also as phone
  photos. FinTabNet crops are about 70 dpi, so they were upscaled 3x to match a 200 dpi page. Answer key: the dataset's
  own structure and cell text.
- Every document also exists as a generated phone photo (paper tint, tilt, perspective, uneven light).

PaddleOCR-VL-1.6 and Docling's TableFormer may have seen FinTabNet during training, so FinTabNet favours them. The mill
certificates and the roster are unseen documents.

Metric definitions are in `scripts/bench/score.py` and `score_tables.py` (TEDS = tree-edit similarity of the table; cell F1
= multiset F1 of cell texts; "numbers found" = share of numbers on the page that appear in the output).

## 3. Results, bake-off 1 (invoices and contracts)

Scans = office / phone. All figures are percentages.

| Pipeline | Words F1 scans | Fields found (53) scans | Table cells exact scans | Invented numbers scans | Label then value scans |
|---|---|---|---|---|---|
| Chandra OCR 2 (no licence) | 96 / 95 | 100 / 96 | 100 / 98 | 1 / 1 | 98 / 96 |
| PP-OCRv6 | 92 / 92 | 98 / 98 | no tables | 3 / 3 | 96 / 92 |
| PaddleOCR-VL-1.6 | 92 / 87 | 90 / 76 | 92 / 93 | 2 / 2 | 85 / 72 |
| HunyuanOCR (no licence) | 85 / 91 | 92 / 100 | 48 / 67 | 1 / 1 | 90 / 96 |
| Tesseract 5 | 88 / 78 | 92 / 69 | no tables | 2 / 4 | 78 / 63 |
| Docling + Tesseract | 82 / 77 | 81 / 77 | 63 / 28 | 4 / 4 | 64 / 55 |
| PP-StructureV3 | 76 / 70 | 79 / 70 | 47 / 46 | 2 / 2 | 73 / 54 |
| EasyOCR | 85 / 77 | 64 / 70 | no tables | 5 / 8 | 50 / 41 |
| Docling + EasyOCR | 83 / 77 | 64 / 56 | 74 / 43 | 4 / 7 | 40 / 23 |

- **Digital PDFs: use the text layer.** Docling on it: words F1 97, 100% of fields, 0 invented numbers. Never OCR a
  digital PDF.
- **Hallucination:** low for the Paddle family and the VLMs (invented numbers 1-3%). PaddleOCR-VL looped on 6% of digital
  invoice pages ("Authorized Signature" repeated) and "corrected" a printed typo (PARTICUALRS to PARTICULARS).
  PP-StructureV3 looped on 14% of phone pages. Chandra invented 8% of words on digital invoices, the most of any pipeline.
- **Contracts:** most pipelines reach 96-100 words F1. HunyuanOCR finds only 53% of the numbers (it drops clause numbers);
  Docling + EasyOCR falls to 76 on phone photos.
- **LayoutLMv3** as a key/value tagger on PP-OCRv6 words: value tagged correctly 79-83%, label and value 70%. No layout
  regions or tables, and a non-commercial licence.

## 4. Results, bake-off 2

### Complex tables (40 FinTabNet crops, 15 as phone photos)

| Pipeline | TEDS clean / phone | Cell F1 clean / phone | Rows exactly right (clean) |
|---|---|---|---|
| PaddleOCR-VL-1.6 | **98 / 93** | 99 / 87 | 94 |
| LightOnOCR-2-1B | 86 / 75 | 93 / 76 | 81 |
| Qwen3-VL-8B | 74 / 72 | 84 / 71 | 68 |
| Docling + Tesseract | 74 / 28 | 43 / 11 | 17 |
| PP-StructureV3 | 71 / 54 | 76 / 39 | 44 |
| Granite-Docling-258M | 45 / 68 | 45 / 63 | 39 |
| Docling + EasyOCR | 37 / 24 | 16 / 9 | 3 |

PaddleOCR-VL-1.6's 98 is an upper bound (possible training exposure). Docling's weakness here is its OCR: EasyOCR dropped
the label column and garbled numbers.

### Quality certificates, gate passes, roster (digital / phone)

| Use case | Pipeline | Fields found | Table cells exact |
|---|---|---|---|
| Quality (3 docs) | PP-OCRv6 | 100 / 100 | no tables |
| | LightOnOCR, Qwen3-VL | 100 / 100 | 51 / 48 and 73 / 49 (see decimal commas) |
| | PaddleOCR-VL-1.6 | 100 / 97 | 51 / 49 (see decimal commas) |
| | PP-StructureV3 | 100 / 93 | 53 / 39 |
| Gate passes (6, synthetic) | PP-OCRv6, PaddleOCR-VL, LightOnOCR, Qwen3-VL | 100 / 100 | 100 / 100 (PP-OCRv6 no tables) |
| | PP-StructureV3 | 92 / 80 | 100 / 95 |
| | Docling + Tesseract | 96 / 67 | 22 / 12 |
| Roster (1 grid) | PaddleOCR-VL-1.6 | 96 / 98 | 89 / 86 |
| | LightOnOCR | 100 / 94 | 65 / 0 |
| | PP-OCRv6 | 100 / 100 | no tables |
| | PP-StructureV3 | 79 / 83 | 24 / 16 |
| | Qwen3-VL-8B | 15 / 13 | looped, 12 / 0 |

**Decimal commas.** On the mill certificates the three VLMs read the 24- and 35-column tables essentially perfectly, but
rewrite the printed `0,047` as `0.047`. With comma and point treated alike their cell accuracy is 100% (digital) and 94-100%
(phone); the exact-match scores above are low only because of that rewrite. It is a silent change to the printed value, so
the pipeline must keep the printed text (see section 6).

**Granite-Docling** failed on many digital pages (11% of quality fields). The DocTags conversion in the runner may be at
fault, so this is not a verdict on the model.

## 5. Findings

1. **Table structure is the hard part, not text.** PP-StructureV3's table recogniser and Docling's TableFormer plateau at
   50-75% of cells; a VLM reading each cell from layout regions (PaddleOCR-VL-1.6) reaches 92-100%.
2. **PaddleOCR-VL-1.6 is the only commercially usable pipeline that stays strong across invoices, certificates, a roster
   grid and phone photos.** LightOnOCR-2-1B is a credible second reader on clean pages (but failed the phone-photo roster).
3. **Plain PP-OCRv6 is the most reliable text and field finder** (98-100% of fields everywhere) and invents almost nothing,
   but gives no table structure.
4. **VLMs normalise.** Decimal commas, printed typos and "obvious" corrections get rewritten. Qwen3-VL-8B looped on the
   dense roster. Accuracy claims need a check against the page's own characters.
5. **Chandra is the accuracy ceiling** (100% of invoice table cells), but its licence excludes Harsha.
6. **Never trust one reader for numbers.**

## 6. Recommendation per use case

All use the same stack: text layer for digital PDFs, otherwise PP-OCRv6 for text and fields, PaddleOCR-VL-1.6 for tables,
every value cited to its measured box, and REVIEW routing for anything unverified (the v2 pipeline already does this).

1. **Logistics invoices (fields with citations):** keep the v2 pipeline. Digital: text layer. Scans: PP-OCRv6 text plus
   PaddleOCR-VL-1.6 line items (92-93% of cells exact on scans). Evidence is strongest here (real invoices).
2. **Quality reports:** the same stack; PP-OCRv6 finds 100% of the labelled fields. For measurement tables, compare each
   table cell against the PP-OCRv6 text and **keep the printed digits** (do not accept `0.047` for `0,047`). Evidence: 3
   certificates only; no real quality report with free text and signatures.
3. **Gate pass slips:** PP-OCRv6 plus any reader reaches 100% on synthetic passes. Untested: handwriting, stamps, vehicle
   numbers written by hand, ID photos. Collect real passes before sign-off.
4. **Rosters:** PaddleOCR-VL-1.6 (89% / 86% of cells exact). Check totals arithmetically (hours x rate = pay) and route
   mismatches to review. One roster layout tested; need more, including scanned ones.
5. **Specialised complex tables:** PaddleOCR-VL-1.6 as primary, LightOnOCR-2-1B as an independent second reader, and send
   every disagreement to review. PaddleOCR-VL's 98 TEDS is an upper bound from a public benchmark of simple financial tables.
   The need for "very good" table models is **not yet proven met**: test on the real documents.

## 7. Next steps

- Refresh `eval/real_invoices/`: it was used for selection here.
- Collect 10-20 real documents each for use cases 2-5 (real quality reports, hand-filled gate passes, scanned rosters, the
  hardest specialised tables) and rerun `scripts/bench/build_set2.py`-style scoring with hand-checked keys.
- Optional: test Table Transformer and DeepSeek-OCR; an LLM field-extraction step over each pipeline's text, to turn "value
  present" into "field extracted correctly".
- Fix or re-check the Granite-Docling runner.
