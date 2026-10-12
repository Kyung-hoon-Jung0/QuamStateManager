"""Synthetic bias sweep that sets the smoothing defaults and caps (design 6.4).

Every model runs through the full request API on name-free synthetic maps at four strengths:
raw (0), the default, the cap (a large request clamped to it) and twice the cap with the
override. Bias and rms are in pixels/steps of the axis the value lives on, measured against
the model's own noiseless raw estimate (so a model bias such as a Fano shift is reported
separately as "model" and never hides a smoothing bias). Run as a script to print the table:

    python -m tests.mapfit_bias_sweep [output.txt]
"""
from __future__ import annotations

import sys

import numpy as np

from quam_state_manager.core.mapfit.api import analyze
from tests.mapfit_synthetic import sample

LABELS = {"erroramp": ("amp_prefactor", "nb_of_pulses"), "ridge": ("flux", "frequency"),
          "chevron": ("duration", "detuning"), "landscape": ("a_scan", "b_over_a"),
          "extremum": ("position", "index")}
# model -> (variants, noise, trials, value keys with their step)
PLAN = {
    "erroramp": ({"k=1": {}, "k=1.5": {"k": 1.5}, "nan 5%": {"nan": .05}}, .1, 2, {"a_pi": .005}),
    "ridge": ({"symmetric": {}, "fano q=.3": {"asymmetry": .3}}, .3, 3, {"x": 1., "y": 1.}),
    "chevron": ({"hyperbola": {}}, .1, 3, {"dv0": .1}),
    "landscape": ({"paraboloid": {}, "cubic .2": {"asymmetry": .2}}, .01, 3, {"x": 1., "y": 1.}),
    "extremum": ({"paraboloid": {}, "cubic .2": {"asymmetry": .2}}, .01, 3, {"x": 1., "y": 1.}),
}
LEVELS = ("raw", "default", "cap", "2x cap")


def _request(model, variant, noise, seed, smooth):
    options = dict(variant)
    holes = options.pop("nan", 0.)
    z, x, y, meta, _ = sample(model, noise=noise, seed=seed, **options)
    meta.pop("hwhm_pts", None)  # the width must come from the map, as on a node without one
    if holes:
        z = z.copy()
        z[np.random.default_rng(seed + 100).random(z.shape) < holes] = np.nan
    lx, ly = LABELS[model]
    return {"z": z, "x": x, "y": y, "meta": {**meta, "x": {"name": lx}, "y": {"name": ly}},
            "smooth": smooth, "fit": {"model": "auto"}}


def _settings(level, cap):
    if level == "raw":
        return {"sigma_x": 0, "sigma_y": 0}
    if level == "default":
        return {}
    if level == "cap":
        return {"sigma_x": 100, "sigma_y": 100}
    return {"sigma_x": 2 * cap[0], "sigma_y": 2 * cap[1], "override_cap": True}


def sweep():
    rows = []
    for model, (variants, noise, trials, keys) in PLAN.items():
        for name, variant in variants.items():
            reference = analyze(_request(model, variant, 0., 0, _settings("raw", None)))["fit"]["values"]
            _, _, _, _, truth = sample(model, **{k: v for k, v in variant.items() if k != "nan"})
            capped = analyze(_request(model, variant, 0., 0, _settings("cap", None)))["caps_applied"]
            cap = (capped["cap_x"], capped["cap_y"])
            for level in LEVELS:
                errors, ok, used = {key: [] for key in keys}, 0, None
                for seed in range(1, trials + 1):
                    out = analyze(_request(model, variant, noise, seed, _settings(level, cap)))
                    used = out["caps_applied"]
                    if out["fit"]["verdict"] != "ok":
                        continue
                    ok += 1
                    for key, step in keys.items():
                        errors[key].append((out["fit"]["values"][key] - reference[key]) / step)
                for key, step in keys.items():
                    e = np.asarray(errors[key])
                    rows.append({"model": model, "variant": name, "level": level, "key": key,
                                 "sigma_x": round(float(used["sigma_x"]), 3),
                                 "sigma_y": round(float(used["sigma_y"]), 3),
                                 "cap_x": round(float(cap[0]), 3), "cap_y": round(float(cap[1]), 3),
                                 "ok": f"{ok}/{trials}",
                                 "model_bias": round(float((reference[key] - truth[key]) / step), 3),
                                 "bias": round(float(e.mean()), 3) if e.size else None,
                                 "rms": round(float(np.sqrt(np.mean(e**2))), 3) if e.size else None})
    return rows


def table(rows):
    head = ("model", "variant", "level", "key", "sigma_x", "sigma_y", "cap_x", "cap_y", "ok",
            "model_bias", "bias", "rms")
    lines = ["  ".join(f"{h:>10}" for h in head)]
    lines += ["  ".join(f"{str(r[h]):>10}" for h in head) for r in rows]
    return "\n".join(lines)


if __name__ == "__main__":
    text = table(sweep())
    print(text)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
