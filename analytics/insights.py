"""MIS aggregates, findings and data science over prepared invoices (see analytics.data.prepare).

Every finding carries the invoice numbers it is based on, so a reader can go from a statement in a
report to the rows (and from there, through the audit trail, to the cited boxes on the document).
Methods are deliberately simple and explainable; each one states the minimum data it needs and
reports "not enough data" instead of guessing below that.
"""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from . import ENGINE_VERSION
from .data import CHARGES, fingerprint

MIN_PEERS_VENDOR, MIN_PEERS_CATEGORY, Z_CUT = 8, 12, 3.5
LEGAL = re.compile(r"\b(pvt|private|ltd|limited|llp|llc|inc|gmbh|corp|plc|co|company|india)\b\.?", re.I)
GENERIC = {"global", "international", "india", "indian", "trans", "shipping", "logistics", "freight", "cargo",
           "express", "sea", "air", "ocean", "pacific", "national", "united", "royal", "star", "prime", "the",
           "new", "sri", "shree", "shri", "south", "north", "east", "west", "inter", "world", "worldwide"}


def inr(v: float) -> str:
    """Compact Indian units for reading: 8.71 L (lakh = 1,00,000), 1.24 Cr (crore = 1,00,00,000)."""
    v = float(v or 0)
    a = abs(v)
    if a >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


# ------------------------------------------------------------------ aggregates --
def monthly(inv: pd.DataFrame) -> list[dict]:
    d = inv.dropna(subset=["month"])
    if d.empty:
        return []
    g = d.groupby("month").agg(invoices=("total", "size"), spend=("total", "sum")).reset_index()
    idx = pd.period_range(g["month"].min(), g["month"].max(), freq="M").strftime("%Y-%m")
    g = g.set_index("month").reindex(idx, fill_value=0).rename_axis("month").reset_index()
    return [{"month": r.month, "invoices": int(r.invoices), "spend": round(float(r.spend), 2)} for r in g.itertuples()]


def by_vendor(inv: pd.DataFrame) -> list[dict]:
    tot = float(inv["total"].sum())
    g = inv.groupby("vendor_name").agg(invoices=("total", "size"), spend=("total", "sum")).reset_index()
    g = g.sort_values("spend", ascending=False)
    return [{"vendor": r.vendor_name, "invoices": int(r.invoices), "spend": round(float(r.spend), 2),
             "share": pct(r.spend, tot), "avg_invoice": round(float(r.spend) / max(1, r.invoices), 2)}
            for r in g.itertuples()]


def by_category(inv: pd.DataFrame) -> list[dict]:
    tot = float(inv["total"].sum())
    out = [{"code": c, "category": t, "invoices": int(inv[c].notna().sum()),
            "spend": round(float(inv[c].sum(skipna=True)), 2)} for c, t in CHARGES.items()]
    out = [dict(o, share=pct(o["spend"], tot), avg=round(o["spend"] / o["invoices"], 2)) for o in out if o["invoices"]]
    return sorted(out, key=lambda o: -o["spend"])


def vendor_category(inv: pd.DataFrame, top_vendors: int = 8, top_categories: int = 6) -> dict:
    """Where each of the largest vendors' money goes; the tail folds into 'Other' on both sides."""
    cats = [c["code"] for c in by_category(inv)]
    lead, rest = cats[:top_categories], cats[top_categories:]
    vend = [v["vendor"] for v in by_vendor(inv)]
    rows = []
    for name in vend[:top_vendors] + (["All other vendors"] if len(vend) > top_vendors else []):
        d = inv[~inv["vendor_name"].isin(vend[:top_vendors])] if name == "All other vendors" else inv[inv["vendor_name"] == name]
        vals = [round(float(d[c].sum(skipna=True)), 2) for c in lead]
        if rest:
            vals.append(round(float(d[rest].sum(skipna=True).sum()), 2))
        rows.append({"vendor": name, "values": vals})
    return {"categories": [CHARGES[c] for c in lead] + (["Other categories"] if rest else []), "rows": rows}


