"""Render the knowledge pack's exemplar figures from RAW data (docs/129).

Why re-render instead of copying the lab's PNG:

* **Confidentiality.** An exemplar ships inside SM to every other lab. The
  lab's own figure carries absolute frequencies, powers and the chip's
  identity in its axis ticks. Re-rendering with NORMALISED, UNLABELLED axes
  removes all of it.
* **Clause B, structurally.** The manual may only teach chip-independent
  geometry. A picture with no numbers on it *cannot* teach an absolute scale
  — the confidentiality fix and the generalisation rule are the same fix.
* **Raw provenance.** The stripped render is built from ``ds_raw.h5``, not
  from the node's fit overlay, so an exemplar shows what the MEASUREMENT
  looks like. Where a fit marker is drawn it is drawn deliberately (the
  branch-label-swap cases are only legible with the contradicting marker on
  the picture) and always without a number beside it.

The lab's own axis convention is preserved — frequency on x increasing
rightwards, power on y increasing upwards — because a judge trained on one
orientation misreads the other (the docs/122 axis-order lesson).

Run (any env with numpy + matplotlib + h5py/scipy):

    python -m quam_state_manager.generator.render_knowledge_exemplars \
        --family resonator_spectroscopy_vs_power [--only C1,F3] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Where each chip's archive lives on this workstation. An exemplar id names a
# chip, never a path, so the manual stays portable; a missing root simply
# skips those exemplars (loudly).
ARCHIVE_ROOTS = {
    "AS_10TQ9TC": [Path(r"D:\work\dataset\AS_10TQ9TC")],
    "CQT": [Path(r"D:\work\Customer_Codes\CQT\data")],
}


def find_run(chip: str, run_no: str, family: str) -> tuple[Path | None, str]:
    """Locate ``#<n>_<family>_...`` under a chip's date directories.

    The family is part of the key, not decoration: run numbers COLLIDE across
    (and even within) date directories — one pilot archive holds both
    ``#76_01_time_of_flight_…`` and ``#76_05_resonator_spectroscopy_vs_power_…``
    — so a number-only glob silently renders the wrong experiment. An
    exemplar that still resolves to more than one folder is REFUSED rather
    than guessed at.
    """
    hits: list[Path] = []
    for root in ARCHIVE_ROOTS.get(chip, []):
        if not root.exists():
            continue
        for date_dir in sorted(root.iterdir()):
            if not date_dir.is_dir():
                continue
            hits += [p for p in sorted(date_dir.glob(f"{run_no}_*"))
                     if family in p.name]
    if not hits:
        return None, "run folder not found on this machine"
    if len(hits) > 1:
        return None, ("ambiguous run number — " +
                      ", ".join(p.name for p in hits[:4]))
    return hits[0], ""


def load_map(run_folder: Path, qubit: str):
    """(freq, power, IQ_abs[freq, power]) for one qubit, or None.

    Routed through the ndview reader adapter: one pilot chip writes
    NetCDF-classic under the ``.h5`` name and h5py refuses those outright.
    """
    from quam_state_manager.core.ndview import _open_reader
    import numpy as np

    with _open_reader(run_folder / "ds_raw.h5") as f:
        coord = f.read_coord("qubit")
        if coord is None:
            return None
        names = [n.decode() if isinstance(n, bytes) else str(n)
                 for n in np.asarray(coord).tolist()]
        if qubit not in names:
            return None
        i = names.index(qubit)
        var = f.get("IQ_abs")
        if var is None:
            return None
        cube = np.asarray(f.read(var))
        if cube.ndim != 3:
            return None
        z = np.asarray(cube[i], dtype=float)          # (freq, power)
        pw = f.get("power")
        power = np.asarray(f.read(pw), dtype=float) if pw is not None else None
        ff = f.get("full_freq")
        if ff is not None:
            fa = np.asarray(f.read(ff), dtype=float)
            freq = fa[i] if fa.ndim == 2 else fa
        else:
            det = f.get("detuning")
            freq = np.asarray(f.read(det), dtype=float) if det is not None else None
    if power is None or freq is None:
        return None
    if z.shape != (freq.size, power.size):
        if z.shape == (power.size, freq.size):
            z = z.T
        else:
            return None
    return freq, power, z


def dip_track(z, *, min_z: float = 3.0):
    """Per-power-row dip index and its prominence — the geometry a tracker
    sees. Rows below *min_z* are returned as NaN: an untraceable row is part
    of the picture (that is what an snr-floor exemplar must show), not a
    number to be invented."""
    import numpy as np

    n_freq, n_power = z.shape
    idx = np.full(n_power, np.nan)
    for p in range(n_power):
        col = z[:, p]
        med = float(np.median(col))
        noise = float(np.median(np.abs(np.diff(col)))) * 1.4826 / np.sqrt(2) + 1e-30
        j = int(np.argmin(col))
        if (med - float(col[j])) / noise >= min_z:
            idx[p] = j
    return idx


def render(run_folder: Path, qubit: str, out_png: Path, *,
           markers: dict | None = None) -> dict:
    """Write the stripped exemplar PNG; return what was drawn."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    got = load_map(run_folder, qubit)
    if got is None:
        return {"ok": False, "why": "cube unreadable or qubit absent"}
    freq, power, z = got

    # NORMALISED axes: every absolute scale leaves the picture here.
    fx = (freq - freq.min()) / (np.ptp(freq) or 1.0)
    py = (power - power.min()) / (np.ptp(power) or 1.0)
    order = np.argsort(py)
    py, z = py[order], z[:, order]

    lo, hi = np.nanpercentile(z, 2), np.nanpercentile(z, 98)
    fig, ax = plt.subplots(figsize=(4.6, 3.4), dpi=150)
    ax.pcolormesh(fx, py, z.T, shading="nearest", cmap="viridis",
                  vmin=lo, vmax=hi)

    track = dip_track(z)
    ok = ~np.isnan(track)
    if ok.any():
        ax.plot(fx[track[ok].astype(int)], py[ok], color="#ff8c1a", lw=1.1,
                alpha=0.95)

    drawn = []
    for key, colour, style in (("dressed", "#38bdf8", "--"),
                               ("bare", "#e879f9", ":")):
        v = (markers or {}).get(key)
        if isinstance(v, (int, float)) and freq.min() <= v <= freq.max():
            ax.axvline((v - freq.min()) / (np.ptp(freq) or 1.0),
                       color=colour, ls=style, lw=1.4)
            drawn.append(key)
    v = (markers or {}).get("optimal_power")
    if isinstance(v, (int, float)) and power.min() <= v <= power.max():
        ax.axhline((v - power.min()) / (np.ptp(power) or 1.0),
                   color="#ef4444", lw=1.4)
        drawn.append("optimal_power")

    # No numbers anywhere — only the two directions, which are geometry.
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_xlabel("frequency \u2192", fontsize=9)
    ax.set_ylabel("readout power \u2192", fontsize=9)
    ax.set_xlim(fx.min(), fx.max()); ax.set_ylim(py.min(), py.max())
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)
    return {"ok": True, "grid": [int(freq.size), int(power.size)],
            "markers_drawn": drawn,
            "rows_traceable": int(ok.sum()), "rows_total": int(power.size)}


