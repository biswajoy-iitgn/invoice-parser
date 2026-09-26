"""Charts for EVALUATION.md from evaluation/results/<run>/metrics.json."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
RES, OUT = HERE / "results", HERE / "charts"
RUNS = [("test_ensemble_fixed", "Ensemble (Tesseract x2 + PP-OCRv4, verified)"),
        ("test_tesseract", "Tesseract 5 LSTM"),
        ("test_rapidocr", "PP-OCRv4 (RapidOCR)")]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]          # validated categorical slots 1-3 (all-pairs safe)
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                     "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})


def load():
    return {r: json.loads((RES / r / "metrics.json").read_text()) for r, _ in RUNS if (RES / r / "metrics.json").exists()}


def grouped(ax, groups, series_vals, labels, fmt, ymax):
    n = len(series_vals)
    width = 0.8 / n
    for i, (vals, lab) in enumerate(zip(series_vals, labels)):
        xs = [g + (i - (n - 1) / 2) * width for g in range(len(groups))]
        bars = ax.bar(xs, vals, width=width - 0.02, color=SERIES[i], label=lab, edgecolor=SURFACE, linewidth=2)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + ymax * 0.01, fmt(v), ha="center", va="bottom",
                    fontsize=8, color=INK2)
    ax.set_xticks(range(len(groups)), groups)
    ax.set_ylim(0, ymax)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def main():
    m = load()
    runs = [r for r, _ in RUNS if r in m]
    names = [n for r, n in RUNS if r in m]
    OUT.mkdir(exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 4.2))
    keys = [("Micro F1", lambda x: x["micro"]["f1"]), ("Precision", lambda x: x["micro"]["precision"]),
            ("Recall", lambda x: x["micro"]["recall"]), ("mAP", lambda x: x["mAP"]),
            ("Line recall", lambda x: x["lines"]["line_recall"])]
    grouped(ax, [k for k, _ in keys], [[f(m[r]) for _, f in keys] for r in runs], names, lambda v: f"{v:.2f}", 1.08)
    ax.set_title("Field extraction quality on the held-out test split (90 invoices)", loc="left", fontsize=11)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(OUT / "test_quality_by_ocr.png", dpi=150)

    fig, ax = plt.subplots(figsize=(9, 4.0))
    keys = [("Fabricated field", lambda x: x["hallucination"]["fabrication_rate"]),
            ("Ungrounded value", lambda x: x["hallucination"]["ungrounded_rate"]),
            ("Wrong value", lambda x: x["wrong_value_rate"])]
    vals = [[f(m[r]) * 100 for _, f in keys] for r in runs]
    grouped(ax, [k for k, _ in keys], vals, names, lambda v: f"{v:.1f}%", max(max(v) for v in vals) * 1.25)
    ax.set_ylabel("Percent")
    ax.set_title("Hallucination and substitution rates (lower is better)", loc="left", fontsize=11)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(OUT / "test_hallucination_by_ocr.png", dpi=150)

    fig, ax = plt.subplots(figsize=(9, 4.0))
    tiers = ["clean", "medium", "scan"]
    grouped(ax, [f"{t} (n={m[runs[0]]['by_tier'][t]['n']})" for t in tiers],
            [[m[r]["by_tier"][t]["micro_f1"] for t in tiers] for r in runs], names, lambda v: f"{v:.2f}", 1.08)
    ax.set_ylabel("Micro F1")
    ax.set_title("Micro F1 by image quality tier (test split)", loc="left", fontsize=11)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(OUT / "test_f1_by_tier.png", dpi=150)

    best = m[runs[0]]["fields"]
    items = sorted(((f, v["f1"]) for f, v in best.items() if v["support"]), key=lambda x: x[1])
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.barh([f for f, _ in items], [v for _, v in items], color=SERIES[0], height=0.7, edgecolor=SURFACE, linewidth=2)
    for i, (_, v) in enumerate(items):
        ax.text(v + 0.01, i, f"{v:.2f}", va="center", fontsize=8, color=INK2)
    ax.set_xlim(0, 1.08)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(f"Per-field F1, {names[0]} (test split)", loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "test_f1_per_field.png", dpi=150)
    print("charts ->", OUT)


if __name__ == "__main__":
    main()
