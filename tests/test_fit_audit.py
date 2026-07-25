"""Fit-Auditor (gate-migration triage, docs/50) — driver + engine unit tests,
plus a §6-③ anchor integration test (auto-skipped without the LabB venv + archive).
"""
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import fit_audit as FA
from quam_state_manager.generator import run_fit_audit as ENG


# ---------------------------------------------------------------------------
# family registry / name derivation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("node_name,expected", [
    ("1Q_05_resonator_spectroscopy_vs_power", "resonator_spectroscopy_vs_power"),
    ("05b_resonator_spectroscopy_vs_power", "resonator_spectroscopy_vs_power"),
    # the REAL LabB/LabA resonator node names carry an _iq suffix — must alias,
    # else the whole second pilot family silently drops from the backlog.
    ("05b_resonator_spectroscopy_vs_power_iq", "resonator_spectroscopy_vs_power"),
    ("1Q_05b_resonator_spectroscopy_vs_power_iq", "resonator_spectroscopy_vs_power"),
    ("1Q_08_qubit_spectroscopy", "qubit_spectroscopy"),
    ("1Q_08_qubit_spectroscopy_new", "qubit_spectroscopy"),   # alias
    ("08_qubit_spectroscopy_new", "qubit_spectroscopy"),
])
def test_family_for_registered(node_name, expected):
    assert FA.family_for(node_name) == expected


@pytest.mark.parametrize("node_name", [
    "2Q_24_Bell_State_Tomography", "1Q_09_qubit_spectroscopy_vs_flux",
    "1Q_28_Qubit_Spectroscopy_E_to_F", "", "random_thing",
])
def test_family_for_unregistered_is_none(node_name):
    assert FA.family_for(node_name) is None


# ---------------------------------------------------------------------------
# stored_claim normalizer
# ---------------------------------------------------------------------------

