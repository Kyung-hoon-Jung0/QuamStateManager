"""Standard-library SM-side node builder; heavy libraries stay in the child.

D-7, D-15 pins and the D-9 live-file digest subset. The interpreter is supplied
by the caller after selection; this module does not discover or choose one.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from . import safe_io


def pinned_env(scratch, library_root, chip_copy, base=None):
    root = Path(scratch).resolve()
    env = dict(os.environ if base is None else base)
    env.update({
        "QUALIBRATE_CONFIG_FILE": str(root / "config.toml"),
        "QUAM_CONFIG_FILE": str(root / "config.toml"),
        "QUAM_STATE_PATH": str(Path(chip_copy).resolve()),
        "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1", "MPLCONFIGDIR": str(root / "mpl"),
        "MPLBACKEND": "Agg", "QDAC_DISABLE": "1",
        "PYTHONPATH": str(Path(library_root).resolve()), "PYTHONUTF8": "1",
    })
    return env


def prepare_scratch(scratch, chip_copy):
    """Write a standalone config, with no inherited project or database overlay."""
    root = Path(scratch).resolve()
    state = Path(chip_copy).resolve()
    if not state.is_relative_to(root):
        raise ValueError("chip copy must be under scratch")
    for path in (root, state, root / "tmp", root / "mpl", root / "logs", root / "storage"):
        path.mkdir(parents=True, exist_ok=True)
    quote = lambda p: json.dumps(Path(p).as_posix())
    (root / "config.toml").write_text(
        '[qualibrate]\nversion = 6\nproject = "nodesim"\n'
        f'log_folder = {quote(root / "logs")}\n'
        f'[qualibrate.storage]\nlocation = {quote(root / "storage")}\n'
        f'[quam]\nstate_path = {quote(state)}\n', encoding="utf-8")


def parse_output(stdout):
    verdicts = [line for line in stdout.splitlines() if line.startswith("GUARD FAIL ")]
    answers = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and {"node", "ok", "program_script", "replaced_hosts", "verdict", "errors"} <= value.keys():
            answers.append(value)
    if len(answers) != 1:
        raise ValueError("child did not return exactly one result JSON")
    result = answers[0]
    expected = [result["verdict"]] if str(result["verdict"]).startswith("GUARD FAIL ") else []
    if verdicts != expected or (verdicts and result["ok"]):
        raise ValueError("child verdict and JSON disagree")
    return result


def live_digest(folder):
    _, _, state, wiring = safe_io.read_state_wiring_raw(folder)
    return {"state.json": hashlib.sha256(state).hexdigest(),
            "wiring.json": hashlib.sha256(wiring).hexdigest()}


def build_node(interpreter, node_file, library_root, chip_copy, scratch,
               overrides=None, live_folder=None, timeout=60):
    if live_folder:
        live = Path(live_folder).resolve()
        root = Path(scratch).resolve()
        if live.is_relative_to(root) or root.is_relative_to(live):
            raise ValueError("live folder and scratch must not overlap")
    prepare_scratch(scratch, chip_copy)
    before = live_digest(live_folder) if live_folder else None
    job = dict(node_file=str(Path(node_file).resolve()), library_root=str(Path(library_root).resolve()),
               chip_copy=str(Path(chip_copy).resolve()), scratch=str(Path(scratch).resolve()),
               overrides=overrides or {})
    script = Path(__file__).resolve().parents[1] / "generator" / "nodesim_build.py"
    process = None
    try:
        process = subprocess.Popen([str(interpreter), "-B", str(script)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", cwd=scratch,
                                   env=pinned_env(scratch, library_root, chip_copy))
        stdout, stderr = process.communicate(json.dumps(job), timeout=timeout)
        result = parse_output(stdout)
        if process.returncode and result["ok"]:
            raise RuntimeError("child exited unsuccessfully after a success result")
        result["stderr"] = stderr
        return result
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        if live_folder:
            after = live_digest(live_folder)
            if before != after:
                raise RuntimeError("live state changed during node build (writer unattributed)")
