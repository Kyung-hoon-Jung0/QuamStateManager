"""The display time zone (docs/196).

Snapshot stamps are UTC in both derivations (``core/history._ts_stamp`` uses
``datetime.now(timezone.utc)``; a run ingest converts the run's own local
wall-clock LOCAL->UTC, docs/132). The row-level display sites honoured that
through ``ts_local``; the CHART AXES did not -- they sliced the UTC digits into
a label and drew them unconverted and unlabelled, so one page showed two bases.

Customer: "trends에 찍히는 날짜/시간을 settings에서 time zone으로 바꿀수있게…
현재 qualibrete의 시간, 그러니까 date가 어떤 기준인지 모르겠네? 기준점이 있으면
좋겠는데."
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_snaptime_selfcheck():
    """Executed against the REAL app.js, including the Settings wiring."""
    proc = subprocess.run(["node", str(_ROOT / "tests" / "snaptime_selfcheck.cjs")],
                          capture_output=True, text=True, cwd=str(_ROOT), timeout=180)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


class TestThereIsExactlyOneFormatter:
    """A second spelling of "what time is this stamp" is how the page came to
    show two answers. These pin that the chart formatters go through the shared
    one rather than slicing the digits themselves again."""

    def test_the_param_history_chart_uses_it(self):
        src = (_STATIC / "app.js").read_text(encoding="utf-8")
        m = re.search(r"var fmtTs = function\(ts\) \{(.*?)\};", src, re.S)
        assert m, "the drawer chart's formatter is still called fmtTs"
        body = m.group(1)
        assert "SnapTime" in body, "it must go through the shared formatter"
        assert "ts.slice" not in body, \
            "slicing the digits is exactly the bug -- it ignores the zone"

    def test_the_chip_status_chart_uses_it(self):
        src = (_STATIC / "chip-status.js").read_text(encoding="utf-8")
        m = re.search(r"function _iso\(ts\) \{(.*?)\n    \}", src, re.S)
        assert m, "_iso is still the chip-status axis formatter"
        assert "SnapTime" in m.group(1), \
            "the axis must ask the shared formatter before its own fallback"

    def test_the_time_axis_names_its_zone(self):
        # The report was that the basis is unstated. An axis of clock times
        # that does not say which clock is the thing being complained about.
        src = (_STATIC / "chip-status.js").read_text(encoding="utf-8")
        assert "_tzNote" in src
        assert re.search(r"title: axisType === 'date'", src), \
            "the zone note belongs on the TIME axis only"


class TestNothingStoredMoves:
    """Display only. A zone is a rendering choice; if it reached anything that
    is stored, sorted or compared, a viewer in Seoul and one in Boston would
    disagree about which snapshot is newer."""

    def test_the_preference_is_client_side_only(self):
        routes = (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(
            encoding="utf-8")
        assert "quam_tz" not in routes, \
            "the zone must never reach the server -- it is a rendering choice"

    def test_the_stamp_derivations_are_untouched(self):
        hist = (_ROOT / "quam_state_manager" / "core" / "history.py").read_text(
            encoding="utf-8")
        # both derivations still land in UTC (docs/132's unification)
        assert "datetime.now(timezone.utc)" in hist
        # the run-ingest derivation: the run's own local wall clock -> UTC
        assert "naive.astimezone().astimezone(" in hist

    def test_the_settings_control_says_what_it_does_not_change(self):
        base = (_ROOT / "quam_state_manager" / "web" / "templates"
                / "base.html").read_text(encoding="utf-8")
        assert 'id="tz-select"' in base
        assert "display only" in base.lower(), \
            "the label must say it changes nothing stored"
        assert "UTC" in base
