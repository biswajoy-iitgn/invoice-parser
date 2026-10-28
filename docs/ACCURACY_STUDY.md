# Why "fully correct" was low, and what changed (2026-10-04)

"Fully correct" means every one of an invoice's values is right at once. A held-out invoice has 11.7 non-empty
values on average, so 98% per value gives about 0.98^11.7 = 79% of invoices fully correct; reaching 90% needs
about 99.1% per value. The work therefore was to find where the last 2% of values went wrong.

## Method

- The reader's output and the page layout of held-out invoices were cached once (`scripts/fusion_study.py collect`),
  so every change to the logic after the reader could be re-scored in seconds (`... evaluate`).
- Changes were chosen on the **validation** split (150 digital, 60 scanned) and on a first test sample whose errors
  were read (120 digital). The numbers to trust are from samples whose errors were **not** looked at before
  scoring: a second test sample (120 digital) and the scanned test sample (60).
- Real invoices (7 digital, 14 scans of them) were re-scored after every change.

## What was wrong

1. **The small semantic models overruled a correct reader.** Categories were decided by a weighted sum of four
   scores (embedding model, LAYA, word list, reader) whose weights were set before the fine-tuned reader existed.
   On validation the sum overruled the reader on 27 lines with no known alias or rule behind it: it was right 3
   times, the reader 12 times.
2. **Who to believe depends on the embedding model's score, not on a vote.** When the embedding model disagrees
   with the reader with a score of 0.9 or more it was right 20 times in 22; below 0.7, with LAYA on the reader's
   side, the reader was right 27 times in 28. LAYA repeats the reader's mistakes on unseen wording (both learned
   from the same invoices), so its agreement proves little.
3. **A gap-filling fallback filled header fields with garbage.** When the reader left a B/L or AWB number empty,
   a label match filled it. Measured: wrong 6 times in 7 (a charge row "B/L Fee ... 996759" read as "label: value").
4. **The reader reworded or cut values** on vendors it had not seen: "Pvt Ltd" written as "Private Limited",
   "... SERVICES PVT" for "... SERVICES PVT LTD", "E/88835/26-" for "E/88835/26-27", "160-9052" for
   "160-9052 2913", a stamp word kept inside a number ("DEL/AX/2020 ORIGINAL").
5. **One of my own rules was wrong:** "On-Carriage" was sent to DAP Charges; the reference says Transportation.
6. **Exchange rates were rounded** to six significant digits (104.6538 stored as 104.654) — fixed earlier the same day.
7. The post-reader step took about 16 s per invoice on CPU (LAYA), more than the reader; on the Apple GPU under 3 s
   (`LAYA_DEVICE=mps`, now the default when the app starts on this Mac).

## What changed

- **Category decision** (`semantic.py`, `_reader_first`): a known alias decides; then a business rule; otherwise
  the reader's category, accepted when the embedding model agrees. If the embedding model disagrees with a score
  of 0.9 or more (0.75 when LAYA agrees with it) its category is taken; in every disagreement that is not clearly
  the reader's the invoice goes to review, with the reason spelled out.
- **Header safeguards** (`pipeline.py`): a near match on a digital page is replaced by the printed text; a number
  cut inside a printed token is completed to the token; a MAWB is normalized to its 11 digits or sent to review;
  copy markers are removed from reference numbers; a vendor name cut before its legal suffix is completed.
- **Gap filling** needs a label that is not on a charge row, both semantic models naming the same field, and a
  value shaped like a reference number; otherwise the field stays empty and the invoice goes to review with the
  candidate named.
- **New review reasons:** a second reference number of the same kind on the same page; a rule that would apply but
  for a conflicting word on the line; a scanned header value the OCR engine scored below 0.95.
- The "On-Carriage" rule was corrected.

## Results

Fully correct / all fields / charge category / automatically accepted (and how many of those had an error):