def _write_data_json(folder: Path, fit_results: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.json").write_text(json.dumps({"fit_results": fit_results}))


def test_stored_claim_success_dict(tmp_path):
    _write_data_json(tmp_path, {
        "qA1": {"success": True, "frequency": 5.1e9},
        "qA2": {"success": False, "frequency": float("nan")},
    })
    claim = FA.stored_claim(tmp_path, "frequency")
    assert claim["qA1"] == (True, 5.1e9)
    assert claim["qA2"][0] is False


def test_stored_claim_outcome_string(tmp_path):
    _write_data_json(tmp_path, {
        "qA1": {"outcome": "successful", "frequency": 7.0e9},
        "qA2": {"status": "failed", "frequency": 7.1e9},
    })
    claim = FA.stored_claim(tmp_path, "frequency")
    assert claim["qA1"] == (True, 7.0e9)
    assert claim["qA2"] == (False, 7.1e9)


def test_stored_claim_missing_success_is_none(tmp_path):
    _write_data_json(tmp_path, {"qA1": {"frequency": 5.0e9}})
    assert FA.stored_claim(tmp_path, "frequency")["qA1"][0] is None


def test_stored_claim_no_data_json(tmp_path):
    assert FA.stored_claim(tmp_path, "frequency") == {}


# ---------------------------------------------------------------------------
# verdict codifier — every class + the false-accept ledger invariant
# ---------------------------------------------------------------------------

VF, TOL = "frequency", 1e6


def _fresh(success, value, deterministic=True):
    return {"success": success, "frequency": value, "deterministic": deterministic}


def test_codify_reject():
    v, _ = FA._codify(True, 5.0e9, _fresh(False, None), VF, TOL)
    assert v == "reject"


def test_codify_recover():
    v, _ = FA._codify(False, None, _fresh(True, 5.0e9), VF, TOL)
    assert v == "recover"


def test_codify_drift_only_beyond_tol():
    assert FA._codify(True, 5.0e9, _fresh(True, 5.0e9 + 2e6), VF, TOL)[0] == "drift"
    assert FA._codify(True, 5.0e9, _fresh(True, 5.0e9 + 0.5e6), VF, TOL)[0] == "agrees"


def test_codify_agrees_both_reject():
    assert FA._codify(False, None, _fresh(False, None), VF, TOL)[0] == "agrees"


def test_codify_unverifiable_paths():
    assert FA._codify(True, 5e9, None, VF, TOL)[0] == "unverifiable"           # no fresh
    assert FA._codify(True, 5e9, _fresh(False, None, deterministic=False),
                      VF, TOL)[0] == "unverifiable"                            # non-det
    assert FA._codify(None, None, _fresh(True, 5e9), VF, TOL)[0] == "unverifiable"  # no claim


def test_false_accept_ledger_reject_never_downgrades():
    """A stored-True→fresh-False must ALWAYS codify to reject, never agrees —
    across a grid of values/tolerances (docs/50 false-accept ledger)."""
    for sval in (None, 5.0e9, 7.6e9, float("nan")):
        for fval in (None, 5.0e9, 0.0):
            v, _ = FA._codify(True, sval, _fresh(False, fval), VF, TOL)
            assert v == "reject", (sval, fval, v)


# ---------------------------------------------------------------------------
# WSL <-> Windows path translation
# ---------------------------------------------------------------------------

def test_to_win():
    assert FA._to_win("/mnt/d/work/x/y") == r"D:\work\x\y"
    assert FA._to_win("/mnt/c/Users/a b") == r"C:\Users\a b"
    assert FA._to_win("D:\\already\\win") == "D:\\already\\win"   # not /mnt → unchanged
    assert FA._to_win("/home/x") == "/home/x"                      # not a drive mount


def test_is_windows_interp():
    assert FA._is_windows_interp("C:\\x\\python.exe")
    assert FA._is_windows_interp("/mnt/c/x/python.exe")
    assert not FA._is_windows_interp("/usr/bin/python")
    assert not FA._is_windows_interp("/x/.venv/bin/python")


def test_pth_translates_only_for_windows_interp():
    assert FA._pth("C:\\x\\python.exe", "/mnt/d/a") == r"D:\a"
    assert FA._pth("/usr/bin/python", "/mnt/d/a") == "/mnt/d/a"


# ---------------------------------------------------------------------------
# digest aggregation
# ---------------------------------------------------------------------------

def test_digest_counts_and_flagged_sorting():
    summaries = [
        {"family": "qubit_spectroscopy", "family_label": "Qubit spectroscopy (f_01)",
         "gate_hash": "abc123def456", "run": "#1", "uid": "k:1",
         "counts": {"agrees": 3, "reject": 1, "recover": 0, "drift": 1, "unverifiable": 0},
         "rows": [
             {"qubit": "qA1", "verdict": "reject", "stored_success": True, "fresh_success": False,
              "stored_value": 5e9, "fresh_value": None, "detail": "x", "deterministic": True},
             {"qubit": "qA2", "verdict": "drift", "stored_success": True, "fresh_success": True,
              "stored_value": 5e9, "fresh_value": 5.1e9, "detail": "y", "deterministic": True},
             {"qubit": "qA3", "verdict": "agrees", "stored_success": True, "fresh_success": True,
              "stored_value": 5e9, "fresh_value": 5e9, "detail": "", "deterministic": True},
         ]},
    ]
    dg = FA._digest(summaries)
    fam = dg["families"]["qubit_spectroscopy"]
    assert fam["counts"]["reject"] == 1 and fam["counts"]["drift"] == 1
    assert fam["gate_hashes"] == ["abc123def456"[:12]]
    # only non-agree rows are flagged, reject sorts before drift
    verdicts = [r["verdict"] for r in dg["flagged"]]
    assert verdicts == ["reject", "drift"]
    assert dg["flagged"][0]["uid"] == "k:1"


# ---------------------------------------------------------------------------
# source-root setting round-trip
# ---------------------------------------------------------------------------

def test_validate_source_root(tmp_path):
    assert FA.validate_source_root("")[0] is True             # blank = env install
    assert FA.validate_source_root(str(tmp_path / "nope"))[0] is False
    (tmp_path / "empty").mkdir()
    assert FA.validate_source_root(str(tmp_path / "empty"))[0] is False  # no calibration_utils/
    (tmp_path / "sc" / "calibration_utils").mkdir(parents=True)
    ok, msg = FA.validate_source_root(str(tmp_path / "sc"))
    assert ok and "calibration_utils" in msg


def test_sweep_cancel_sets_cancelled_status():
    job = FA.SweepJob(total=3, env="/e", source_root=None)
    assert job.status == "running" and not job.cancelled
    job.cancel()
    assert job.cancelled
    job.finish()                                   # cancel set, no error -> cancelled
    assert job.snapshot()["status"] == "cancelled"


def test_sweep_finish_error_beats_cancel():
    job = FA.SweepJob(total=1, env="/e", source_root=None)
    job.cancel()
    job.finish(error="boom")                        # a real error still wins
    assert job.snapshot()["status"] == "error"


def test_source_root_setting_roundtrip(tmp_path):
    assert FA.get_audit_source_root(tmp_path) is None
    FA.set_audit_source_root(tmp_path, "/some/graph/superconducting")
    assert FA.get_audit_source_root(tmp_path) == "/some/graph/superconducting"
    # setting it must not clobber a co-resident selected_env key
    from quam_state_manager.core.config_generator import _settings_path, set_selected_env
    set_selected_env(tmp_path, "/env/python")
    FA.set_audit_source_root(tmp_path, "/other/root")
    data = json.loads(_settings_path(tmp_path).read_text())
    assert data["selected_env_python"] == "/env/python"
    assert data["fit_audit_source_root"] == "/other/root"


def test_set_selected_env_preserves_audit_source_root(tmp_path):
    """Re-picking a QM env must NOT wipe the audit source-root (shared settings)."""
    from quam_state_manager.core.config_generator import get_selected_env, set_selected_env
    FA.set_audit_source_root(tmp_path, "/graph/superconducting")
    set_selected_env(tmp_path, "/env/python")
    assert FA.get_audit_source_root(tmp_path) == "/graph/superconducting"
    assert get_selected_env(tmp_path) == "/env/python"


# ---------------------------------------------------------------------------
# engine helpers (run_fit_audit.py)
# ---------------------------------------------------------------------------

def test_gate_hash_reproducible_and_sensitive(tmp_path):
    class _M:  # stand-in module with a package dir
        pass
    pkg = tmp_path / "some_util"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "analysis.py").write_text("x = 1\n")
    m = _M()
    m.__file__ = str(pkg / "__init__.py")
    h1, files1 = ENG._gate_hash(m)
    h2, files2 = ENG._gate_hash(m)
    assert h1 == h2 and files1 == files2 == ["__init__.py", "analysis.py"]
    (pkg / "analysis.py").write_text("x = 2\n")   # touch a source
    h3, _ = ENG._gate_hash(m)
    assert h3 != h1


