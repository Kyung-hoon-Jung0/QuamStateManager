"""D-6 oracle driver (offline test aid, never a click path).

Runs the REAL node in a child started with the click's own spawn pieces
(``nodesim_spawn.prepare_scratch`` / ``pinned_env`` / ``parse_output`` /
``check_library``; D-7 pins, D-8 guard) and compares its ``generate_qua_script``
program text with the stub-built one. A decoy "live" folder is hashed file by
file before and after; any change is a hard failure.
"""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

from .nodesim_spawn import check_library, parse_output, pinned_env, prepare_scratch

ORACLE_ENV = "SM_NODESIM_ORACLE"
CHILD = Path(__file__).resolve().parents[1] / "generator" / "nodesim_oracle.py"


def oracle_gate(environ):
    """None when the oracle may run, else the skip reason (D-6: opt-in only)."""
    if environ.get(ORACLE_ENV) != "1":
        return f"D-6 oracle runs only with {ORACLE_ENV}=1"
    if not environ.get("SM_NODESIM_LAB_PY") or not environ.get("SM_NODESIM_LIB"):
        return "oracle requires both SM_NODESIM_LAB_PY and SM_NODESIM_LIB"
    return None


def folder_digest(folder):
    """sha256 of every file under a decoy folder (the decoy is ours, plain reads)."""
    root = Path(folder)
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def run_real_node(interpreter, node_file, library_root, chip_copy, scratch,
                  overrides=None, live_folder=None, timeout=180, script=None, mode="real"):
    """Oracle child; ``mode`` "real" runs the real node, "stub" runs
    ``nodesim_build.main`` unchanged. Returns the
    parsed result plus ``pid`` and the decoy digests.

    D-39: both sides get the builder's env (``pinned_env``: D-7, D-36, and the
    D-37 hash-seed pin) and the child checks it with the builder's
    ``verify_env``; the oracle adds no pin of its own."""
    if mode not in ("real", "stub"):
        raise ValueError(mode)
    root = Path(scratch).resolve()
    if live_folder:
        live = Path(live_folder).resolve()
        if live.is_relative_to(root) or root.is_relative_to(live):
            raise ValueError("live folder and scratch must not overlap")
    prepare_scratch(scratch, chip_copy)
    before = folder_digest(live_folder) if live_folder else None
    job = dict(node_file=str(Path(node_file).resolve()), library_root=str(Path(library_root).resolve()),
               chip_copy=str(Path(chip_copy).resolve()), scratch=str(root), overrides=overrides or {},
               mode=mode)
    process = None
    result = None
    try:
        process = subprocess.Popen([str(interpreter), "-B", str(script or CHILD)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", cwd=str(root),
                                   env=pinned_env(root, library_root, chip_copy))
        stdout, stderr = process.communicate(json.dumps(job), timeout=timeout)
        result = parse_output(stdout)
        if process.returncode and result["ok"]:
            raise RuntimeError("child exited unsuccessfully after a success result")
        if result["ok"]:
            check_library(result, library_root)
        result["stderr"] = stderr
        result["pid"] = process.pid
        return result
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        if live_folder:
            after = folder_digest(live_folder)
            if result is not None:
                result["live_before"], result["live_after"] = before, after
            if before != after:
                raise RuntimeError("decoy live folder changed during the oracle run")


# The only line generate_qua_script varies between two builds of the same program.
STAMP = "# Single QUA script generated at "


def normalize(script):
    return "\n".join(line for line in script.splitlines() if not line.startswith(STAMP))


def compare(stub_script, real_script, lines=40):
    """identical / differs (generation stamp line dropped) with the first
    ``lines`` lines of a unified diff."""
    stub_script, real_script = normalize(stub_script), normalize(real_script)
    if stub_script == real_script:
        return {"verdict": "identical", "diff": []}
    diff = list(difflib.unified_diff(stub_script.splitlines(), real_script.splitlines(),
                                     "stub", "real", lineterm=""))
    return {"verdict": "differs", "diff": diff[:lines], "diff_lines": len(diff)}
