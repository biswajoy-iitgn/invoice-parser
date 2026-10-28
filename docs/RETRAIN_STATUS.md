# Retraining status — handoff (2026-10-03 ~15:00 IST)

## State at 23:30 IST, 2026-10-03

**Models (all trained, nothing running on a GPU; JarvisLabs instance 524820 is paused):**
reader LoRA `checkpoints/reader-qwen3vl-8b/reader-lora.gguf` (val loss 0.0084), semantic encoder
(94.9% unseen wording), LAYA v2 (96.5% val), SwinIR trained but not enabled (OCR comparison not run).

**Real invoices (7 digital + 14 simulated scans), reports in `work/real_eval/`:**

| | old pipeline | new, as trained (`v2_final*`) | new + fixes (`v2_fix*`) |
|---|---|---|---|
| digital: fully correct | 3/7 | 4/7 | 7/7 |
| scanned: fully correct | 2/14 | 6/14 | 14/14 |
| scanned: charge fields | 87.3% | 93.8% | 100% |

The fixes (container check-digit scan, amounts recovered from merged OCR tokens, weight selection across
attachments, legal vendor name, editable category rules in `configs/category_rules.json`, review flags)
were developed while looking at these same invoices, so the last column is NOT an independent measure.
A fresh set of real invoices is needed for that.

**Held-out synthetic test invoices (unseen layout families, vendors and wording), `reports/synth_test_*.json`:**
digital (40): fields 97.9%, critical 98.1%, charges 98.0%, fully correct 75%, category 95.9%, citation box correct
96.1%, auto-accepted 55% of which 91% fully correct. Scanned (30, measured before the two rule corrections):
fields 97.1%, critical 98.3%, charges 96.8%, fully correct 60%, citation 91.3%, auto-accepted 47% of which 79%
fully correct. This is the realistic expectation for vendors the system has not seen; auto-accept still lets
some wrong invoices through.

**App:** `.venv/bin/python scripts/run_app.py` (or `bash scripts/run_app.sh`) -> http://127.0.0.1:8000,
connected to Neon through `.env.local`. `/analytics` (periodic analytics), `/audit` (audit trail).
**Export:** `./export_invoices.sh` / `export_invoices.bat`. **Docs:** docs/ANALYTICS.md, docs/AUDIT.md,
docs/EXCEL_ODBC_SETUP.md, docs/NEON_SETUP.md.

**Open:** rotate the Neon owner password and move the app to a limited role; Actual ODBC driver licence
(3-row cap inside Excel); SwinIR decision (`scripts/eval_sr_ocr.py`); more real invoices for an
independent test; GPU budget left about INR 164 (A100 or smaller, JarvisLabs templates only).

## Update 19:00 IST — second A100 run in progress (instance ID is now **524820**)

* 15:15 first run FAILED at reader training (transformers 5 removed `group_by_length`); fixed in
  `training/train_reader_qwen3vl.py` and verified end to end with a tiny random Qwen3-VL on CPU.
* 18:40 the Mac kernel-panicked (watchdog timeout) with three trainings on the Apple GPU at once (encoder, LAYA and a
  reader smoke test). **Never run more than one MPS/Metal job next to LAYA.** The encoder's epoch-2 model
  (94.9% unseen-wording accuracy) was already saved and is the final encoder; LAYA resumed from its step-1600 checkpoint.
* 18:47 instance resumed (new ID 524820; pip packages are lost on pause, `/home/autopilot2_remote.sh` reinstalls them),
  reader LoRA training started 18:51: 313 steps at ~20 s plus an eval every 40 steps, then LoRA -> GGUF.
* Budget guard: `work/jl_watch2.sh` (under `caffeinate`), log `work/jl_watch.log`; pauses on ALL_DONE / FAILED or at 21:04.
  Manual stop: `jl pause 524820 --yes --json`.
* Live view: `.venv/bin/python scripts/training_dashboard.py` -> http://127.0.0.1:8767
* LAYA: `work/train_laya_v2.log` (`step=` line every 20 steps), then SwinIR, then `bash scripts/run_final_eval.sh`.

## Running unattended (as of 15:00)

| Where | What | Log / check | Ends with |
|---|---|---|---|
| JarvisLabs A100-80GB `524694` (₹141/h) | `/home/autopilot_remote.sh`: setup → unpack → PP-OCRv6 on 4,013 scan pages → reader data → **Qwen3-VL-8B LoRA** → LoRA→GGUF | `jl exec 524694 -- cat /home/autopilot.status` (RUNNING/ALL_DONE/FAILED); logs in `/home/logs/` | `checkpoints/reader-qwen3vl-8b/{adapter,reader-lora.gguf}` |
| This Mac | `work/jl_watch.sh` — downloads adapter + logs, then **pauses 524694** on ALL_DONE / FAILED / 3 h cap | `work/jl_watch.log` | "PAUSED" |
| This Mac | semantic encoder (bge-small) training | `work/train_encoder.log` | `checkpoints/semantic-encoder/` |
| This Mac | LAYA v2 training (34 classes) | `work/train_laya_v2.log` | `checkpoints/laya-invoice-v2/laya-invoice-ft/` |

If the Mac sleeps or the watcher dies: `jl pause 524694 --yes --json` (stops GPU billing; data kept).

## After those finish

```sh
# SwinIR (optional SR, small; Mac)
.venv/bin/python training/train_swinir.py --hr-list work/sr_v2/train_hr.txt --val-list work/sr_v2/val_hr.txt \
    --swinir-ref work/SwinIR --output checkpoints/swinir-doc-x2      # build the hr lists like remote_stage.sh swinir
# full evaluation battery (semantic tuning on val, real digital+scanned, base-model ablation, synthetic test, SR)
bash scripts/run_final_eval.sh
# run the app (llama.cpp base GGUF + reader LoRA, PP-OCRv6 workers, batch UI at http://127.0.0.1:8000)
bash scripts/run_app.sh
```

If the reader run FAILED: read `work/jl_logs/reader.log` (or `jl exec 524694 -- tail -50 /home/logs/reader.log`
after `jl resume`), fix, and re-run only the failed stage: `bash scripts/jl_stage.sh reader` then `export`.

## Baselines to beat (old pipeline, fixed evidence guard) — `work/real_eval/*/report.json`

| Real invoices | critical | charges | exact invoices | line-item categories |
|---|---|---|---|---|
| digital (7) | 94.6% | 96.1% | 3/7 | 91.5% |
| scanned (14) | 85.7% | 87.3% | 2/14 | 56.4% (amount recall 73.4%, 20 spurious) |

Semantic, real charge lines: old LAYA 68.1%, old rule matcher 93.6%, new engine with smoke-trained models 93.6%.


## 2026-10-04: why "fully correct" was low

See `docs/ACCURACY_STUDY.md`: the post-reader logic was reworked (category decision, header safeguards) and
measured on untouched held-out samples: 90.0% of digital invoices fully correct (99.5% of fields), 75% of scans.