def test_sanitize_non_finite():
    out = ENG._sanitize({"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": 3.0}})
    assert out == {"a": None, "b": [1.0, None], "c": {"d": 3.0}}


def test_deterministic():
    a = {"success": True, "frequency": 5.0e9}
    assert ENG._deterministic(a, dict(a))
    assert not ENG._deterministic(a, {"success": False, "frequency": 5.0e9})
    assert not ENG._deterministic(a, {"success": True, "frequency": 5.0e9 + 100})
    # both-NaN on the same field is consistent, not a disagreement
    assert ENG._deterministic({"success": False, "frequency": float("nan")},
                              {"success": False, "frequency": float("nan")})


def test_params_shim_precedence():
    p = ENG._Params({"a": 1}, {"a": 99, "b": 2})
    assert p.a == 1 and p.b == 2
    with pytest.raises(AttributeError):
        _ = p.missing


# ---------------------------------------------------------------------------
# single-run verdict cache + apply-popup badge (Item 1)
# ---------------------------------------------------------------------------

def test_audit_run_cached_coalesces(tmp_path, monkeypatch):
    (tmp_path / "node.json").write_text("{}")
    (tmp_path / "ds_raw.h5").write_bytes(b"x")
    calls = {"n": 0}

    def fake(node_name, folder, env, source_root, timeout=180):
        calls["n"] += 1
        return {"auditable": True, "family": "qubit_spectroscopy", "errors": [],
                "rows": [{"qubit": "qA1", "verdict": "agrees"}]}

    monkeypatch.setattr(FA, "audit_run", fake)
    FA._VERDICT_CACHE.clear()
    r1 = FA.audit_run_cached("1Q_08_qubit_spectroscopy", str(tmp_path), "/e", None)
    r2 = FA.audit_run_cached("1Q_08_qubit_spectroscopy", str(tmp_path), "/e", None)
    assert calls["n"] == 1 and r1 is r2                       # second served from cache
    assert FA.cached_result(str(tmp_path), None, "/e") is r1
    # env is in the key: a different env is a different gate -> cache miss
    assert FA.cached_result(str(tmp_path), None, "/other") is None
    (tmp_path / "ds_raw.h5").write_bytes(b"xy")               # data change -> re-fingerprint
    FA.audit_run_cached("1Q_08_qubit_spectroscopy", str(tmp_path), "/e", None)
    assert calls["n"] == 2
    # data.json is fingerprinted too (the stored-claim source)
    (tmp_path / "data.json").write_text('{"fit_results": {}}')
    FA.audit_run_cached("1Q_08_qubit_spectroscopy", str(tmp_path), "/e", None)
    assert calls["n"] == 3


