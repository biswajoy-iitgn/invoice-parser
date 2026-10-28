# Reader bake-off: which model fills the fields

29 Oct 2026. Apple M5 Max, 36 GB, one model at a time, every model served by the app's own model pool (`mlx_vlm`).
Script: the profile pipeline end to end (`profiles.extract`), so the numbers include citation, printed-text restoration and
checks. Only aggregate numbers appear here: the invoices are real and unredacted.

**Read this first.** n is small: 7 invoices (53 hand-checked fields, 45 hand-transcribed charge lines from the parser
bake-off's key), 2 mill certificates (answer key: 7 and 9 heat rows), 1 roster (16 employees). The 7 invoices were also used
for the parser bake-off, so they have now been used for selection twice and should be refreshed.

## Why this was run

Research (Oct 2026) confirmed the design (layout-preserving text in, JSON-schema output, every value cited) and the table
reader (PaddleOCR-VL-1.6, best published OmniDocBench v1.6 score among open models), but not the reader: two newer
Apache-2.0 4B models claimed better extraction.

| Reader | Licence | Size loaded | Why tested |
|---|---|---|---|
| Qwen3-VL-4B Instruct, 4-bit | Apache-2.0 | ~3.5 GB | current default |
| Qwen3.5-4B, 4-bit | Apache-2.0 | ~4 GB | newer generation (Mar 2026); 86.2 OmniDocBench 1.5 on its card |
| NuExtract3, nvfp4 | Apache-2.0 | ~4 GB | Qwen3.5-4B fine-tuned for template-to-JSON extraction; 0.651 vs 0.417 for Qwen3.5-4B on its maker's benchmark |
| Qwen3-VL-8B Instruct, 8-bit | Apache-2.0 | ~10 GB | was loaded for the roster |

Each ran on the page text alone and with the first pages' images added (VAREX found text + image best for an 8B).
Thinking was off for all (their cards' advice for extraction). NuExtract3 got its own prompt format (template + instructions)
through a patched chat template (`scripts/prepare_nuextract.py`); the rendered prompt matches its model card.

## Results

| Reader | Invoice fields (53) | Charge lines (45) | s / invoice | Mill cert heat rows (7, 9) | Roster rows (16), cells on page | Roster s |
|---|---|---|---|---|---|---|
| **Qwen3-VL-4B** | **50** | **45** | 20.5 | **7, 9** | 0 | 2 |
| Qwen3-VL-4B + images | 42 | 41 | 51.8 | 7, 9 | 16, 87% | 93 |
| Qwen3.5-4B | 47 | 32 | 24.0 | 3, 3 | 16, 100% | 15 |
| Qwen3.5-4B + images | 50 | 27 | 18.6 | 3, 5 | 87 (duplicates) | 67 |
| **NuExtract3** | 48 | 41 | **5.0** | 3, 4 | **16, 100%** | **10** |
| NuExtract3 + images | 46 | 42 | 5.4 | 3, 9 (no mechanical rows) | 16, 87% | 13 |
| Qwen3-VL-8B | 49 | 42 | 22.4 | 0, 0 | 16, 99% | 42 |

## Findings

1. **Qwen3-VL-4B stays the default reader.** Most accurate on invoices and the only reader that read the wide (24- and
   35-column) certificate tables right. The newer Qwen3.5-4B is not better here despite its higher general benchmarks.
2. **NuExtract3 reads the dense roster best and fastest**, and is 4x faster on invoices at a cost of 2 fields and 4 of 45
   charge lines. It now reads the roster profile; nothing uses the 8B any more, so the largest model the app loads is ~4 GB.
3. **Page images do not help the best reader.** They cost 2.5x the time and lost fields and lines on invoices; they only
   rescued the roster for a reader that otherwise failed it. Profiles can still opt in (`images: true`); none does.
4. **Vendor benchmarks did not carry over.** NuExtract3's maker reports a large lead over Qwen3.5-4B; on these documents it
   leads on speed and grids, not on invoice accuracy.

## Scans and phone photos: where PaddleOCR-VL-1.6 might help (29 Oct)

The office-scan and phone-photo page 1 of the same 7 invoices (14 pages), against the same key.

**Charge lines (45 per capture type)**

| Route | Office | Phone | Fields (106) | s / page |
|---|---|---|---|---|
| PP-OCRv6 -> Qwen3-VL-4B (current) | 45 | 42 | 96 | 45 |
| PaddleOCR-VL-1.6's own tables (with PP-DocLayoutV3) | 45 | 44 | no field mapping | bake-off |
| Qwen3-VL-4B given PaddleOCR-VL's tables as well | 45 | 41 | 96 | 38 |

**Re-reading a cropped value** (106 key values located on the scans; the pipeline re-reads only low-confidence ones)

| Reader of the crop | All 106 | The 6 low-confidence |
|---|---|---|
| PP-OCRv6 text as cited | 64 | 5 |
| Qwen3-VL-4B (current re-read) | **93** | **6** |
| PaddleOCR-VL-1.6 "OCR:" | 71 | 4 |

**The one stamp in the set** (MEGALO seal, 6 words, digital / office / phone): PP-OCRv6 0 / 2 / 0, Qwen3-VL-4B 1 / 1 / 0,
PaddleOCR-VL-1.6 "Seal Recognition:" on the crop **6 / 6 / 6**. Switching on the pipeline's own seal recognition instead
(`use_seal_recognition`) is not the same thing: it uses separate seal-OCR models, doubled the page time (163 s vs 87 s), read
the office scan as "PVT LID" and found no stamp on the phone photo, so it stays off.

**Decisions.** No change: the current route keeps the scans' charge lines (+2 of 45 on phone photos for the parser is not worth
a parser pass per scanned page, and handing its tables to the reader made phone photos worse), and the reader stays the
re-reader. Stamps: PaddleOCR-VL's seal prompt is far better (one stamp, matching its published lead), but using it needs the
seal located first; worth building once 10-20 stamped documents with known text are collected.

## Layout models in front of the reader (29 Oct)

The same Qwen3-VL-4B reader, given the page text either flat (current) or grouped under the regions a layout model found
(each line under the smallest region holding it, regions in column order, a region-label header per group). Layout ran on CPU.

| Front end | Layout s / page | Invoice fields (53) | Charge lines (45) | Scan fields (106) | Scan lines office / phone (45) | Mill cert heat + mech rows (7+20, 9+9) | Roster | s / digital invoice | s / scan |
|---|---|---|---|---|---|---|---|---|---|
| **Flat text (current)** | 0 | **50** | **45** | **96** | **45 / 42** | **7+20, 9+9** | 0 | 18.9 | **44.7** |
| PP-DocLayoutV3 | 0.93 | 48 | 43 | 93 | 39 / 37 | 1+0, 9+9 | 0 | 35.5 | 80.9 |
| Docling 2.144 Heron (default) | 0.23 | 49 | 43 | 91 | 45 / 42 | 1+0, 9+9 | 0 | **8.0** | 78.1 |
| Docling 2.144 Egret-large | 0.38 | 49 | 38 | 95 | 34 / 39 | 7+7, 1+0 | 0 | 24.3 | 89.4 |

Flat text wins or ties every accuracy column. Every layout front end broke at least one wide certificate table, because a
table split across regions loses its rows. None rescued the roster, which NuExtract3 reads anyway. Heron's fast digital
invoices (8 s) are not worth a field, 2 charge lines and both certificate tables.

**Stamps.** PP-DocLayoutV3 found the MEGALO seal on all 4 pages that carry it (digital p1 and p2, office scan, phone photo)
and flagged no seal on the other 23 documents. Docling has no seal class. Each found seal was cropped (1% padding) and read with
PaddleOCR-VL-1.6's `Seal Recognition:` prompt: **6/6 words on all 4**, about 0.2 s per seal once the model is loaded. So the
seal location the previous section said was missing works on this set. It is still one stamp design, too few to switch on.

**Decision.** No change to reading: flat text stays. A stamp step (PP-DocLayoutV3 locates a seal, then PaddleOCR-VL reads
the crop) is the one layout use that paid off. Build it as an opt-in profile field once 10-20 stamped documents with
known text are collected.

## Configuration after this run

- `configs/models.yaml`: default reader `qwen3-vl-4b`; `nuextract3` and `qwen3.5-4b` added with these numbers in their notes.
- `configs/profiles/roster.yaml`: `models: {reader: nuextract3}`.
- `scripts/run_app.py` prepares NuExtract3's adapted copy when its download is present.

## Next steps

- Refresh the invoice set; collect 10-20 real rosters, quality reports and gate passes with answer keys and rerun this
  comparison (one roster and two certificates decide two profiles' readers today).
- Re-test when an MLX build of a newer extraction model appears; the comparison script runs any catalog reader.
