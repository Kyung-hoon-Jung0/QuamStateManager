"""Which Python environment a chip was built in, and whether another env matches.

A rebuild in a different QM stack writes a different chip: a newer
``quam_builder`` adds gate variants an older one cannot load, and the other way
round. Re-generate therefore starts from the env the chip was built in.

Two sources, in this order:

1. ``<chip>/.regen/build_env.json`` -- written by State Manager's own Generate
   and Re-generate builds (a dot-folder, so QUAM's folder load never reads it).
2. The chip's own ``__package_versions__`` (quam's serialisation stamps it into
   every saved state) matched against the cached probes of the envs State
   Manager already knows. Nothing is spawned for this.

A path read from a chip folder is never run on its own say-so: the caller only
uses it when :func:`config_generator.known_interpreter` accepts it.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIR = ".regen"
_FILE = "build_env.json"
_KEYS = ("quam", "quam_builder")


def record(folder, python_path: str, versions: dict | None = None) -> None:
    """Remember *python_path* as the env *folder*'s chip was built in.
    Best-effort: a write failure only means the next Re-generate falls back
    to the version match."""
    try:
        d = Path(folder) / _DIR
        d.mkdir(parents=True, exist_ok=True)
        payload = {"python": str(python_path),
                   "versions": {k: (versions or {}).get(k) for k in _KEYS}}
        (d / _FILE).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def recorded(folder) -> str | None:
    """The interpreter recorded for *folder*, or None."""
    try:
        data = json.loads((Path(folder) / _DIR / _FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    py = data.get("python") if isinstance(data, dict) else None
    return py if isinstance(py, str) and py else None


def chip_versions(state: dict) -> dict:
    """``{quam, quam_builder}`` the chip was saved with (empty when unstamped)."""
    pv = (state or {}).get("__package_versions__")
    if not isinstance(pv, dict):
        return {}
    return {k: pv.get(k) for k in _KEYS if isinstance(pv.get(k), str)}


def versions_match(chip: dict, env: dict) -> bool | None:
    """True / False when every stamped package compares; None when the chip
    carries no stamp or the env reported no version for a stamped package."""
    if not chip:
        return None
    for k, v in chip.items():
        ev = (env or {}).get(k)
        if not ev:
            return None
        if ev != v:
            return False
    return True


def choose(folders, state: dict, selected: str | None,
           cached_probes: dict[str, dict], is_known) -> dict:
    """Decide which env a Re-generate of this chip should start in.

    *folders*: chip folders to look for a recorded env in (live first).
    *cached_probes*: ``{python: probe_result}`` already on disk (no spawn).
    *is_known*: ``callable(python) -> bool`` (known_interpreter).

    Returns ``{python, source, chip_versions, selected, selected_versions,
    selected_matches, message, level}`` where ``source`` is ``"recorded"``,
    ``"versions"`` or None, and ``python`` is the env to use (None = keep
    the selected one)."""
    chip = chip_versions(state)
    sel_v = ((cached_probes.get(selected) or {}).get("versions") or {}) if selected else {}
    sel_match = versions_match(chip, sel_v)
    out = {"python": None, "source": None, "chip_versions": chip,
           "selected": selected, "selected_versions":
               {k: sel_v.get(k) for k in _KEYS if sel_v.get(k)},
           "selected_matches": sel_match, "message": None, "level": None}
    from quam_state_manager.core.config_generator import interpreter_key
    for f in folders:
        if not f:
            continue
        py = recorded(f)
        if py and is_known(py):
            out["python"], out["source"] = py, "recorded"
            break
    if out["python"] is None and chip and sel_match is not True:
        for py, res in cached_probes.items():
            if (isinstance(res, dict) and res.get("usable")
                    and versions_match(chip, res.get("versions") or {}) is True
                    and is_known(py)):
                out["python"], out["source"] = py, "versions"
                break
    stamp = ", ".join(f"{k} {v}" for k, v in chip.items()) or "no version stamp"
    use = out["python"] or selected
    same = (out["python"] is None or selected is None
            or interpreter_key(out["python"]) == interpreter_key(selected))
    if out["python"] is not None:
        how = ("the env it was built in" if out["source"] == "recorded"
               else "an env with the same versions")
        out["message"] = (f"This chip was built with {stamp}. The rebuild uses "
                          f"{how}: {out['python']}"
                          + ("" if same else
                             f" (the selected env {selected} differs; pick it on step 1 "
                             "to rebuild there instead)."))
        out["level"] = "info" if same else "warn"
    elif chip and sel_match is False:
        sv = ", ".join(f"{k} {v}" for k, v in out["selected_versions"].items())
        out["message"] = (f"This chip was built with {stamp}, but the selected env "
                          f"{use} has {sv}. A rebuild there writes a different "
                          "chip (gate variants and fields differ) that may load only "
                          "in that env. Pick an env with the chip's versions on step 1.")
        out["level"] = "warn"
    elif use:
        out["message"] = f"This chip was built with {stamp}. The rebuild uses {use}."
        out["level"] = "info"
    return out