| Set | Before | After |
|---|---|---|
| Validation, digital (150) — used for tuning | 78.7% / 98.75% / 97.1% / 71% (3 wrong) | 90.7% / 99.44% / 98.5% / 77% (1 wrong) |
| Test, digital, first sample (120) — errors were read | 80.0% / 98.84% / 97.2% / 78% (17 wrong) ¹ | 88.3% / 99.41% / 98.8% / 64% (2 wrong) |
| **Test, digital, second sample (120) — untouched** | not measured | **90.0% / 99.46% / 99.2% / 65% (4 wrong)** ² |
| **Test, scans (60) — untouched** | 68.3% / 97.7% / 94.8% / 63% (8 wrong) ¹ | **75.0% / 98.4% / 96.5% / 53% (2 wrong)** ³ |
| Validation, scans (60) | — | 70.0% / 97.8% / 93.7% / 67% (3 wrong) |
| Real invoices, digital (7) | 7 of 7 ⁴ | 6 of 7, 47/47 charge lines, 0 wrong auto-accepted |
| Real invoices, scans (14) | 14 of 14 ⁴ | 12 of 14, 94/94 charge lines, 0 wrong auto-accepted |

¹ First measurement on that sample, taken part-way through the changes (the first category policy, before the
embedding-score rule and the header safeguards). The code as it was at the start of the day was not run on it.
² Scored with the MAWB/HAWB label rule (below). Two of the twelve wrong invoices, and two of the four wrong
auto-accepts, are wrong only by that rule: the synthetic reference keeps HAWB empty on sea shipments.
³ Scored before the MAWB/HAWB label rule; with it the same sample scores 71.7% (4 wrong auto-accepts), again
because of the reference's convention.
⁴ Reached after rules were tuned on these same seven invoices; not an independent result.

All rows except the untouched digital sample were scored before the MAWB/HAWB label rule. With that rule the
synthetic sets score 1 to 3 points lower (validation 88.7%, first test sample 86.7%, test scans 71.7%), only because
their reference keeps MAWB / HAWB empty on sea shipments; the real invoices are unchanged (6 of 7, 12 of 14).

**The real invoice that is no longer fully correct** is EPT Global: the line "EXPORT ORIGIN CHARGES SECURITY MANIFEST
CHARGES" is read as ENS Charges by the reader and as AMS by both semantic models; the reference books it under AMS.
The embedding score (0.58) is below the level at which the models are right more often than the reader, so the
reader's category stands and the invoice goes to review with that reason. The old weighted sum got this line
right and lost more elsewhere. Whether a security filing is ENS or AMS depends on the destination, which this
invoice does not print. One correction in the review screen teaches the wording for good.

## MAWB / HAWB from the document's own label (user decision, 2026-10-04)

A number printed under a label that names MAWB or HAWB now fills that key on sea shipments too (the Dachser
invoice prints "M B/L No. / MAWB No." over `HLCUBO12608AWQG7`). The reader was trained to leave those keys empty on
sea shipments, and the synthetic reference follows that convention, so this rule lowers the synthetic scores
slightly (see ² and ³) while matching what the user expects.

## What still goes wrong

- **New wording that nothing has seen**, where the booking is a business convention ("Air Cargo Complex Charges" is
  Warehouse charges Port in the reference; reader and models all guess otherwise). On validation ten of the fifteen
  remaining wrong invoices are that one phrase. These go to review; one correction teaches the alias.
- **House versus master B/L** when a document prints both under similar labels (about 1 invoice in 100).
- **Scans:** misread characters in reference numbers (0/O, 5/S, 1/I), which no check can detect, and amounts the OCR
  misread (those invoices fail the subtotal check and go to review). Scans stay about 15-20 points below digital
  files on "fully correct".
- **Automatic acceptance is not yet safe as approval:** on the untouched samples 2 to 4 of every 100 accepted
  digital invoices, and about 6 of every 100 accepted scans, still contain an error.

## Reproducing

```sh
.venv/bin/python scripts/fusion_study.py collect dataset_v2 --split test --source digital --n 120 --offset 160
LAYA_DEVICE=mps .venv/bin/python scripts/fusion_study.py evaluate dataset_v2 --split test --source digital --offset 160 --n 120 --errors
.venv/bin/python scripts/fusion_study.py evaluate dataset_v2 --split real --source digital
LAYA_DEVICE=cpu .venv/bin/python scripts/audit_blank_fields.py      # blank keys versus what the documents print
```