def test_audit_run_cached_does_not_cache_errors(tmp_path, monkeypatch):
    (tmp_path / "node.json").write_text("{}")
    (tmp_path / "ds_raw.h5").write_bytes(b"x")
    monkeypatch.setattr(FA, "audit_run",
                        lambda *a, **k: {"auditable": True, "rows": [], "errors": [{"stage": "x"}]})
    FA._VERDICT_CACHE.clear()
    FA.audit_run_cached("1Q_08_qubit_spectroscopy", str(tmp_path), "/e", None)
    assert FA.cached_result(str(tmp_path), None, "/e") is None   # a bad run stays retryable


def test_cached_result_cold_is_none(tmp_path):
    FA._VERDICT_CACHE.clear()
    assert FA.cached_result(str(tmp_path), None, "/e") is None


@pytest.mark.parametrize("verdict,glyph", [
    ("reject", "✕"), ("recover", "↑"), ("drift", "↔"), ("agrees", "✓"), ("unverifiable", "?")])
def test_verdict_badge_renders(verdict, glyph):
    from quam_state_manager.web.app import create_app
    from flask import render_template
    with create_app().test_request_context():
        h = render_template("_fit_audit_verdict.html",
                            row={"verdict": verdict, "detail": "d", "fresh_success": True},
                            gate_hash="abcdef012345zz")
        assert ("pp-verdict-" + verdict) in h and glyph in h and "abcdef012345" in h


def test_verdict_badge_both_reject_not_green():
    """A 'both reject' fit (agrees verdict, fresh_success False) must NOT read as a
    green ✓ 'matches' on the apply popup — it's surfaced as a current-gate reject."""
    from quam_state_manager.web.app import create_app
    from flask import render_template
    with create_app().test_request_context():
        h = render_template("_fit_audit_verdict.html",
                            row={"verdict": "agrees", "fresh_success": False, "detail": "both reject"},
                            gate_hash="abcdef012345")
        assert "pp-verdict-reject" in h
        assert "pp-verdict-agrees" not in h
        assert "matches the current gate" not in h
        assert "rejects this fit" in h


def test_verdict_badge_agrees_accept_is_green():
    from quam_state_manager.web.app import create_app
    from flask import render_template
    with create_app().test_request_context():
        h = render_template("_fit_audit_verdict.html",
                            row={"verdict": "agrees", "fresh_success": True, "detail": ""},
                            gate_hash="abcdef012345")
        assert "pp-verdict-agrees" in h and "matches the current gate" in h


def test_verdict_check_affordance_renders():
    from quam_state_manager.web.app import create_app
    from flask import render_template
    with create_app().test_request_context():
        h = render_template("_fit_audit_verdict_check.html", uid="k:1", qubit="qA2")
        assert "pp-verdict-check-btn" in h and "k:1" in h and "qA2" in h


