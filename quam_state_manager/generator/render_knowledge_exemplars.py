"""Render the knowledge packs' exemplar figures from RAW data (docs/130).

Why re-render instead of copying the lab's PNG:

* **Confidentiality.** An exemplar ships inside SM to every other lab. The
  lab's own figure carries absolute frequencies, powers, fluxes and the
  chip's identity in its axis ticks. Re-rendering with NORMALISED, UNLABELLED
  axes removes all of it.
* **Clause B, structurally.** The manual may only teach chip-independent
  geometry. A picture with no numbers on it *cannot* teach an absolute scale
  — the confidentiality fix and the generalisation rule are the same fix.
* **Raw provenance.** The stripped render is built from ``ds_raw.h5``, not
  from the node's fit overlay, so an exemplar shows what the MEASUREMENT
  looks like. Where a fit marker is drawn it is drawn deliberately (the
  branch-label-swap and off-feature cases are only legible with the
  contradicting marker on the picture) and always without a number beside it.

Both map shapes are handled: a 2-D sweep renders as a pcolormesh with the
tracked feature over it, a 1-D spectroscopy trace renders as a line. The
labs' own axis conventions are preserved — frequency rightwards, the swept
quantity upwards — because a judge trained on one orientation misreads the
other (the docs/122 axis-order lesson).

Run (any env with numpy + matplotlib + h5py/scipy):

    python -m quam_state_manager.generator.render_knowledge_exemplars \
        [--family <name>] [--only C1,F3] [--dry-run]
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
# lab, never a path, so the manual stays portable; a missing root simply
# skips those exemplars (loudly).
ARCHIVE_ROOTS: dict[str, list[Path]] = {
    "CQT": [Path(r"D:\work\Customer_Codes\CQT\data")],
}
_DATASET = Path(r"D:\work\dataset")
if _DATASET.exists():
    for _lab in sorted(_DATASET.iterdir()):
        if _lab.is_dir() and any(_lab.glob("2026-*")):
            ARCHIVE_ROOTS.setdefault(_lab.name, [_lab])

# node-name prefixes that belong to each family (a run number alone is NOT a
# key — see find_run)
FAMILY_NODES = {
    "resonator_spectroscopy_vs_power": ["05_resonator_spectroscopy_vs_power"],
    "qubit_spectroscopy": ["08_qubit_spectroscopy"],
    "qubit_spectroscopy_vs_flux": ["09_qubit_spectroscopy_vs_flux",
                                   "03c_qubit_spectroscopy_vs_flux_qdac"],
    "resonator_spectroscopy_vs_flux": ["06_resonator_spectroscopy_vs_flux",
                                       "02e_resonator_spectroscopy_vs_flux_qdac"],
    "resonator_spectroscopy_vs_coupler_flux": [
        "07_resonator_spectroscopy_vs_coupler_flux"],
}

# which fit fields to draw as markers, per family: (field, colour, style)
MARKERS = {
    "resonator_spectroscopy_vs_power": [
        ("resonator_frequency", "#38bdf8", "--"),
        ("bare_resonator_frequency", "#e879f9", ":")],
    "qubit_spectroscopy": [("frequency", "#38bdf8", "--")],
    "qubit_spectroscopy_vs_flux": [
        ("qubit_frequency", "#38bdf8", "--"),
        ("upper_sweet_spot_frequency", "#e879f9", ":")],
    "resonator_spectroscopy_vs_flux": [
        ("resonator_frequency", "#38bdf8", "--")],
    "resonator_spectroscopy_vs_coupler_flux": [
        ("resonator_frequency", "#38bdf8", "--")],
}
# a horizontal marker on the SWEEP axis (the value the node picked there)
SWEEP_MARKERS = {
    "resonator_spectroscopy_vs_power": "optimal_power",
    "qubit_spectroscopy_vs_flux": "idle_offset",
    "resonator_spectroscopy_vs_flux": "idle_offset",
    "resonator_spectroscopy_vs_coupler_flux": "idle_offset",
}
SWEEP_LABEL = {"power": "readout power", "flux_bias": "flux bias",
               "current": "current", "attenuated_current": "current",
               "amplitude": "amplitude"}
# The labs do NOT share one orientation, and a judge trained on the wrong one
# misreads the map (the docs/122 axis-order lesson): the punch-out plots put
# frequency on x and power on y, while every flux family plots flux on x and
# frequency on y. Verified against the labs' own figures, not assumed.
SWEEP_ON_X = {
    "qubit_spectroscopy_vs_flux": True,
    "resonator_spectroscopy_vs_flux": True,
    "resonator_spectroscopy_vs_coupler_flux": True,
    "resonator_spectroscopy_vs_power": False,
}


def find_run(lab: str, run_no: str, family: str) -> tuple[Path | None, str]:
    """Locate ``#<n>_<family-node>_...`` under a lab's date directories.

    The node name is part of the key, not decoration: run numbers COLLIDE
    across (and even within) date directories — one archive holds both
    ``#76_01_time_of_flight_…`` and ``#76_05_resonator_spectroscopy_vs_power_…``
    — so a number-only glob silently renders the wrong experiment. An
    exemplar that still resolves to more than one folder is REFUSED rather
    than guessed at.
    """
    nodes = FAMILY_NODES.get(family, [])
    # Manuals cite runs either by number (``#314``) or by full folder name
    # (``#45_08_qubit_spectroscopy_061005``). Both are legitimate ways for a
    # human to name a run, so both resolve; only ambiguity is refused.
    exact = "_" in run_no
    hits: list[Path] = []
    for root in ARCHIVE_ROOTS.get(lab, []):
        if not root.exists():
            continue
        for date_dir in sorted(root.iterdir()):
            if not date_dir.is_dir():
                continue
            pattern = run_no if exact else f"{run_no}_*"
            for p in sorted(date_dir.glob(pattern)):
                tail = p.name.split("_", 1)[1] if "_" in p.name else ""
                if any(tail.startswith(n + "_") for n in nodes):
                    hits.append(p)
    if not hits:
        return None, "run folder not found on this machine"
    if len(hits) > 1:
        return None, "ambiguous run number — " + ", ".join(p.name for p in hits[:4])
    return hits[0], ""


def render(run_folder: Path, target: str, out_png: Path, *, family: str,
           markers: dict | None = None) -> dict:
    """Write the stripped exemplar PNG; return what was drawn."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from quam_state_manager.core.autofit import mapshapes as MS

    cube = MS.read_cube(run_folder, target)
    if cube is None:
        return {"ok": False, "why": "cube unreadable or target absent"}
    sign = MS.orient(cube)

    # NORMALISED axes: every absolute scale leaves the picture here.
    fx = (cube.freq - cube.freq.min()) / (np.ptp(cube.freq) or 1.0)

    def _fx(v):
        if not isinstance(v, (int, float)) or not np.isfinite(v):
            return None
        if not (cube.freq.min() <= v <= cube.freq.max()):
            return None
        return (v - cube.freq.min()) / (np.ptp(cube.freq) or 1.0)

    fig, ax = plt.subplots(figsize=(4.6, 3.4), dpi=150)
    drawn: list[str] = []
    extra: dict = {}

    if cube.n_sweep > 1:
        sy = (cube.sweep - cube.sweep.min()) / (np.ptp(cube.sweep) or 1.0)
        lo, hi = np.nanpercentile(cube.z, 2), np.nanpercentile(cube.z, 98)
        tr = MS.track_ridge(cube, sign=sign)
        ok = tr.ok
        sweep_x = SWEEP_ON_X.get(family, False)
        sm_field = SWEEP_MARKERS.get(family)
        sv = (markers or {}).get(sm_field) if sm_field else None
        svn = (((sv - cube.sweep.min()) / (np.ptp(cube.sweep) or 1.0))
               if isinstance(sv, (int, float))
               and cube.sweep.min() <= sv <= cube.sweep.max() else None)
        slabel = f"{SWEEP_LABEL.get(cube.sweep_name, 'sweep')} \u2192"
        if sweep_x:
            ax.pcolormesh(sy, fx, cube.z, shading="nearest", cmap="viridis",
                          vmin=lo, vmax=hi)
            if ok.any():
                ax.plot(sy[ok], fx[tr.pos[ok].astype(int)], color="#ff8c1a",
                        lw=1.1, alpha=0.95)
            if svn is not None:
                ax.axvline(svn, color="#ef4444", lw=1.4)
                drawn.append(sm_field)
            ax.set_xlabel(slabel, fontsize=9)
            ax.set_ylabel("frequency \u2192", fontsize=9)
            ax.set_xlim(sy.min(), sy.max()); ax.set_ylim(fx.min(), fx.max())
        else:
            ax.pcolormesh(fx, sy, cube.z.T, shading="nearest", cmap="viridis",
                          vmin=lo, vmax=hi)
            if ok.any():
                ax.plot(fx[tr.pos[ok].astype(int)], sy[ok], color="#ff8c1a",
                        lw=1.1, alpha=0.95)
            if svn is not None:
                ax.axhline(svn, color="#ef4444", lw=1.4)
                drawn.append(sm_field)
            ax.set_xlabel("frequency \u2192", fontsize=9)
            ax.set_ylabel(slabel, fontsize=9)
            ax.set_xlim(fx.min(), fx.max()); ax.set_ylim(sy.min(), sy.max())
        extra = {"grid": [cube.n_freq, cube.n_sweep],
                 "traceable": int(ok.sum()), "slices": int(cube.n_sweep),
                 "sweep_on_x": bool(sweep_x), "background": tr.background}
    else:
        y = cube.z[:, 0]
        ax.plot(fx, y, color="#334155", lw=1.0)
        ln = MS.shape_line(cube, sign=sign)
        if ln.pos_px is not None:
            ax.axvline(fx[int(ln.pos_px)], color="#ff8c1a", lw=1.0, alpha=0.6)
        extra = {"n_points": cube.n_freq, "depth_z": round(ln.depth_z, 1),
                 "secondaries": len(ln.secondaries)}
        # no numbers on the response axis either — only its direction
        ax.set_yticks([])
        ax.set_ylabel("readout response \u2192", fontsize=9)

    freq_on_y = cube.n_sweep > 1 and SWEEP_ON_X.get(family, False)
    for field, colour, style in MARKERS.get(family, []):
        v = _fx((markers or {}).get(field))
        if v is not None:
            (ax.axhline if freq_on_y else ax.axvline)(
                v, color=colour, ls=style, lw=1.4)
            drawn.append(field)

    ax.set_xticks([])
    ax.set_yticks([])
    if cube.n_sweep == 1:
        ax.set_xlabel("frequency \u2192", fontsize=9)
        ax.set_xlim(fx.min(), fx.max())
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)
    # These pictures ship inside the app, and 600-odd of them at full colour
    # depth is 32 MB of bundle. A viridis map plus four marker colours needs
    # nowhere near 24-bit colour: a 64-entry palette is visually identical at
    # the same pixel size and measured 3.4x smaller. Resolution is what a
    # reader (human or model) needs, and it is untouched.
    try:
        from PIL import Image
        with Image.open(out_png) as im:
            rgb = im.convert("RGB")
        rgb.quantize(colors=64, method=Image.MEDIANCUT).save(
            out_png, optimize=True)
    except Exception:      # noqa: BLE001 — a bigger file is not a failure
        pass
    return {"ok": True, "markers_drawn": drawn,
            "feature": "dip" if sign < 0 else "peak", **extra}


