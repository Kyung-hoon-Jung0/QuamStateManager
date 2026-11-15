"""Discover the environment's package or check frozen class bytes in a copy."""
from __future__ import annotations

import argparse
import base64
import importlib
import json
import site
import sys
from pathlib import Path


def run(req):
    if req.get("action") == "discover":
        package = importlib.import_module("quam_config")
        return {"ok": True, "folder": str(Path(package.__file__).resolve().parent),
                "site_packages": site.getsitepackages() + [site.getusersitepackages()]}
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
        root_class = req.get("root_class") or "quam_config.Quam"
        module, name = root_class.rsplit(".", 1)
        cls = getattr(importlib.import_module(module), name)
        cls.load(req["chip"])
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