def test_verdict_endpoint_204_without_args():
    from quam_state_manager.web.app import create_app
    c = create_app().test_client()
    assert c.get("/fit-audit/verdict").status_code == 204            # no uid
    assert c.get("/fit-audit/verdict?uid=x:1").status_code == 204    # no qubit


# ---------------------------------------------------------------------------
# §6-③ ANCHOR integration test (opt-in: needs the LabB venv + LabA archive)
# ---------------------------------------------------------------------------

_VENV = "<work-root>/LabA/naive_code/qualibration_graphs/superconducting/.venv/bin/python"
_SRC = "<work-root>/Customer_Codes/LabB/qualibration_graphs/superconducting"
_RVP = "<dataset-root>/example_lab/2026-05-22/#176_1Q_05_resonator_spectroscopy_vs_power_020954"
_QS298 = "<dataset-root>/example_lab/2026-05-29/#298_1Q_08_qubit_spectroscopy_020445"
_QS236 = "<dataset-root>/example_lab/2026-05-28/#236_1Q_08_qubit_spectroscopy_225436"

_have_env = os.path.exists(_VENV) and os.path.isdir(_SRC)
anchor = pytest.mark.skipif(not _have_env, reason="LabB venv / hardened source tree absent")


@anchor
@pytest.mark.skipif(not os.path.isdir(_RVP), reason="rvp #176 archive absent")
def test_anchor_rvp_176_rejects_qB1_qD3():
    r = FA.audit_run("1Q_05_resonator_spectroscopy_vs_power", _RVP, _VENV, _SRC)
    by_q = {row["qubit"]: row["verdict"] for row in r["rows"]}
    assert by_q.get("qB1") == "reject"
    assert by_q.get("qD3") == "reject"
    assert r["gate_hash"]


@anchor
@pytest.mark.skipif(not os.path.isdir(_QS298), reason="qspec #298 archive absent")
def test_anchor_qspec_298_flag():
    r = FA.audit_run("1Q_08_qubit_spectroscopy", _QS298, _VENV, _SRC)
    by_q = {row["qubit"]: row["verdict"] for row in r["rows"]}
    assert by_q.get("qA2") == "reject"


@anchor
@pytest.mark.skipif(not os.path.isdir(_QS236), reason="qspec #236 archive absent")
def test_anchor_qspec_236_all_agree():
    r = FA.audit_run("1Q_08_qubit_spectroscopy", _QS236, _VENV, _SRC)
    assert r["counts"]["reject"] == 0
    assert r["counts"]["agrees"] == 5


# ---------------------------------------------------------------------------
# Cross-platform audit: ns-resolution run fingerprint + atomic settings write
# ---------------------------------------------------------------------------

def test_run_fingerprint_sees_same_second_same_size_rewrite(tmp_path):
    """int(st_mtime) second-truncation made a same-second same-size rewrite
    invisible → a stale cached verdict; the fingerprint must use st_mtime_ns."""
    p = tmp_path / "node.json"
    p.write_text("{}", encoding="utf-8")
    base = 1_700_000_000 * 10**9
    os.utime(p, ns=(base, base))
    fp1 = FA._run_fingerprint(tmp_path)
    # 0.5 ms later: same integer second, same size — only the ns part moved.
    os.utime(p, ns=(base + 500_000, base + 500_000))
    fp2 = FA._run_fingerprint(tmp_path)
    assert fp1 != fp2


def test_set_audit_source_root_atomic_no_tmp_left(tmp_path):
    """The shared settings file is written via safe_io.atomic_write_json now —
    no plain write_text, no .tmp leftovers, other keys preserved."""
    from quam_state_manager.core import config_generator as CG
    CG.set_selected_env(tmp_path, "/envs/qm/bin/python")
    FA.set_audit_source_root(tmp_path, "/src/superconducting")
    assert list(Path(tmp_path).glob("*.tmp")) == []
    data = json.loads(
        (Path(tmp_path) / "config_generator.json").read_text(encoding="utf-8"))
    assert data["selected_env_python"] == "/envs/qm/bin/python"
    assert data["fit_audit_source_root"] == "/src/superconducting"
