"""Named default-value presets for the Generate-Config wizard's Populate step.

Users save recurring default sets (x180/readout pulse values, resonator
timings, flux points, pair-gate seeds) under a name and re-apply them to any
new chip — the "memory archiving section" customers asked for. Presets live
SERVER-SIDE (``<instance>/gen_presets/<slug>.json``, one file per preset) so
they survive browser sessions and machine restarts, unlike the sessionStorage
wizard draft.

Storage schema (version 1)::

    {
      "version": 1,
      "name": "Lab-A 5-qubit defaults",
      "created_at": "2026-07-16T10:00:00Z",
      "updated_at": "2026-07-16T10:00:00Z",
      "sections": {
        "pulses": {
          "defaults":  {"x180_length": 40e-9, "x180_amplitude": 0.1},
          "overrides": {"q3": {"drag_alpha": 0.62}}
        },
        "qubit": {...}, "resonator": {...}, "flux": {...}, "pairs": {...}
      }
    }

``defaults`` holds column→value pairs uniform across every valued row at
capture time; ``overrides`` holds per-row (qubit id / pair id) values that
differed. Values are BASE units straight from ``spec.populate`` (Hz, ns, V,
dimensionless amp) — unit toggles never corrupt a preset. ``LO_frequency``
(auto-derived from RF), ``grid_location`` (chip-specific topology) and the
CR target LO/IF escape hatches are never part of a preset.

Concurrency mirrors the chip-decisions pattern (core/history.py): a module
lock + ``safe_io.atomic_write_json``. Two SM instances sharing an instance
dir are last-writer-wins per preset file — acceptable for a defaults store.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from quam_state_manager.core import safe_io

logger = logging.getLogger(__name__)

_PRESETS_DIRNAME = "gen_presets"
_presets_lock = threading.Lock()

# Sections and their allowed fields — a server-side mirror of the wizard's
# POP_QUBIT_COLS / POP_RESONATOR_COLS / POP_FLUX_COLS / POP_PULSE_FIELDS /
# POP_CZ_PAIR_COLS + POP_CR_PAIR_COLS, minus the never-preset fields
# (LO_frequency is re-derived from RF on apply; grid_location is topology;
# the CR target LO/IF escape hatches are chip-specific).
SECTION_FIELDS: dict[str, set] = {
    "qubit": {"RF_freq", "anharmonicity", "full_scale_power_dbm"},
    "resonator": {
        "RF_freq", "depletion_time", "time_of_flight", "readout_length",
        "readout_amplitude", "full_scale_power_dbm",
    },
    "flux": {
        "joint_offset", "independent_offset", "min_offset", "flux_point",
        "output_mode", "upsampling_mode",
    },
    "pulses": {
        "x180_length", "x180_amplitude", "drag_alpha", "drag_detuning",
        "saturation_length", "saturation_amplitude",
    },
    "pairs": {
        "cz_variant", "cz_interaction_duration", "cz_amplitude",
        "moving_qubit", "cz_order", "coupler_interaction_offset",
        "cr_drive_amplitude", "cr_cancel_amplitude", "cr_drive_phase",
        "cr_cancel_phase", "qc_correction_phase", "qt_correction_phase",
        # CR shape library + ZZ (Stark-CZ) seeds — docs/54. Target LO/IF stay
        # excluded (chip-specific frequency plan, like LO_frequency).
        "cr_shapes", "zz_detuning", "zz_drive_amplitude",
        "zz_flattop_length", "zz_flattop_flat_length",
    },
}

_MAX_NAME_LEN = 120
_MAX_OVERRIDE_ROWS = 500
_MAX_SERIALIZED_BYTES = 200 * 1024

# --- built-in "Standard defaults" preset -----------------------------------
# Conventional starting values so a fresh chip isn't blank (customer
# request). BASE units (freq Hz, time ns, volt V, amp dimensionless).
# Grounded in run_build's own seeds + QM template conventions + the
# customer's explicit picks (x180 amp 0.25, DRAG α 1.0, depletion 10 µs).
# No frequencies / LO / FSP / grid / flux — those are chip-specific.
# CZ and CR fields coexist in `pairs`; the wizard's applyPreset filters by
# the chip's active gate (pairPopCols keep-set).
BUILTIN_SLUG = "builtin-standard"
_BUILTIN_NAME = "Standard defaults (built-in)"


def builtin_standard() -> dict:
    """The built-in preset payload (fresh dict each call — callers mutate)."""
    return {
        "version": 1,
        "slug": BUILTIN_SLUG,
        "name": _BUILTIN_NAME,
        "builtin": True,
        "created_at": None,
        "updated_at": None,
        "sections": {
            "pulses": {
                "defaults": {
                    "x180_length": 40,            # ns — the conventional π pulse
                    "x180_amplitude": 0.25,
                    "drag_alpha": 1.0,
                    "drag_detuning": 0,
                    "saturation_length": 10000,   # 10 µs
                    "saturation_amplitude": 0.1,  # ≈ −20 dBm at FSP 0
                },
                "overrides": {},
            },
            "qubit": {
                "defaults": {"anharmonicity": -200e6},
                "overrides": {},
            },
            "resonator": {
                "defaults": {
                    "readout_length": 1000,       # ns
                    "readout_amplitude": 0.1,     # ≈ −20 dBm at FSP 0
                    "depletion_time": 10000,      # 10 µs
                    "time_of_flight": 28,         # ns
                },
                "overrides": {},
            },
            "pairs": {
                "defaults": {
                    "cz_interaction_duration": 100,   # ns (run_build seed)
                    "cz_amplitude": 0.1,              # V (run_build seed)
                    "cr_drive_amplitude": 1.0,        # run_build seed
                    "cr_cancel_amplitude": 0.1,       # run_build seed
                },
                "overrides": {},
            },
        },
    }


def _builtin_summary() -> dict:
    p = builtin_standard()
    return {
        "slug": BUILTIN_SLUG,
        "name": _BUILTIN_NAME,
        "builtin": True,
        "created_at": None,
        "updated_at": None,
        "sections": {
            sec: {"defaults": len(body["defaults"]), "overrides": 0}
            for sec, body in p["sections"].items()
        },
    }


def slugify(name: str) -> str:
    """Filesystem-safe slug: lowercase, runs of non-alphanumerics → ``-``.

    Raises ``ValueError`` when nothing survives (the same intent as
    /mkdir's name sanitization — no separators, no dot-tricks, no NULs
    are constructible).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    slug = slug[:60].strip("-")
    if not slug:
        raise ValueError("preset name has no usable characters")
    return slug