def parse_exemplar(ex: str):
    """``LAB/#RUN/target`` -> (lab, run_no, target)."""
    parts = ex.split("/")
    if len(parts) != 3 or not parts[1].startswith("#"):
        return None
    return parts[0], parts[1], parts[2]


def render_family(family: str, version: str, only: set[str], dry: bool) -> int:
    from quam_state_manager.core.autofit import knowledge

    pack_path = knowledge.pack_path(family, version)
    if not pack_path.exists():
        print(f"[skip] no pack for {family}")
        return 0
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    out_root = pack_path.parent / "exemplars"

    index, missing = [], []
    for case in pack.get("cases") or []:
        if only and case["id"] not in only:
            continue
        for ex in case.get("exemplars") or []:
            got = parse_exemplar(ex)
            if got is None:
                missing.append({"exemplar": ex, "case": case["id"],
                                "why": "unparseable id"})
                continue
            lab, run_no, target = got
            folder, why = find_run(lab, run_no, family)
            if folder is None:
                missing.append({"exemplar": ex, "case": case["id"], "why": why})
                continue
            markers = {}
            try:
                d = json.loads((folder / "data.json").read_text(encoding="utf-8"))
                e = (d.get("fit_results") or {}).get(target) or {}
                markers = {k: v for k, v in e.items()
                           if isinstance(v, (int, float))}
            except (OSError, ValueError):
                pass
            num = run_no.lstrip("#").split("_")[0]
            name = f"{lab}_{num}_{target}.png"
            out_png = out_root / case["id"] / name
            if dry:
                print(f"[dry] {family}/{case['id']}/{name}  <- {folder.name}")
                continue
            res = render(folder, target, out_png, family=family, markers=markers)
            if not res.get("ok"):
                missing.append({"exemplar": ex, "case": case["id"],
                                "why": res.get("why")})
                continue
            index.append({"case": case["id"], "exemplar": ex,
                          "file": f"exemplars/{case['id']}/{name}",
                          "chip": lab, "run": run_no, "qubit": target,
                          **{k: v for k, v in res.items()
                             if k not in ("ok",)}})
            print(f"[ok]  {family}/{case['id']}/{name}")

    if dry:
        return 0
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "index.json").write_text(json.dumps({
        "schema": "smknowex/v1",
        "family": family,
        "note": ("Axes are NORMALISED and UNLABELLED: no absolute frequency, "
                 "power or flux leaves this pack, and a picture without "
                 "numbers cannot teach an absolute scale (Clause B). "
                 "Orientation follows the labs' own convention: frequency "
                 "rightwards, the swept quantity upwards. Overlays: orange = "
                 "the tracked feature, cyan dashed and magenta dotted = the "
                 "record's own frequency claims, red = the sweep value it "
                 "chose. Markers are the RECORD's claims, drawn even when "
                 "they contradict the map — that contradiction is the lesson "
                 "in the mislabelled and off-feature cases. Whether the "
                 "feature is a dip or a peak is MEASURED per run, because the "
                 "readout rotation decides it and it differs between labs."),
        "rendered": index, "missing": missing,
    }, indent=1), encoding="utf-8")
    print(f"  {family}: rendered {len(index)}, missing {len(missing)}")
    for m in missing:
        print("    MISSING:", m)
    return len(index)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="", help="default: every pack present")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from quam_state_manager.core.autofit import knowledge

    fams = ([args.family] if args.family else
            sorted(p.name for p in (knowledge._ROOT / args.version).iterdir()
                   if p.is_dir()))
    only = {c.strip() for c in args.only.split(",") if c.strip()}
    total = 0
    for fam in fams:
        total += render_family(fam, args.version, only, args.dry_run)
    print(f"\ntotal rendered: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