def unit_costs(inv: pd.DataFrame, min_invoices: int = 3) -> list[dict]:
    """Freight per kilogram by vendor and mode: the like-for-like rate comparison."""
    out = []
    for mode, col in (("sea", "sea_freight"), ("air", "air_freight")):
        d = inv[(inv["mode"] == mode) & inv[col].notna() & inv["weight_kg"].notna() & (inv["weight_kg"] > 0)]
        if d.empty:
            continue
        d = d.assign(rate=d[col] / d["weight_kg"])
        for vendor, g in d.groupby("vendor_name"):
            if len(g) >= min_invoices:
                out.append({"vendor": vendor, "mode": mode, "invoices": int(len(g)),
                            "median_per_kg": round(float(g["rate"].median()), 2),
                            "min_per_kg": round(float(g["rate"].min()), 2), "max_per_kg": round(float(g["rate"].max()), 2)})
    return sorted(out, key=lambda r: (r["mode"], r["median_per_kg"]))


def quality(inv: pd.DataFrame, runs: pd.DataFrame | None) -> dict:
    n = len(inv)
    filled = lambda c: int((inv[c].fillna("").astype(str).str.strip() != "").sum()) if c in inv else 0   # noqa: E731
    sea, air = inv[inv["mode"] == "sea"], inv[inv["mode"] == "air"]
    rows = [("Invoice No", filled("invoice_number"), n), ("Invoice Date", int(inv["invoice_date"].notna().sum()), n),
            ("Vendor Name", int((inv["vendor_name"] != "(no vendor)").sum()), n), ("WEIGHT", filled("weight"), n),
            ("Container Number (sea)", int((sea["container_number"].fillna("").astype(str).str.strip() != "").sum()), len(sea)),
            ("Bill of Lading / HBL (sea)", int((sea["bill_of_lading_hbl"].fillna("").astype(str).str.strip() != "").sum()), len(sea)),
            ("MAWB No (air)", int((air["mawb_no"].fillna("").astype(str).str.strip() != "").sum()), len(air)),
            ("HAWB No (air)", int((air["hawb_no"].fillna("").astype(str).str.strip() != "").sum()), len(air)),
            ("At least one charge", int((inv["total"] > 0).sum()), n)]
    out = {"fill_rates": [{"field": f, "filled": a, "of": b, "rate": pct(a, b)} for f, a, b in rows if b],
           "review_status": {str(k): int(v) for k, v in inv.get("review_status", pd.Series(dtype=str)).value_counts().items()},
           "extraction_status": {str(k): int(v) for k, v in inv.get("extraction_status", pd.Series(dtype=str)).value_counts().items()}}
    if runs is not None and len(runs):
        ms = pd.to_numeric(runs["processing_time_ms"], errors="coerce").dropna()
        routes = runs["route"].fillna("unknown").value_counts()
        out["runs"] = {"with_run_record": int(len(runs)),
                       "pipeline_versions": {str(k): int(v) for k, v in runs["pipeline_version"].value_counts().items()},
                       "routes": {str(k): int(v) for k, v in routes.items()},
                       "invoices_with_warnings": int((pd.to_numeric(runs["warnings"], errors="coerce").fillna(0) > 0).sum())}
    return out


# ------------------------------------------------------------------- findings --
def _finding(kind, severity, title, detail, invoices, **metric) -> dict:
    return {"type": kind, "severity": severity, "title": title, "detail": detail,
            "invoices": [str(i) for i in invoices if str(i) not in ("", "None", "nan")][:12], "metric": metric}


