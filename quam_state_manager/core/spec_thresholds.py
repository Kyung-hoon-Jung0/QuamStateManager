"""The lab's spec thresholds, on disk instead of in one person's browser.

THE SYMPTOM this exists for, measured on a real 20-qubit chip:

    QUBITS IN SPEC   0/20   (16 warn · 4 fail)

which does not mean the chip is broken. It means the DEFAULT thresholds are not
this lab's — and the page did not say so, so the number read as a verdict about
the device rather than about a comparison nobody had configured.

Two halves, and the first is the important one:

1. **The numbers say whose they are.** A threshold that came from SM's own seed
   defaults is labelled as SM's, everywhere it produces a verdict. SM has no
   idea what a good T1 is on somebody else's device; saying so costs nothing
   and turns a wrong-looking number into a question the lab can answer.

2. **One spec per installation, not one per browser.** It lived in
   ``localStorage['quam_chip_thresholds']``, so five people had five
   definitions of "in spec" and clearing a cache erased one. It now lives in
   ``instance/spec_thresholds.json`` — the same sidecar discipline as
   ``type_assignments`` and ``project_dataset_roots``.

DELIBERATELY NOT PER-CHIP. A design review cut that: it would add a second
place to look when the numbers surprise somebody, for a symptom caused by
labelling alone. A lab that genuinely needs different bands for two devices can
say so and get a per-chip layer built on this one — with the layering shown on
screen, which is the part that would make it safe.
"""

from __future__ import annotations

import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from quam_state_manager.core import chip_health, safe_io

logger = logging.getLogger(__name__)

_FILENAME = "spec_thresholds.json"
_lock = threading.Lock()

# Only these two move. `direction` and `label` come from the metric's own
# definition and are not a lab preference — a lab that flipped `direction`
# would be renaming the metric, not re-specifying it.
_BOUNDS = ("warn", "fail")


def spec_path(instance_path) -> Path:
    return Path(instance_path) / _FILENAME


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("corrupt spec-threshold sidecar %s — using defaults", path)
        return {}
    saved = data.get("thresholds") if isinstance(data, dict) else None
    return saved if isinstance(saved, dict) else {}


def resolve(instance_path) -> dict[str, Any]:
    """``{"metrics": {...}, "edited": [...], "source": ..., "summary": ...}``.

    ``metrics`` is always a DEEP COPY of the defaults with the lab's overrides
    applied on top: never the module global, because ``DEFAULT_THRESHOLDS`` is
    shared with ``make_record``, ``report_card`` and the template context, and
    one caller mutating a returned dict would silently re-spec the whole app.

    ``source`` is ``"default"`` when nothing has been set, ``"lab"`` when
    everything a verdict uses came from the file, and ``"mixed"`` in between —
    which is the honest answer and the one the tile needs, because "some of
    these bands are ours and some are SM's" is a real state.
    """
    metrics = deepcopy(chip_health.DEFAULT_THRESHOLDS)
    saved = _read(spec_path(instance_path))
    edited: list[str] = []
    for key, override in saved.items():
        if key not in metrics or not isinstance(override, dict):
            continue
        touched = False
        for bound in _BOUNDS:
            value = override.get(bound)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics[key][bound] = float(value)
                touched = True
        if touched:
            edited.append(key)
    edited.sort()

    if not edited:
        source, summary = "default", "SM's own default bands — not your lab's yet"
    elif len(edited) == len(metrics):
        source, summary = "lab", "your lab's bands"
    else:
        source = "mixed"
        summary = (f"your lab's bands for {len(edited)} of {len(metrics)} metrics; "
                   "the rest are SM's defaults")
    return {"metrics": metrics, "edited": edited, "source": source,
            "summary": summary}


def save(instance_path, metrics: dict[str, Any]) -> dict[str, Any]:
    """Persist the bands that DIFFER from the defaults, and only those.

    Storing a full copy would freeze today's defaults into the file, so a later
    correction to a seed value would never reach a lab that had once pressed
    Apply — the bug where "we use the defaults" quietly means "we use the
    defaults as they were in August".
    """
    out: dict[str, dict[str, float]] = {}
    for key, band in (metrics or {}).items():
        base = chip_health.DEFAULT_THRESHOLDS.get(key)
        if not base or not isinstance(band, dict):
            continue
        diff: dict[str, float] = {}
        for bound in _BOUNDS:
            value = band.get(bound)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if abs(float(value) - float(base[bound])) > 1e-12 * max(1.0, abs(float(base[bound]))):
                diff[bound] = float(value)
        if diff:
            out[key] = diff

    path = spec_path(instance_path)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, {"version": 1, "thresholds": out})
    return resolve(instance_path)


def clear(instance_path) -> dict[str, Any]:
    path = spec_path(instance_path)
    with _lock:
        if path.exists():
            safe_io.atomic_write_json(path, {"version": 1, "thresholds": {}})
    return resolve(instance_path)
