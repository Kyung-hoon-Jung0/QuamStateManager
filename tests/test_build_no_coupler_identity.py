"""A chip without couplers builds byte for byte as before the coupler-delay
change: the base commit's run_build.py (exported with ``git archive``) and this
tree's run_build.py build the same no-coupler spec in the same QM env, and
state.json and wiring.json must be equal byte for byte.

The same comparison as a recorded run (sha256 with the base env's python):
    <study folder>/byte_identity/make_main_fixture.py 6d6f00d7
Skips without a usable QM env, git, or the base commit.
"""

import copy
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from quam_state_manager.core.config_generator import run_generator

BASE = "6d6f00d7"
_ROOT = Path(__file__).resolve().parent.parent


def no_coupler_spec():
    from tests.test_script_emitter_live import _spec
    spec = copy.deepcopy(_spec())
    spec["pair_gate"] = "cz_fixed"
    spec["lines"] = [ln for ln in spec["lines"] if ln["line"] != "coupler"]
    # one MW latency class: a chip mixing band 2 with bands 1 / 3 now gets
    # its earlier MW outputs delayed (test_mw_band_align_live.py)
    for group in ("qubit", "resonator"):
        for row in spec["populate"][group].values():
            row["band"] = 2
    return spec


def test_no_coupler_chip_is_byte_identical_to_the_base_build(tmp_path):
    from tests.test_script_emitter_live import _first_usable_qm_env
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    arch = subprocess.run(["git", "archive", BASE, "quam_state_manager/generator"],
                          capture_output=True, cwd=str(_ROOT))
    if arch.returncode != 0:
        pytest.skip("base commit not in this clone")
    base_dir = tmp_path / "base"
    with tarfile.open(fileobj=io.BytesIO(arch.stdout)) as tf:
        tf.extractall(base_dir)

    spec_path = tmp_path / "_spec.json"
    spec_path.write_text(json.dumps(no_coupler_spec(), indent=2), encoding="utf-8")
    base_out = tmp_path / "base_out"
    r = subprocess.run([python, str(base_dir / "quam_state_manager" / "generator" / "run_build.py"),
                        "--mode", "build", "--spec", str(spec_path), "--out", str(base_out)],
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]

    out = tmp_path / "built"
    outcome = run_generator(python, "build", no_coupler_spec(), out, timeout=600)
    assert outcome.get("ok"), outcome
    for name in ("state.json", "wiring.json"):
        assert (out / name).read_bytes() == (base_out / name).read_bytes(), \
            f"{name} differs from the base build"
