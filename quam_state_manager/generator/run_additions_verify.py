"""Verify candidate chip folders in the selected env (Generate-Config additions).

Runs inside the QM env; NEVER imported by ``quam_state_manager``. For each
folder: load the machine fresh through the class its ``state.json`` names
(no fallback root -- a fallback would hide the very failure being checked),
call ``generate_config()``, and for each listed dot path call that object's
own ``calculate_waveform()`` (a lab class checks its values in its own code).

Nothing is saved: the script only reads.

Spec (``--spec``)::

    {"folders": {"<key>": {"folder": "<abs path>", "own_code": ["qubits.q1.z.operations.p"]}}}

Result (``--out``)::

    {"status": "ok", "folders": {"<key>": {"ok", "stage", "error"}}}
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import traceback
from pathlib import Path


def _node_at(root, path: str):
    node = root
    for seg in path.split("."):
        try:
            node = node[seg]
        except (KeyError, TypeError, IndexError):
            node = getattr(node, seg)
    return node


def check(folder: str, own_code: list) -> dict:
    os.environ["QUAM_STATE_PATH"] = folder
    stage = "load"
    try:
        state = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
        mod, _, name = str(state.get("__class__") or "").rpartition(".")
        root_cls = getattr(importlib.import_module(mod), name)
        machine = root_cls.load(folder)
        stage = "generate_config"
        machine.generate_config()
        for path in own_code or []:
            stage = f"own code of {path}"
            obj = _node_at(machine, path)
            fn = getattr(obj, "calculate_waveform", None)
            if callable(fn) and fn() is None:
                return {"ok": False, "stage": stage,
                        "error": "its calculate_waveform() returned None"}
        return {"ok": True, "stage": "done", "error": None}
    except BaseException as exc:  # noqa: BLE001 -- reported, never raised
        msg = str(exc).strip().splitlines()
        return {"ok": False, "stage": stage,
                "error": f"{stage}: {type(exc).__name__}: {(msg[0] if msg else '')[:300]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = {"status": "error", "mode": "additions_verify", "error": None, "folders": {}}
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        for key, item in (spec.get("folders") or {}).items():
            result["folders"][key] = check(item["folder"], item.get("own_code") or [])
        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    Path(args.out).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": result["status"], "result_file": args.out}))
    sys.stdout.flush()
    os._exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