def outlier_charges(inv: pd.DataFrame, limit: int = 25) -> list[dict]:
    """A charge far from what peers pay for the same thing (robust z-score on log amounts, |z| >= 3.5).

    Peers are the same vendor's other invoices when it has at least 8 with that charge, otherwise every
    invoice with that charge (at least 12). Median/MAD are used so one extreme value cannot hide itself."""
    out = []
    for code, title in CHARGES.items():
        s = inv.loc[inv[code].notna() & (inv[code] > 0), ["invoice_number", "vendor_name", code]]
        if len(s) < MIN_PEERS_CATEGORY:
            continue
        logs = np.log10(s[code].to_numpy(float))
        g_med, g_mad = float(np.median(logs)), float(np.median(np.abs(logs - np.median(logs))))
        vstats = {v: (float(np.median(x)), float(np.median(np.abs(x - np.median(x)))), len(x))
                  for v, x in ((v, np.log10(g[code].to_numpy(float))) for v, g in s.groupby("vendor_name"))
                  if len(x) >= MIN_PEERS_VENDOR}
        for r, lx in zip(s.itertuples(index=False), logs):
            med, mad, peers, scope = g_med, g_mad, len(s), "all vendors"
            if r.vendor_name in vstats and vstats[r.vendor_name][1] > 1e-6:
                med, mad, peers = vstats[r.vendor_name]
                scope = "this vendor's own invoices"
            if mad <= 1e-6:
                continue
            z = 0.6745 * (lx - med) / mad
            if abs(z) >= Z_CUT:
                amt, typical = float(getattr(r, code)), 10 ** med
                ratio = amt / typical
                out.append(_finding(
                    "outlier_charge", "high" if abs(z) >= 6 else "medium" if abs(z) >= 4.5 else "low",
                    f"{title} of {inr(amt)} is {ratio:.1f}x the typical amount" if ratio >= 1 else
                    f"{title} of {inr(amt)} is only {ratio * 100:.0f}% of the typical amount",
                    f"{r.vendor_name}: typical {title} is {inr(typical)} across {peers} invoices ({scope}).",
                    [r.invoice_number], z=round(float(z), 2), amount=round(amt, 2), typical=round(typical, 2),
                    peers=int(peers), category=title))
    return sorted(out, key=lambda f: -abs(f["metric"]["z"]))[:limit]


def duplicates(inv: pd.DataFrame) -> list[dict]:
    out = []
    d = inv[inv["invoice_number"].fillna("").astype(str).str.strip() != ""]
    for num, g in d.groupby("invoice_number"):
        if len(g) > 1:
            names = sorted(set(g["vendor_name"]))
            out.append(_finding("duplicate_number", "high", f"Invoice number {num} appears {len(g)} times",
                                (f"Stored under {len(names)} vendor names: " + "; ".join(names) +
                                 ". One invoice under two spellings, or billed twice." if len(names) > 1 else
                                 f"{names[0]}: the same number is stored {len(g)} times with different details. "
                                 "Check whether it was billed twice."),
                                [num], vendors=sorted(set(g["vendor_name"])), total=round(float(g["total"].sum()), 2)))
    p = inv[(inv["total"] > 0) & inv["invoice_date"].notna()]
    for (vendor, date, total), g in p.groupby(["vendor_name", "invoice_date", p["total"].round(0)]):
        nums = sorted(set(g["invoice_number"].astype(str)))
        if len(nums) > 1:
            out.append(_finding("possible_double_billing", "medium",
                                f"{vendor}: {len(nums)} invoices on {date:%d %b %Y} for the same {inr(total)}",
                                "Different invoice numbers, same vendor, date and amount. Check for double billing.",
                                nums, amount=float(total)))
    b = inv[(inv["total"] > 0) & (inv["bill_of_lading_hbl"].fillna("").astype(str).str.strip() != "")]
    for (bl, total), g in b.groupby(["bill_of_lading_hbl", b["total"].round(0)]):
        nums = sorted(set(g["invoice_number"].astype(str)))
        if len(nums) > 1 and g["invoice_date"].nunique() > 1:
            out.append(_finding("same_shipment_same_amount", "medium",
                                f"B/L {bl} billed {len(nums)} times for {inr(total)}",
                                "The same bill of lading carries the same amount on different invoices.",
                                nums, amount=float(total)))
    return out