def parse_exemplar(ex: str):
    """``CHIP/#RUN/qubit`` -> (chip, run_no, qubit)."""
    parts = ex.split("/")
    if len(parts) != 3 or not parts[1].startswith("#"):
        return None
    return parts[0], parts[1], parts[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="resonator_spectroscopy_vs_power")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from quam_state_manager.core.autofit import knowledge

    pack_path = knowledge.pack_path(args.family, args.version)
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    out_root = pack_path.parent / "exemplars"
    want = {c.strip() for c in args.only.split(",") if c.strip()}

    index, missing = [], []
    for case in pack.get("cases") or []:
        if want and case["id"] not in want:
            continue
        for ex in case.get("exemplars") or []:
            got = parse_exemplar(ex)
            if got is None:
                missing.append({"exemplar": ex, "why": "unparseable id"})
                continue
            chip, run_no, qubit = got
            folder, why = find_run(chip, run_no, args.family)
            if folder is None:
                missing.append({"exemplar": ex, "case": case["id"], "why": why})
                continue
            # markers come from the run's OWN fit record — an exemplar of a
            # mislabelled fit is only legible with the wrong marker on it
            markers = {}
            try:
                d = json.loads((folder / "data.json").read_text(encoding="utf-8"))
                e = (d.get("fit_results") or {}).get(qubit) or {}
                markers = {"dressed": e.get("resonator_frequency"),
                           "bare": e.get("bare_resonator_frequency"),
                           "optimal_power": e.get("optimal_power")}
            except (OSError, ValueError):
                pass
            name = f"{chip}_{run_no.lstrip('#')}_{qubit}.png"
            out_png = out_root / case["id"] / name
            if args.dry_run:
                print(f"[dry] {case['id']}/{name}  <- {folder.name}")
                continue
            res = render(folder, qubit, out_png, markers=markers)
            if not res.get("ok"):
                missing.append({"exemplar": ex, "case": case["id"],
                                "why": res.get("why")})
                continue
            index.append({
                "case": case["id"], "exemplar": ex,
                "file": f"exemplars/{case['id']}/{name}",
                "chip": chip, "run": run_no, "qubit": qubit,
                "grid_freq_x_power": res["grid"],
                "markers_drawn": res["markers_drawn"],
                "rows_traceable": res["rows_traceable"],
                "rows_total": res["rows_total"],
            })
            print(f"[ok]  {case['id']}/{name}  grid={res['grid']} "
                  f"traceable={res['rows_traceable']}/{res['rows_total']}")

    if args.dry_run:
        return 0
    (out_root / "index.json").write_text(json.dumps({
        "schema": "smknowex/v1",
        "family": args.family,
        "note": ("Axes are NORMALISED and UNLABELLED: no absolute frequency "
                 "or power leaves this pack, and a picture without numbers "
                 "cannot teach an absolute scale (Clause B). Orientation "
                 "follows the labs' own convention: frequency rightwards, "
                 "readout power upwards. Overlays: orange = per-row dip "
                 "track (rows where no dip clears the noise are simply "
                 "absent), cyan dashed = the record's dressed frequency, "
                 "magenta dotted = its bare frequency, red = its chosen "
                 "power. Markers are the RECORD's claims, drawn even when "
                 "they contradict the map — that contradiction is the "
                 "lesson in the branch-swap and off-feature cases."),
        "rendered": index, "missing": missing,
    }, indent=1), encoding="utf-8")
    print(f"\nrendered {len(index)}, missing {len(missing)}")
    for m in missing:
        print("  MISSING:", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
