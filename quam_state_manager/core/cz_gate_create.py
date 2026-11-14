"""May this new CZ gate be created with this check result? (pure, one rule)

:func:`may_create` is the one place that decides it; the create route asks
it for every pair. ``result`` is a :func:`core.gate_check.check_gates`
result, ``entry`` that result's entry for the gate.

- the lab's code ran the gate and no check failed: allowed, ``verified``;
- any check of the gate failed (also "the write would be refused", and the
  chip that loads without the write but not with it): refused;
- the gate could not be checked (no environment, a timeout, a crash, the lab
  environment busy, the chip as it is does not load or build its config,
  the lab's code could not tell what the gate plays): allowed only for a
  class whose structure SM knows (``cz_gate_build.GATE_CLASSES``) with the
  ``known`` layout, as ``not checked`` with the reason kept for the record;
  a ``copied`` layout is SM's proposal and must have passed loading and
  running the gate, so it is refused.

Returns ``(allowed, state, reason)``: ``state`` is ``verified`` or
``not checked``; ``reason`` is a plain sentence (empty for verified).

Pure: no I/O, no QM-stack import.
"""
from __future__ import annotations

from typing import Any

from quam_state_manager.core.cz_gate_build import GATE_CLASSES

VERIFIED = "verified"
NOT_CHECKED = "not checked"

_COULD_NOT_RUN = {
    "no-env": "No Python environment is selected, so the lab's code did not check this gate.",
    "timeout": "The lab's code did not answer in time, so this gate was not checked.",
    "crash": "The lab check stopped with an error, so this gate was not checked.",
    "busy": "The lab environment was busy with another run, so this gate was not checked.",
}

COPIED_NEEDS_CHECK = ("This gate copies the layout of a gate SM does not know, so the "
                      "lab's code must load the chip and run the gate before it is "
                      "written. {why}")


def _first_fail(entry: dict) -> str | None:
    for c in (entry.get("checks") or {}).values():
        if isinstance(c, dict) and c.get("status") == "fail":
            return str(c.get("message") or "A check failed.")
    return None


def _why_not_checked(result: dict, entry: dict | None) -> str:
    status = (result or {}).get("status")
    if status in _COULD_NOT_RUN:
        text = _COULD_NOT_RUN[status]
        err = (result or {}).get("error")
        return f"{text} The reason: {err}." if err and status != "no-env" else text
    if (result or {}).get("chip_error"):
        return ("The chip as it is does not load or build its config with the lab's "
                f"code, so this gate was not checked. The lab's error: {result['chip_error']}")
    if entry:
        ops = (entry.get("checks") or {}).get("ops") or {}
        if ops.get("message"):
            return str(ops["message"])
    return (result or {}).get("why_not_checked") or "This gate was not checked with the lab's code."


def may_create(result: dict | None, entry: dict | None, *, gate_class: Any,
               layout: Any) -> tuple[bool, str, str]:
    """``(allowed, state, reason)`` (see the module docstring)."""
    result = result or {}
    known = layout == "known" and gate_class in GATE_CLASSES
    if entry is not None:
        bad = _first_fail(entry)
        if bad:
            return False, NOT_CHECKED, bad
    if result.get("status") == "invalid-create":
        return False, NOT_CHECKED, str(result.get("error") or "The write would be refused.")
    ran = bool(entry and entry.get("ran"))
    if result.get("status") == "ok" and result.get("checked") and ran:
        return True, VERIFIED, ""
    why = _why_not_checked(result, entry)
    if known:
        return True, NOT_CHECKED, why
    return False, NOT_CHECKED, COPIED_NEEDS_CHECK.format(why=why)
