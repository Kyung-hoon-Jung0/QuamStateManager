"""Golden parity tests: numpy synth vs the real quam library.

``tests/golden/waveform_golden.json`` is produced by
``quam_state_manager/generator/run_waveform_golden.py`` running in the
user's QM-stack env (conda ``LabC``). Regenerate from WSL with::

    <qm-env>/python \
        quam_state_manager/generator/run_waveform_golden.py \
        --out 'D:\\work\\state-manager\\tests\\golden'

The comparison runs on every test invocation (golden file is committed);
the live-regeneration test additionally runs the dump script when the LabC
interpreter is present, catching quam version drift.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.pulse_catalog import PULSE_CATALOG, inferred_length
from quam_state_manager.core.waveform_synth import synthesize, synthesize_raw

sys.path.insert(0, str(Path(__file__).parent))
from waveform_matrix import CASES  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden" / "waveform_golden.json"
LabC_PYTHON = Path("<qm-env>/python")

RTOL = 1e-9
ATOL = 1e-12

pytestmark = pytest.mark.skipif(
    not GOLDEN_PATH.exists(),
    reason="tests/golden/waveform_golden.json not generated yet",
)


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _materialized_params(case: dict, golden_entry: dict) -> dict:
    """The params synthesize_raw needs: matrix params + concrete length."""
    params = dict(case["params"])
    if "length" not in params and golden_entry.get("length") is not None:
        params["length"] = golden_entry["length"]
    return params


def _ids():
    return [case["id"] for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=_ids())
def test_golden_parity(case, golden):
    entry = golden["cases"].get(case["id"])
    assert entry is not None, f"case {case['id']} missing from golden file"

    if case["raises"]:
        assert entry["raised"], (
            f"{case['id']}: quam did not raise but the matrix expects it")
        params = _materialized_params(case, entry)
        with pytest.raises((ValueError, KeyError)):
            synthesize_raw(case["key"], params)
        return

    assert not entry["raised"], (
        f"{case['id']}: quam raised {entry.get('error_type')}: {entry.get('error')}")

    params = _materialized_params(case, entry)
    ours = synthesize_raw(case["key"], params)
    theirs = entry["waveform"]

    if theirs["kind"] == "constant":
        assert isinstance(ours, (int, float, complex)) and not isinstance(ours, bool), (
            f"{case['id']}: quam returned a constant, synth returned {type(ours)}")
        ours_c = complex(ours)
        assert np.isclose(ours_c.real, theirs["re"], rtol=RTOL, atol=ATOL)
        expected_imag = theirs["im"] if theirs["im"] is not None else 0.0
        if theirs["im"] is None:
            assert not isinstance(ours, complex), (
                f"{case['id']}: quam returned real, synth returned complex")
        assert np.isclose(ours_c.imag, expected_imag, rtol=RTOL, atol=ATOL)
        return

    ours_arr = np.asarray(ours)
    re_expected = np.asarray(theirs["re"], dtype=float)
    assert len(ours_arr) == len(re_expected), (
        f"{case['id']}: length mismatch synth={len(ours_arr)}"
        f" quam={len(re_expected)}")

    if theirs["im"] is not None:
        assert np.iscomplexobj(ours_arr), (
            f"{case['id']}: quam returned complex, synth returned real")
        np.testing.assert_allclose(ours_arr.real, re_expected,
                                   rtol=RTOL, atol=ATOL, err_msg=case["id"])
        np.testing.assert_allclose(
            ours_arr.imag, np.asarray(theirs["im"], dtype=float),
            rtol=RTOL, atol=ATOL, err_msg=case["id"])
    else:
        assert not np.iscomplexobj(ours_arr), (
            f"{case['id']}: quam returned real, synth returned complex")
        np.testing.assert_allclose(ours_arr.astype(float), re_expected,
                                   rtol=RTOL, atol=ATOL, err_msg=case["id"])


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if not c["raises"]
     and PULSE_CATALOG[c["key"]].length_mode == "inferred"],
    ids=lambda c: c["id"],
)
def test_inferred_length_matches_quam(case, golden):
    """pulse_catalog.inferred_length must equal quam's runtime property."""
    entry = golden["cases"][case["id"]]
    assert inferred_length(case["key"], dict(case["params"])) == entry["length"]


