"""A test process must never see a quam/qualibrate state path outside pytest's tmp."""
import os
import subprocess
import sys
from pathlib import Path


def _under(path, root):
    try:
        Path(os.path.realpath(path)).relative_to(os.path.realpath(root))
        return True
    except ValueError:
        return False


def test_quam_state_path_is_pinned_to_tmp(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    value = os.environ.get("QUAM_STATE_PATH")
    assert value, "QUAM_STATE_PATH must be set so a pathless save() cannot fall back to ~/.qualibrate"
    assert _under(value, base), value
    assert _under(os.environ["QUALIBRATE_CONFIG_FILE"], base)
    for name in ("QUALIBRATE_CONFIG_DIR", "QUALIBRATE_STATE_PATH"):
        assert name not in os.environ or _under(os.environ[name], base), name


def test_a_spawned_subprocess_inherits_the_pin(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    out = subprocess.run(
        [sys.executable, "-c", "import os;print(os.environ.get('QUAM_STATE_PATH',''))"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out and _under(out, base), out