def _presets_dir(instance_path) -> Path:
    return Path(instance_path) / _PRESETS_DIRNAME


def _scalar_ok(v) -> bool:
    return isinstance(v, (int, float, str, bool))


def validate_preset(name, sections) -> list:
    """Shape-check a save payload. Returns a list of error strings."""
    errors: list = []
    if not isinstance(name, str) or not name.strip():
        errors.append("name: required")
    elif len(name) > _MAX_NAME_LEN:
        errors.append(f"name: longer than {_MAX_NAME_LEN} characters")
    if not isinstance(sections, dict) or not sections:
        errors.append("sections: at least one section is required")
        return errors
    for sec, body in sections.items():
        allowed = SECTION_FIELDS.get(sec)
        if allowed is None:
            errors.append(f"sections.{sec}: unknown section")
            continue
        if not isinstance(body, dict):
            errors.append(f"sections.{sec}: must be an object")
            continue
        for extra in set(body) - {"defaults", "overrides"}:
            errors.append(f"sections.{sec}.{extra}: unknown key")
        defaults = body.get("defaults") or {}
        overrides = body.get("overrides") or {}
        if not isinstance(defaults, dict) or not isinstance(overrides, dict):
            errors.append(f"sections.{sec}: defaults/overrides must be objects")
            continue
        for f, v in defaults.items():
            if f not in allowed:
                errors.append(f"sections.{sec}.defaults.{f}: unknown field")
            elif not _scalar_ok(v):
                errors.append(f"sections.{sec}.defaults.{f}: value must be scalar")
        if len(overrides) > _MAX_OVERRIDE_ROWS:
            errors.append(f"sections.{sec}.overrides: more than "
                          f"{_MAX_OVERRIDE_ROWS} rows")
            continue
        for rid, row in overrides.items():
            if not isinstance(row, dict):
                errors.append(f"sections.{sec}.overrides.{rid}: must be an object")
                continue
            for f, v in row.items():
                if f not in allowed:
                    errors.append(
                        f"sections.{sec}.overrides.{rid}.{f}: unknown field")
                elif not _scalar_ok(v):
                    errors.append(
                        f"sections.{sec}.overrides.{rid}.{f}: value must be scalar")
    return errors