def test_snz_derived_properties(golden):
    """Pin the SNZ t_phi / B decomposition against quam."""
    for case in CASES:
        if case["key"] != "SNZPulse" or case["raises"]:
            continue
        derived = golden["cases"][case["id"]].get("derived")
        assert derived is not None
        t_phi_eff = float(case["params"].get("t_phi_eff", 0.0))
        t_phi = int(np.floor(t_phi_eff / 2.0)) * 2
        assert derived["t_phi"] == t_phi, case["id"]
        assert np.isclose(derived["b_over_a_ratio"],
                          1.0 - (t_phi_eff - t_phi) / 2.0), case["id"]


@pytest.mark.parametrize("case", [c for c in CASES if not c["raises"]], ids=lambda c: c["id"])
def test_payload_layer_matches_raw(case, golden):
    """synthesize() payload must carry the same samples as synthesize_raw."""
    entry = golden["cases"][case["id"]]
    params = _materialized_params(case, entry)
    payload = synthesize(case["key"], params)
    assert payload["ok"], f"{case['id']}: {payload['error']} {payload['param_errors']}"

    raw = synthesize_raw(case["key"], params)
    if isinstance(raw, (int, float, complex)) and not isinstance(raw, bool):
        assert payload["kind"] == "constant"
        assert len(payload["i"]) == payload["length"]
        value = complex(raw)
        assert np.allclose(payload["i"], value.real)
        if payload["q"] is not None:
            assert np.allclose(payload["q"], value.imag)
    else:
        arr = np.asarray(raw)
        np.testing.assert_allclose(payload["i"], arr.real if np.iscomplexobj(arr)
                                   else arr.astype(float), rtol=RTOL, atol=ATOL)
        if np.iscomplexobj(arr):
            np.testing.assert_allclose(payload["q"], arr.imag, rtol=RTOL, atol=ATOL)
        else:
            assert payload["q"] is None


@pytest.mark.skipif(not LabC_PYTHON.exists(),
                    reason="LabC env interpreter not available")
def test_live_regeneration_matches_committed(tmp_path):
    """Re-run the dump script in the LabC env; fresh output must equal the
    committed golden (catches quam/qualang_tools version drift)."""
    script = (Path(__file__).parents[1]
              / "quam_state_manager" / "generator" / "run_waveform_golden.py")

    def _win(path: Path) -> str:
        return subprocess.run(["wslpath", "-w", str(path)], capture_output=True,
                              text=True, check=True).stdout.strip()

    proc = subprocess.run(
        [str(LabC_PYTHON), _win(script), "--out", _win(tmp_path)],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr

    fresh = json.loads((tmp_path / "waveform_golden.json").read_text(encoding="utf-8"))
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if fresh["versions"] != committed["versions"]:
        pytest.skip(f"version drift: {fresh['versions']} vs {committed['versions']}"
                    " — regenerate the golden file")
    for case_id, fresh_entry in fresh["cases"].items():
        committed_entry = committed["cases"].get(case_id)
        assert committed_entry is not None, f"{case_id} missing from committed golden"
        assert fresh_entry.get("raised") == committed_entry.get("raised"), case_id
        if fresh_entry.get("raised"):
            continue
        fw, cw = fresh_entry["waveform"], committed_entry["waveform"]
        assert fw["kind"] == cw["kind"], case_id
        np.testing.assert_allclose(np.atleast_1d(fw["re"]), np.atleast_1d(cw["re"]),
                                   rtol=0, atol=0, err_msg=case_id)
