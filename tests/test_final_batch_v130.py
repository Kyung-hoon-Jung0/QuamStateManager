"""v1.3.0 final batch pins (server side): SB-01 redo reason, SB-07 applied-log revert."""
from tests.state_model.repros._rig import Chip


def test_redo_after_own_edit_never_blames_another_window(tmp_path):
    """SB-01: own edit + Apply after a live undo forks redo; the refusal must
    not claim another window or a run made the change."""
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    assert chip.c.post("/state/apply-to-live").status_code == 200
    assert chip.c.post("/undo").status_code == 200
    chip.edit("qubits.q2.T1", "4.1e-05")
    assert chip.c.post("/state/apply-to-live").status_code == 200
    r = chip.c.post("/redo")
    cr = chip.trigger(r).get("cellsReverted") or {}
    assert cr.get("stopped") == "forked", cr
    msg = cr.get("message") or ""
    assert "Nothing to redo" in msg and "another window" not in msg and "a run" not in msg, msg


def _run_selfcheck(name: str, min_ok: int):
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    sc = Path(__file__).resolve().parent / name
    res = subprocess.run([node, str(sc)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"{name} failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok ") >= min_ok, res.stdout


def test_stale_tray_answer_never_paints_auto_sync_on_again():
    """SB-08: Turn off, then an older tray answer -- the pill stays off."""
    _run_selfcheck("autosync_stale_pill_selfcheck.cjs", 6)



def test_applied_log_revert_marks_the_row_and_rereads_the_panel():
    """SB-07: after the applied-log x lands, the row offers no second x."""
    _run_selfcheck("applied_log_revert_selfcheck.cjs", 5)


def test_applied_log_in_the_sync_panel_is_in_flow():
    """SB-05: the floating list drew its rows over the panel text below; inside
    the panel it must be in flow (the topbar dropdown elsewhere stays absolute)."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
           / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"\.sp-history \.applied-log-list \{([^}]*)\}", css)
    assert m and "position: static" in m.group(1), "the in-panel applied log floats again"
    assert re.search(r"\.sp-history \.applied-log \{[^}]*display: block", css)


def test_datasets_run_table_width_is_its_colgroup_and_defaults_fit():
    """L9: width max-content re-measured the rows in view (sideways scroll range
    jumped 73 px while scrolling, measured at 1280); width 0 + min-width 100%
    keeps it the colgroup. The wider defaults keep "finished", the corpus's
    longest experiment name and a long key metric unclipped (measured 1280/1600)."""
    import re
    from pathlib import Path
    st = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
    css = (st / "style.css").read_text(encoding="utf-8")
    m = re.search(r"\.datasets-table-virtual \{([^}]*)\}", css)
    assert m and "width: 0;" in m.group(1) and "min-width: 100%" in m.group(1), m and m.group(1)
    assert "max-content" not in m.group(1)
    js = (st / "dataset-virtual.js").read_text(encoding="utf-8")

    def w(key):
        mm = re.search(r"\{key: '" + key + r"', label: '[^']+', on: true, w: (\d+)", js)
        return int(mm.group(1))
    assert w("status") >= 104 and w("exp") >= 280 and w("metric") >= 108
