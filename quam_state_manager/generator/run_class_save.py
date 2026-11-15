"""Discover the environment's package or check frozen class bytes in a copy."""
from __future__ import annotations

import argparse
import base64
import importlib
import json
import site
import sys
from pathlib import Path


def _load_chip(req):
    """Load the chip copy with its root class; the error text when it fails."""
    root_class = req.get("root_class") or "quam_config.Quam"
    module, name = root_class.rsplit(".", 1)
    try:
        cls = getattr(importlib.import_module(module), name)
        cls.load(req["chip"])
    except BaseException as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def run(req):
    if req.get("action") == "discover":
        package = importlib.import_module("quam_config")
        return {"ok": True, "folder": str(Path(package.__file__).resolve().parent),
                "site_packages": site.getsitepackages() + [site.getusersitepackages()]}
    if req.get("action") == "load":
        # The copy as it is, before the draft is written into it.
        sys.path.insert(0, str(Path(req["copy_root"])))
        error = _load_chip(req)
        return {"ok": error is None, "chip_loaded": error is None, "error": error}
    from run_pulse_draft import run as preview
    root = Path(req["copy_root"])
    source = base64.b64decode(req["source"], validate=True)
    draft = root / "quam_config" / req["filename"]
    draft.write_bytes(source)
    sys.path.insert(0, str(root))
    # Import the public module too: package imports and relative imports matter.
    mod = importlib.import_module("quam_config." + draft.stem)
    getattr(mod, req["class_name"])
    result = preview({"draft": str(draft), "class_name": req["class_name"]})
    if result.get("ok") and not any(f["name"] == "amplitude" for f in result["fields"]):
        result.update(ok=False, error="The pulse class needs an amplitude field.")
    if result.get("ok") and req.get("chip"):
        error = _load_chip(req)
        if error:
            result.update(ok=False, chip_error=error,
                          error="The chip copy does not load with the draft class: " + error)
        else:
            result["chip_loaded"] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        result = run(json.loads(Path(args.inp).read_text(encoding="utf-8")))
    except BaseException as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(args.out).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
