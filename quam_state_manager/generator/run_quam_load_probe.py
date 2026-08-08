"""Standalone QM-stack subprocess: can THIS env's quam load THIS run's state?

The env × archive-generation probe behind ``core/autofit/envmatrix.py``
(docs/78 D-13): archives span quam generations (a 0.6.0 env refuses a 0.5-era
``quam_state`` with a ``duration_control``→``duration_qubit`` rename or unknown
optional attributes), and the honest answer is a *classified* verdict — never a
raw traceback surfaced to the user, and never a guess made without spawning the
interpreter that would actually do the loading.

stdlib-only at import (the repo's generator-script contract); quam is imported
inside ``main`` so a QM-less interpreter still produces a classified envelope.

Usage:
    python run_quam_load_probe.py --state <quam_state dir> [--source-root <dir>]
Emits ONE JSON line to stdout (schema ``quamload/v1``):
    {"schema": "quamload/v1", "ok": bool, "stage": "import"|"load"|None,
     "exc_type": str|None, "detail": str|None, "lib_versions": {...}}
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback


def _lib_versions() -> dict:
    out = {}
    for m in ("quam", "qm", "qualibrate", "numpy", "xarray"):
        try:
            out[m] = getattr(importlib.import_module(m), "__version__", None)
        except Exception:  # noqa: BLE001 — a broken package must not kill the probe
            out[m] = None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, help="quam_state directory to load")
    ap.add_argument("--source-root", default="",
                    help="dir holding quam_config/ (prepended to sys.path)")
    args = ap.parse_args()

    if args.source_root:
        sys.path.insert(0, args.source_root)

    result = {"schema": "quamload/v1", "ok": False, "stage": None,
              "exc_type": None, "detail": None, "missing_module": None,
              "lib_versions": {}}

    try:
        from quam_config import Quam
    except Exception as e:  # noqa: BLE001
        # Report the MISSING MODULE explicitly. The traceback always renders the
        # source line `from quam_config import Quam`, so substring-sniffing it
        # classifies every import failure as "no quam_config" — including a
        # missing `quam`. The driver must classify on this field, not the text.
        result.update(stage="import", exc_type=type(e).__name__,
                      missing_module=getattr(e, "name", None),
                      detail=traceback.format_exc()[-1500:])
        result["lib_versions"] = _lib_versions()
        print(json.dumps(result))
        return 2

    result["lib_versions"] = _lib_versions()
    try:
        Quam.load(args.state)
        result["ok"] = True
    except Exception as e:  # noqa: BLE001
        result.update(stage="load", exc_type=type(e).__name__,
                      detail=traceback.format_exc()[-1500:])
    print(json.dumps(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