def fx_checks(inv: pd.DataFrame, limit: int = 15) -> list[dict]:
    """Exchange rates out of line with the other invoices of the same month (or overall when a month is thin)."""
    rows = []
    for r in inv.itertuples(index=False):
        for cur, rate in re.findall(r"\b([A-Z]{3})\s*([\d]+(?:\.\d+)?)", str(getattr(r, "ex_rate", "") or "")):
            if cur != "INR" and float(rate) > 0:
                rows.append((r.invoice_number, r.vendor_name, r.invoice_date, cur, float(rate)))
    fx = pd.DataFrame(rows, columns=["invoice", "vendor", "date", "cur", "rate"])
    out = []
    for cur, g in fx.dropna(subset=["date"]).groupby("cur"):
        for r in g.itertuples():
            near = g[(g.index != r.Index) & ((g["date"] - r.date).abs() <= pd.Timedelta(days=45))]
            if len(near) < 3:           # too few invoices around that date to say what the usual rate was
                continue
            ref = float(near["rate"].median())
            dev = (r.rate - ref) / ref
            if abs(dev) >= 0.03:
                out.append(_finding("fx_rate", "medium" if abs(dev) >= 0.06 else "low",
                                    f"{cur} rate {r.rate:g} is {dev * 100:+.1f}% vs the usual {ref:.2f}",
                                    f"{r.vendor}: compared with {len(near)} other invoices dated within 45 days. "
                                    "A higher rate inflates every converted charge.",
                                    [r.invoice], currency=cur, rate=r.rate, reference=round(ref, 4), deviation=round(dev, 4)))
    return sorted(out, key=lambda f: -abs(f["metric"]["deviation"]))[:limit]


