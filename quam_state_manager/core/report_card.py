"""Chip report card — a dated, shareable summary of a chip's current state.

Computed entirely from the SAME trust-gated MetricRecords the Chip Status summary
uses (``query.get_topology``): an unphysical fit (−473µs T2) is a *bad fit*, never
averaged or counted below-spec. Renders to Markdown / CSV / HTML for archiving or
sending to a colleague. Pure — no Flask, no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quam_state_manager.core import chip_health

# Headline per-qubit metrics that decide "below spec" (mirrors the live page's
# buildHealthSummary NODE_METRICS so the card and the on-screen header agree).
_NODE_VERDICT_METRICS = ["gate_fidelity_avg", "assignment_fidelity", "T1", "T2ramsey", "T2echo"]
_OUTLIER_K = 3.5


def _gated(entity: dict, key: str):
    rec = (entity.get("metrics") or {}).get(key)
    return rec["value"] if rec else entity.get(key)


def _median(xs: list[float]):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2


def _outliers(entities: list[dict], key: str) -> list[tuple[str, float, float]]:
    """Robust MAD outliers (modified z ≥ 3.5) over the gated values — same method
    as the dashboard. Returns [(id, value, score)]; empty when <5 pts / no spread."""
    vals = [(_gated(e, key), e.get("id") or e.get("pair_id")) for e in entities]
    clean = [(v, i) for (v, i) in vals if isinstance(v, (int, float))]
    if len(clean) < 5:
        return []
    med = _median([v for v, _ in clean])
    mad = _median([abs(v - med) for v, _ in clean])
    if not mad:
        return []
    out = []
    for v, i in clean:
        score = abs(v - med) / (1.4826 * mad)
        if score >= _OUTLIER_K:
            out.append((i, v, score))
    return sorted(out, key=lambda t: -t[2])


def _days_ago(epoch_ms, now: datetime):
    if not epoch_ms:
        return None
    return (now.timestamp() * 1000 - epoch_ms) / 86_400_000.0


def build_report(engine, *, chip_name: str, generated_at: datetime | None = None,
                 thresholds: dict | None = None, diag_findings: list | None = None) -> dict[str, Any]:
    """Build the report data dict from the gated topology records."""
    now = generated_at or datetime.now(timezone.utc)
    th = thresholds or chip_health.DEFAULT_THRESHOLDS
    topo = engine.get_topology()
    nodes, edges, summ = topo["nodes"], topo["edges"], topo["summary"]

    # Per-qubit worst verdict across the headline metrics → below-spec set.
    below = []
    for n in nodes:
        worst = None
        worst_metric = None
        for m in _NODE_VERDICT_METRICS:
            vr = chip_health.verdict(_gated(n, m), th.get(m))
            if vr == "fail" or (vr == "warn" and worst != "fail"):
                worst, worst_metric = vr, m
        if worst in ("warn", "fail"):
            below.append({"id": n["id"], "metric": worst_metric,
                          "value": _gated(n, worst_metric), "verdict": worst})
    cz_below = [{"id": e["pair_id"], "value": _gated(e, "cz_fidelity"),
                 "verdict": chip_health.verdict(_gated(e, "cz_fidelity"), th.get("cz_fidelity"))}
                for e in edges
                if chip_health.verdict(_gated(e, "cz_fidelity"), th.get("cz_fidelity")) in ("warn", "fail")]

    # Bad fits: measured-but-unphysical (raw present, gated value None, not a pointer).
    bad_fits = []
    for ents, keys in ((nodes, chip_health.METRIC_META), (edges, chip_health.METRIC_META)):
        for e in ents:
            for k, rec in (e.get("metrics") or {}).items():
                if rec.get("value") is None and not rec.get("unresolved") \
                        and isinstance(rec.get("raw"), (int, float)):
                    bad_fits.append({"id": e.get("id") or e.get("pair_id"), "metric": k, "raw": rec["raw"]})

    # Worst offenders: lowest gated value per headline metric (+ lowest CZ).
    worst = []
    for m in ("gate_fidelity_avg", "T1", "assignment_fidelity"):
        scored = [(n["id"], _gated(n, m)) for n in nodes if isinstance(_gated(n, m), (int, float))]
        if scored:
            i, v = min(scored, key=lambda t: t[1])
            worst.append({"id": i, "metric": m, "value": v, "label": chip_health.metric_meta(m)["label"]})
    cz_scored = [(e["pair_id"], _gated(e, "cz_fidelity")) for e in edges if isinstance(_gated(e, "cz_fidelity"), (int, float))]
    if cz_scored:
        i, v = min(cz_scored, key=lambda t: t[1])
        worst.append({"id": i, "metric": "cz_fidelity", "value": v, "label": chip_health.metric_meta("cz_fidelity")["label"]})

    outliers = []
    for m in _NODE_VERDICT_METRICS + ["f_01"]:
        for (i, v, sc) in _outliers(nodes, m):
            outliers.append({"id": i, "metric": m, "value": v, "score": round(sc, 1)})
    for (i, v, sc) in _outliers(edges, "cz_fidelity"):
        outliers.append({"id": i, "metric": "cz_fidelity", "value": v, "score": round(sc, 1)})

    diag = diag_findings or []
    diag_err = sum(1 for f in diag if (f.get("severity") if isinstance(f, dict) else getattr(f, "severity", None)) == "error")
    diag_warn = sum(1 for f in diag if (f.get("severity") if isinstance(f, dict) else getattr(f, "severity", None)) == "warning")

    # Overall verdict: fail if any structural error or any qubit/cz fail; warn if
    # any warn / below / structural warning; else pass.
    has_fail = diag_err > 0 or any(b["verdict"] == "fail" for b in below) or any(c["verdict"] == "fail" for c in cz_below)
    has_warn = diag_warn > 0 or bool(below) or bool(cz_below)
    verdict = "fail" if has_fail else ("warn" if has_warn else "pass")

    # Bad-fit count from the trust-floor summary (authoritative).
    bad_count = sum(v.get("bad", 0) for v in summ["nodes"].values()) + sum(v.get("bad", 0) for v in summ["edges"].values())

    return {
        "chip": chip_name,
        "generated_at": now.replace(microsecond=0).isoformat(),
        "verdict": verdict,
        "counts": {
            "qubits": summ["qubit_count"],
            "pairs": summ["pair_count"],
            "below_spec": len(below),
            "cz_below_spec": len(cz_below),
            "bad_fits": bad_count,
            "outliers": len(outliers),
            "structural_errors": diag_err,
            "structural_warnings": diag_warn,
            "oldest_calibration_days": _days_ago(summ["oldest_calibration"], now),
            "newest_calibration_days": _days_ago(summ["newest_calibration"], now),
        },
        "below_spec": below,
        "cz_below_spec": cz_below,
        "worst_offenders": worst,
        "bad_fits": bad_fits,
        "outliers": outliers,
        "metrics": summ["nodes"],
        "thresholds_source": ("your UI-edited thresholds" if thresholds
                              else "default spec thresholds"),
    }


# ── Renderers ────────────────────────────────────────────────────────────────

_VERDICT_WORD = {"pass": "✅ PASS", "warn": "⚠️ NEEDS ATTENTION", "fail": "❌ FAIL"}


def _fmt(v, key=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        if "fidelity" in key:
            return f"{v * 100:.2f}%"
        if key in ("T1", "T2ramsey", "T2echo"):
            return f"{v * 1e6:.1f} µs"
        if abs(v) >= 1e6 or (0 < abs(v) < 1e-3):
            return f"{v:.6e}"
        return f"{v:.4g}"
    return str(v)


def _age(days):
    if days is None:
        return "no timestamp"
    d = int(days)
    return "today" if d <= 0 else (f"{d} day ago" if d == 1 else f"{d} days ago")


def render_markdown(r: dict) -> str:
    c = r["counts"]
    out = [
        f"# Chip Report Card — {r['chip']}",
        "",
        f"_Generated {r['generated_at']} · thresholds: {r['thresholds_source']}_",
        "",
        f"## Overall: {_VERDICT_WORD[r['verdict']]}",
        "",
        "| | |",
        "|---|---|",
        f"| Qubits / Pairs | {c['qubits']} / {c['pairs']} |",
        f"| Below spec (qubits) | {c['below_spec']} |",
        f"| CZ pairs below spec | {c['cz_below_spec']} |",
        f"| Bad fits (unphysical, quarantined) | {c['bad_fits']} |",
        f"| Statistical outliers | {c['outliers']} |",
        f"| Structural issues | {c['structural_errors']} error(s), {c['structural_warnings']} warning(s) |",
        f"| Oldest calibration | {_age(c['oldest_calibration_days'])} |",
        f"| Newest calibration | {_age(c['newest_calibration_days'])} |",
        "",
    ]
    if r["worst_offenders"]:
        out += ["## Worst offenders", "", "| Qubit/Pair | Metric | Value |", "|---|---|---|"]
        out += [f"| {w['id']} | {w['label']} | {_fmt(w['value'], w['metric'])} |" for w in r["worst_offenders"]]
        out += [""]
    if r["below_spec"] or r["cz_below_spec"]:
        out += ["## Below spec", "", "| Qubit/Pair | Metric | Value | Verdict |", "|---|---|---|---|"]
        out += [f"| {b['id']} | {chip_health.metric_meta(b['metric'])['label']} | {_fmt(b['value'], b['metric'])} | {b['verdict']} |" for b in r["below_spec"]]
        out += [f"| {b['id']} | CZ Bell fidelity | {_fmt(b['value'], 'cz_fidelity')} | {b['verdict']} |" for b in r["cz_below_spec"]]
        out += [""]
    if r["bad_fits"]:
        out += ["## Bad fits (unphysical — excluded from all stats)", "", "| Qubit/Pair | Metric | Raw value |", "|---|---|---|"]
        out += [f"| {b['id']} | {chip_health.metric_meta(b['metric'])['label']} | {_fmt(b['raw'], b['metric'])} |" for b in r["bad_fits"]]
        out += [""]
    if r["outliers"]:
        out += ["## Statistical outliers (robust MAD ≥ 3.5)", "", "| Qubit/Pair | Metric | Value | × MAD |", "|---|---|---|---|"]
        out += [f"| {o['id']} | {chip_health.metric_meta(o['metric'])['label']} | {_fmt(o['value'], o['metric'])} | {o['score']} |" for o in r["outliers"]]
        out += [""]
    return "\n".join(out) + "\n"


def csv_safe_cell(v: Any) -> str:
    """Neutralize spreadsheet formula injection. A cell starting with = @ (or a
    control char) is a live formula in Excel/Sheets; + and - are too, unless the
    cell is a plain number (chip data is full of legit negatives, so keep those
    numeric). Chip names / ids / notes come from third-party state files, so an
    exported =HYPERLINK(...) / @cmd would execute on open. Prefix the trigger with
    a single quote."""
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    if not s:
        return s
    c0 = s[0]
    if c0 in ("=", "@", "\t", "\r"):
        return "'" + s
    if c0 in ("+", "-"):
        try:
            float(s)   # a real number → leave it numeric
        except ValueError:
            return "'" + s
    return s


def render_csv(r: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)

    def wr(cells):
        w.writerow([csv_safe_cell(c) for c in cells])

    wr(["section", "id", "metric", "value", "extra"])
    c = r["counts"]
    wr(["summary", r["chip"], "verdict", r["verdict"], r["generated_at"]])
    for k, v in c.items():
        wr(["summary", "", k, v, ""])
    for b in r["below_spec"]:
        wr(["below_spec", b["id"], b["metric"], b["value"], b["verdict"]])
    for b in r["cz_below_spec"]:
        wr(["below_spec", b["id"], "cz_fidelity", b["value"], b["verdict"]])
    for b in r["bad_fits"]:
        wr(["bad_fit", b["id"], b["metric"], b["raw"], "unphysical"])
    for o in r["outliers"]:
        wr(["outlier", o["id"], o["metric"], o["value"], o["score"]])
    return buf.getvalue()


def render_html(r: dict) -> str:
    # Self-contained HTML (Markdown rendered as a styled page) — easy to email/print.
    import html as _h
    c = r["counts"]
    rows = "".join(
        f"<tr><td>{_h.escape(str(k))}</td><td>{_h.escape(str(v))}</td></tr>"
        for k, v in [
            ("Qubits / Pairs", f"{c['qubits']} / {c['pairs']}"),
            ("Below spec (qubits)", c["below_spec"]),
            ("CZ pairs below spec", c["cz_below_spec"]),
            ("Bad fits (quarantined)", c["bad_fits"]),
            ("Statistical outliers", c["outliers"]),
            ("Structural issues", f"{c['structural_errors']} err / {c['structural_warnings']} warn"),
            ("Oldest calibration", _age(c["oldest_calibration_days"])),
            ("Newest calibration", _age(c["newest_calibration_days"])),
        ]
    )
    color = {"pass": "#2e7d32", "warn": "#e69500", "fail": "#c62828"}[r["verdict"]]

    def _tbl(title, headers, body_rows):
        if not body_rows:
            return ""
        head = "".join(f"<th>{_h.escape(x)}</th>" for x in headers)
        body = "".join("<tr>" + "".join(f"<td>{_h.escape(str(c))}</td>" for c in row) + "</tr>" for row in body_rows)
        return f"<h2>{_h.escape(title)}</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    worst = _tbl("Worst offenders", ["Qubit/Pair", "Metric", "Value"],
                 [[w["id"], w["label"], _fmt(w["value"], w["metric"])] for w in r["worst_offenders"]])
    below = _tbl("Below spec", ["Qubit/Pair", "Metric", "Value", "Verdict"],
                 [[b["id"], chip_health.metric_meta(b["metric"])["label"], _fmt(b["value"], b["metric"]), b["verdict"]] for b in r["below_spec"]]
                 + [[b["id"], "CZ Bell fidelity", _fmt(b["value"], "cz_fidelity"), b["verdict"]] for b in r["cz_below_spec"]])
    badf = _tbl("Bad fits (unphysical — excluded from stats)", ["Qubit/Pair", "Metric", "Raw value"],
                [[b["id"], chip_health.metric_meta(b["metric"])["label"], _fmt(b["raw"], b["metric"])] for b in r["bad_fits"]])
    outl = _tbl("Statistical outliers (MAD ≥ 3.5)", ["Qubit/Pair", "Metric", "Value", "× MAD"],
                [[o["id"], chip_health.metric_meta(o["metric"])["label"], _fmt(o["value"], o["metric"]), o["score"]] for o in r["outliers"]])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Chip Report — {_h.escape(r['chip'])}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#222}}
h1{{margin-bottom:0}} .verdict{{font-weight:700;color:{color};font-size:1.3em}}
table{{border-collapse:collapse;margin:0.5rem 0 1.5rem;width:100%}} th,td{{border:1px solid #ddd;padding:4px 8px;text-align:left;font-size:0.92em}}
th{{background:#f4f4f4}} .muted{{color:#777;font-size:0.85em}}</style></head>
<body><h1>Chip Report Card — {_h.escape(r['chip'])}</h1>
<p class="muted">Generated {_h.escape(r['generated_at'])} · thresholds: {_h.escape(r['thresholds_source'])}</p>
<p class="verdict">Overall: {_VERDICT_WORD[r['verdict']]}</p>
<table><tbody>{rows}</tbody></table>
{worst}{below}{badf}{outl}</body></html>"""