def list_presets(instance_path) -> list:
    """Summaries of every stored preset (corrupt files flagged, never a 500).

    The built-in "Standard defaults" preset is always FIRST — a fresh
    install has a usable starting point before anything is saved.
    """
    out = [_builtin_summary()]
    d = _presets_dir(instance_path)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sections = data.get("sections") or {}
            out.append({
                "slug": p.stem,
                "name": data.get("name") or p.stem,
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "sections": {
                    sec: {
                        "defaults": len((body or {}).get("defaults") or {}),
                        "overrides": len((body or {}).get("overrides") or {}),
                    }
                    for sec, body in sections.items()
                },
            })
        except (OSError, ValueError):
            logger.warning("gen_presets: unreadable preset file %s", p)
            out.append({"slug": p.stem, "name": p.stem, "corrupt": True})
    return out


def load_preset(instance_path, slug):
    """The full preset dict, or None (missing / corrupt / bad slug)."""
    if slug == BUILTIN_SLUG:
        return builtin_standard()
    try:
        if slug != slugify(slug):
            return None
    except ValueError:
        return None
    p = _presets_dir(instance_path) / f"{slug}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_preset(instance_path, name, sections, overwrite=False) -> dict:
    """Persist a preset; returns its summary. Raises ``ValueError`` on a
    validation failure and ``FileExistsError`` when the slug exists and
    ``overwrite`` is false (the route turns that into a confirm round-trip).
    """
    errors = validate_preset(name, sections)
    if errors:
        raise ValueError("; ".join(errors))
    slug = slugify(name)
    if slug == BUILTIN_SLUG:
        raise ValueError(
            "that name is reserved for the built-in preset — pick another")
    payload_probe = json.dumps(sections)
    if len(payload_probe) > _MAX_SERIALIZED_BYTES:
        raise ValueError("preset too large (over 200 KB)")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _presets_lock:
        d = _presets_dir(instance_path)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{slug}.json"
        created_at = now
        if path.exists():
            if not overwrite:
                raise FileExistsError(slug)
            try:
                created_at = (
                    json.loads(path.read_text(encoding="utf-8")).get("created_at")
                    or now
                )
            except (OSError, ValueError):
                pass  # corrupt original — fresh timestamps
        safe_io.atomic_write_json(path, {
            "version": 1,
            "name": name.strip(),
            "created_at": created_at,
            "updated_at": now,
            "sections": sections,
        })
    return {"slug": slug, "name": name.strip()}


def delete_preset(instance_path, slug) -> bool:
    """Remove a preset; True when a file was deleted (idempotent).
    The built-in preset is not a file and can never be deleted."""
    if slug == BUILTIN_SLUG:
        return False
    try:
        if slug != slugify(slug):
            return False
    except ValueError:
        return False
    p = _presets_dir(instance_path) / f"{slug}.json"
    with _presets_lock:
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False