def _norm_vendor(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", LEGAL.sub(" ", name.lower()))).strip()


def vendor_aliases(inv: pd.DataFrame) -> list[dict]:
    """Vendor names that look like one company spelled several ways (they split that vendor's totals)."""
    names = sorted(n for n in inv["vendor_name"].unique() if n != "(no vendor)")
    norm = {n: _norm_vendor(n) for n in names}
    seen, out = set(), []
    for i, a in enumerate(names):
        if a in seen or not norm[a]:
            continue
        group = [a]
        for b in names[i + 1:]:
            if b in seen or not norm[b]:
                continue
            ta, tb = norm[a].split(), norm[b].split()
            same_first = ta[0] == tb[0] and ta[0] not in GENERIC and len(ta[0]) >= 4
            ratio = SequenceMatcher(None, norm[a], norm[b]).ratio()
            prefix = norm[a].startswith(norm[b] + " ") or norm[b].startswith(norm[a] + " ")
            if norm[a] == norm[b] or (same_first and (ratio >= 0.75 or prefix)) or ratio >= 0.92:
                group.append(b)
        if len(group) > 1:
            seen.update(group)
            d = inv[inv["vendor_name"].isin(group)]
            out.append(_finding("vendor_spelling", "low", f"{len(group)} vendor names may be one company",
                                " / ".join(group) + f" — together {len(d)} invoices, {inr(d['total'].sum())}. "
                                "Merging them gives the true vendor total.",
                                d["invoice_number"].tolist(), names=group, spend=round(float(d["total"].sum()), 2)))
    return sorted(out, key=lambda f: -f["metric"]["spend"])[:15]


def rate_changes(inv: pd.DataFrame, limit: int = 12) -> list[dict]:
    """A vendor's typical amount for a charge moved 20% or more between two consecutive months (>= 3 invoices each)."""
    out = []
    d = inv.dropna(subset=["month"])
    for code, title in CHARGES.items():
        s = d.loc[d[code].notna() & (d[code] > 0), ["vendor_name", "month", code, "invoice_number"]]
        for vendor, g in s.groupby("vendor_name"):
            m = g.groupby("month")[code].agg(["median", "size"]).sort_index()
            months = list(m.index)
            for a, b in zip(months, months[1:]):
                if (pd.Period(b, "M") - pd.Period(a, "M")).n != 1 or m.loc[a, "size"] < 3 or m.loc[b, "size"] < 3:
                    continue
                ch = (m.loc[b, "median"] - m.loc[a, "median"]) / m.loc[a, "median"]
                if abs(ch) >= 0.20:
                    out.append(_finding("rate_change", "medium" if ch >= 0.35 else "low",
                                        f"{vendor}: {title} {'up' if ch > 0 else 'down'} {abs(ch) * 100:.0f}% in {b}",
                                        f"Typical amount moved from {inr(m.loc[a, 'median'])} ({a}) to {inr(m.loc[b, 'median'])} ({b}).",
                                        g.loc[g["month"] == b, "invoice_number"].tolist(), change=round(float(ch), 4),
                                        category=title, month=b))
    return sorted(out, key=lambda f: -abs(f["metric"]["change"]))[:limit]


def unusual_invoices(inv: pd.DataFrame, limit: int = 12) -> list[dict]:
    """Invoices whose mix of charges is unlike the rest (Isolation Forest; needs at least 40 invoices)."""
    d = inv[inv["total"] > 0]
    if len(d) < 40:
        return []
    from sklearn.ensemble import IsolationForest

    codes = list(CHARGES)
    x = np.log1p(d[codes].fillna(0.0).to_numpy(float))
    model = IsolationForest(n_estimators=200, contamination=0.02, random_state=17).fit(x)
    score = -model.score_samples(x)
    flag = model.predict(x) == -1
    share = d[codes].fillna(0.0).div(d["total"], axis=0)
    typical = share.groupby(d["mode"]).transform("median")
    out = []
    for i in np.argsort(-score):
        if not flag[i] or len(out) >= limit:
            continue
        row = d.iloc[i]
        diff = (share.iloc[i] - typical.iloc[i]).sort_values(key=abs, ascending=False)
        why = ", ".join(f"{CHARGES[c]} {share.iloc[i][c] * 100:.0f}% of the invoice (usually {typical.iloc[i][c] * 100:.0f}%)"
                        for c in diff.index[:2])
        out.append(_finding("unusual_invoice", "low", f"Unusual mix of charges on invoice {row['invoice_number']}",
                            f"{row['vendor_name']}, {inr(row['total'])}: {why}.", [row["invoice_number"]],
                            score=round(float(score[i]), 4)))
    return out


def missing_data(inv: pd.DataFrame) -> list[dict]:
    out = []
    blank = lambda c: inv[c].fillna("").astype(str).str.strip() == ""      # noqa: E731
    checks = [("no charges", inv["total"] <= 0, "No charge amount was stored for these invoices."),
              ("no invoice date", inv["invoice_date"].isna(), "They are left out of every monthly figure."),
              ("no vendor", inv["vendor_name"] == "(no vendor)", "They cannot be attributed to a vendor."),
              ("sea shipment without B/L or container", (inv["mode"] == "sea") & blank("bill_of_lading_hbl") & blank("container_number"),
               "Neither a bill of lading nor a container number was captured.")]
    for name, mask, why in checks:
        if int(mask.sum()):
            out.append(_finding("missing_data", "medium" if name == "no charges" else "low",
                                f"{int(mask.sum())} invoice(s) with {name}", why, inv.loc[mask, "invoice_number"].tolist()))
    return out


# ------------------------------------------------------------------- forecast --
def forecast(months: list[dict], horizon: int = 3, now: pd.Timestamp | None = None) -> dict:
    """Monthly spend for the next quarter (Holt's linear trend). Needs six complete months."""
    now = now or pd.Timestamp.now()
    series = [m for m in months if m["month"] < now.strftime("%Y-%m")]      # the running month is incomplete
    active = [m for m in series if m["invoices"] > 0]
    if len(active) < 6 or sum(m["invoices"] for m in series) < 30:
        return {"available": False, "reason": f"Needs 6 complete months with invoices and at least 30 invoices; there are "
                                              f"{len(active)} such months and {sum(m['invoices'] for m in series)} invoices."}
    series = series[next(i for i, m in enumerate(series) if m["invoices"] > 0):]
    y = np.array([m["spend"] for m in series], float)

    def fit(v):
        best = None
        for a in np.linspace(0.1, 0.9, 9):
            for b in np.linspace(0.05, 0.5, 6):
                level, trend, res = v[0], v[1] - v[0], []
                for obs in v[1:]:
                    res.append(obs - (level + trend))
                    new = a * obs + (1 - a) * (level + trend)
                    trend = b * (new - level) + (1 - b) * trend
                    level = new
                sse = float(np.sum(np.square(res)))
                if best is None or sse < best[0]:
                    best = (sse, level, trend, res)
        return best

    _, level, trend, res = fit(y)
    sd = float(np.std(res, ddof=1)) if len(res) > 2 else 0.0
    last = pd.Period(series[-1]["month"], "M")
    level, trend = float(level), float(trend)
    pts = [{"month": str(last + h), "spend": round(max(0.0, level + h * trend), 2),
            "low": round(max(0.0, level + h * trend - 1.28 * sd * math.sqrt(h)), 2),
            "high": round(max(0.0, level + h * trend + 1.28 * sd * math.sqrt(h)), 2)} for h in range(1, horizon + 1)]
    out = {"available": True, "method": "Holt linear trend, 80% interval from past one-step errors",
           "based_on_months": len(series), "points": pts}
    if len(y) >= 9:      # how wrong was it on the last three months it did not see?
        _, l2, t2, _ = fit(y[:-3])
        pred = np.array([max(0.0, float(l2) + h * float(t2)) for h in (1, 2, 3)])
        actual = y[-3:]
        if np.all(actual > 0):
            out["backtest_error_pct"] = round(float(np.mean(np.abs(pred - actual) / actual)) * 100, 1)
    return out


def severity_note(findings: list) -> str:
    """KPI caption naming every non-zero severity count ("2 high, 1 medium, 3 low")."""
    sev = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        if f["severity"] in sev:
            sev[f["severity"]] += 1
    return ", ".join(f"{sev[s]} {s}" for s in ("high", "medium", "low") if sev[s])


# -------------------------------------------------------------------- analyse --
def analyse(frames: dict, now: pd.Timestamp | None = None) -> dict:
    inv, runs = frames["invoices"], frames.get("runs")
    now = now or pd.Timestamp.now()
    n, total = len(inv), float(inv["total"].sum()) if len(inv) else 0.0
    months, vendors, cats = monthly(inv), by_vendor(inv), by_category(inv)
    findings = (duplicates(inv) + outlier_charges(inv) + fx_checks(inv) + rate_changes(inv) + unusual_invoices(inv)
                + vendor_aliases(inv) + missing_data(inv)) if n else []
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    review = inv.get("review_status", pd.Series(dtype=str))
    pending = int((review == "PENDING").sum())
    dated = inv["invoice_date"].dropna()
    fc = forecast(months, now=now)
    q = quality(inv, runs) if n else {}
    full = [m for m in months if m["month"] < now.strftime("%Y-%m")]
    mom = None
    if len(full) >= 2 and full[-2]["spend"] > 0 and min(full[-1]["invoices"], full[-2]["invoices"]) >= 3:
        mom = round(100.0 * (full[-1]["spend"] - full[-2]["spend"]) / full[-2]["spend"], 1)
    hhi = round(sum((v["share"] / 100.0) ** 2 for v in vendors), 4) if vendors else None
    modes = [{"mode": k, "invoices": int(len(g)), "spend": round(float(g["total"].sum()), 2)}
             for k, g in inv.groupby("mode")] if n else []
    kpis = [
        {"key": "invoices", "label": "Invoices", "value": n, "display": f"{n:,}"},
        {"key": "spend", "label": "Total charges before GST", "value": round(total, 2), "display": inr(total)},
        {"key": "vendors", "label": "Vendors", "value": len(vendors), "display": f"{len(vendors):,}"},
        {"key": "avg", "label": "Average invoice", "value": round(total / n, 2) if n else 0, "display": inr(total / n) if n else "—"},
        {"key": "review", "label": "Awaiting review", "value": pending, "display": f"{pending:,}",
         "note": f"{pct(pending, n)}% of invoices" if n else ""},
        {"key": "findings", "label": "Findings to check", "value": len(findings), "display": f"{len(findings):,}",
         "note": severity_note(findings)},
    ]
    snap = {
        "meta": {"engine": ENGINE_VERSION, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "invoices": n,
                 "period_from": dated.min().strftime("%Y-%m-%d") if len(dated) else None,
                 "period_to": dated.max().strftime("%Y-%m-%d") if len(dated) else None,
                 "fingerprint": fingerprint(inv) if n else None, "source": frames.get("source", {}),
                 "currency": "INR, before GST"},
        "kpis": kpis, "monthly": months, "month_over_month_pct": mom, "vendors": vendors, "categories": cats,
        "vendor_category": vendor_category(inv) if n else {"categories": [], "rows": []},
        "modes": modes, "unit_costs": unit_costs(inv) if n else [], "vendor_concentration_hhi": hhi,
        "quality": q, "findings": findings, "forecast": fc,
        "review_queue": [{"invoice": str(r.invoice_number), "vendor": r.vendor_name,
                          "date": r.invoice_date.strftime("%Y-%m-%d") if pd.notna(r.invoice_date) else "",
                          "total": round(float(r.total), 2)}
                         for r in inv[review == "PENDING"].itertuples()][:200] if n else [],
        "methods": {
            "outlier_charge": f"Robust z-score on log amounts (median / MAD), flagged at |z| >= {Z_CUT}; peers are the vendor's own "
                              f"invoices when it has {MIN_PEERS_VENDOR}+, else all invoices with that charge ({MIN_PEERS_CATEGORY}+).",
            "unusual_invoice": "Isolation Forest (200 trees, 2% contamination) on log charge amounts; needs 40+ invoices.",
            "fx_rate": "Deviation of 3% or more from the median rate of the same currency on invoices dated within 45 days (needs 3+ of them).",
            "rate_change": "Median amount per vendor and charge, month over month, 20% or more with 3+ invoices in both months.",
            "forecast": "Holt's linear trend on complete months (needs 6+ months with invoices and 30+ invoices); interval = 1.28 x standard deviation of past one-step errors.",
            "duplicates": "Same invoice number more than once; same vendor + date + amount; same B/L + amount on different dates.",
            "vendor_spelling": "Names equal after removing legal suffixes, or sharing a distinctive first word with similar remainder.",
        },
    }
    snap["narrative"] = narrative(snap)
    return snap


def narrative(s: dict) -> list[str]:
    """Plain-language reading of the numbers, most important first."""
    n = s["meta"]["invoices"]
    if not n:
        return ["There are no invoices in the database for this period."]
    k = {x["key"]: x for x in s["kpis"]}
    out = [f"{n:,} invoices from {k['vendors']['value']:,} vendors, {k['spend']['display']} in charges before GST"
           + (f", dated {s['meta']['period_from']} to {s['meta']['period_to']}." if s["meta"]["period_from"] else ".")]
    v = s["vendors"]
    if len(v) >= 3:
        top = v[:3]
        out.append(f"The three largest vendors ({', '.join(x['vendor'] for x in top)}) account for "
                   f"{sum(x['share'] for x in top):.0f}% of spend.")
    c = s["categories"]
    if c:
        out.append(f"{c[0]['category']} is the largest cost at {c[0]['share']:.0f}% of spend"
                   + (f", followed by {c[1]['category']} ({c[1]['share']:.0f}%)." if len(c) > 1 else "."))
    if s["month_over_month_pct"] is not None:
        full = [m for m in s["monthly"] if m["month"] < s["meta"]["generated_at"][:7]]
        out.append(f"{full[-1]['month']} came to {inr(full[-1]['spend'])}, {s['month_over_month_pct']:+.0f}% against the month before.")
    hi = [f for f in s["findings"] if f["severity"] == "high"]
    if s["findings"]:
        nf = len(s["findings"])
        out.append(f"{nf} finding{'s' if nf != 1 else ''} need{'' if nf != 1 else 's'} a look ({len(hi)} high priority)"
                   + (f"; the first is: {hi[0]['title']}." if hi else "."))
    if k["review"]["value"]:
        out.append(f"{k['review']['value']:,} invoices are still waiting for review ({k['review']['note']}).")
    f = s["forecast"]
    if f.get("available"):
        p = f["points"]
        out.append(f"Expected spend for the next three months: {', '.join(inr(x['spend']) for x in p)}"
                   + (f" (the same method was off by {f['backtest_error_pct']:.0f}% on the last three months)." if "backtest_error_pct" in f else "."))
    else:
        out.append(f"No forecast yet: {f.get('reason', '')}")
    if n < 40:
        out.append("With fewer than 40 invoices, outlier and anomaly detection have too few peers to compare against; "
                   "those sections fill in as more invoices are stored.")
    return out
