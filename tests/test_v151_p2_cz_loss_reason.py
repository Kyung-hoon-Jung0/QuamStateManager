"""v1.5.1 T34-F3: a legacy population-loss run without a frequency coordinate is
said as such, not as "ValueError".

``cz_loss._read_h5`` raises a precise reason for a readable file that lacks
what the plan needs ("no frequency coordinate"), but ``read_run`` replaced it
with the exception's class: "ds_fit.h5 cannot be read (ValueError)". The
reason is kept now; a file that truly cannot be read still says so.
"""

from __future__ import annotations

import h5py
import numpy as np

from quam_state_manager.core import cz_loss as L
from tests.test_cz_loss import _age, _fresh_caches, chip_qubits, pl_run, sweep  # noqa: F401 -- fixture


def _strip(folder, key):
    p = folder / "ds_fit.h5"
    with h5py.File(p, "r") as f:
        kept = {k: f[k][()] for k in f if k != key}
    p.unlink()
    with h5py.File(p, "w") as f:
        for k, v in kept.items():
            f[k] = v if not (isinstance(v, np.ndarray) and v.dtype.kind == "O") else v.astype(h5py.string_dtype())
    _age(folder)


def test_a_missing_frequency_coordinate_is_named(tmp_path):
    root = tmp_path / "data"
    legacy = pl_run(root, 2286, {"q1": sweep(4.5e9)})
    _strip(legacy, "frequency")
    no_loss = pl_run(root, 2287, {"q1": sweep(4.5e9)})
    _strip(no_loss, "p_loss")
    out = L.build([root], chip_qubits())
    reasons = {s["run_id"]: s["reason"] for s in out["skipped"]}
    assert reasons.get(2286) == "no frequency coordinate in ds_fit.h5", reasons
    assert reasons.get(2287) == "no p_loss for any qubit in ds_fit.h5", reasons
    assert "ValueError" not in " ".join(reasons.values())


def test_a_file_that_cannot_be_read_still_says_so(tmp_path):
    root = tmp_path / "data"
    broken = pl_run(root, 72, {"q1": sweep(4.5e9)})
    (broken / "ds_fit.h5").write_bytes((broken / "ds_fit.h5").read_bytes()[:200])
    _age(broken)
    assert L.read_run(broken)["reason"].startswith("ds_fit.h5 cannot be read (")
