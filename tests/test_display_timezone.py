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

class TestTheCompactChipsCarryTheInstantToo:
    """docs/201 — the `when` chips were the same defect as the chart axes.

    Seven server-side sites sliced a UTC stamp into a label
    (``f"{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"``); five reached a template
    and were read as times, so a Seoul viewer read them nine hours off from the
    ``ts_local`` rows beside them. They grew their own slicing because
    ``ts_local`` had no COMPACT form and a chip has no room for a full stamp.
    """

    def _filter(self):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True)
        return app.jinja_env.filters["ts_local"]

    def test_the_short_form_carries_the_same_instant(self):
        f = self._filter()
        long_html = str(f("20260910_141516"))
        short_html = str(f("20260910_141516", short=True))
        assert 'data-utc="2026-09-10T14:15:16Z"' in long_html
        assert 'data-utc="2026-09-10T14:15:16Z"' in short_html, \
            "the compact form must carry the SAME instant, not a truncated one"

    def test_the_short_form_is_marked_so_the_client_can_render_it_small(self):
        f = self._filter()
        assert 'data-fmt="short"' in str(f("20260910_141516", short=True))
        assert 'data-fmt' not in str(f("20260910_141516"))

    def test_the_no_js_fallback_is_compact_but_still_a_real_time(self):
        f = self._filter()
        html = str(f("20260910_141516", short=True))
        assert "09-10 14:15" in html, "a chip-sized fallback"
        assert "2026-09-10 14:15:16 UTC" not in html, "not the full stamp"

    def test_an_unparseable_stamp_is_not_invented(self):
        f = self._filter()
        html = str(f("not-a-stamp", short=True))
        assert "not-a-stamp" in html
        assert "data-utc" not in html

    def test_every_fetched_fragment_that_carries_one_is_localized(self):
        """The CLIENT half of the same fix. ``.ts-local`` ships
        ``visibility:hidden`` until ``applyLocalTimes`` stamps it, and only an
        htmx swap runs that automatically. docs/201 moved the Column History and
        field-history chips onto ``ts_local``, but both cards load through a raw
        ``fetch`` + ``innerHTML``. Every chip's time went INVISIBLE (measured in
        real Chrome, 2026-09-19), the trap docs/128 had already found once on
        the version-diff overlay.

        So: every ``fetch`` of a route that renders a ``ts_local`` template
        must call ``applyLocalTimes`` in its handler.
        """
        tpl_dir = _ROOT / "quam_state_manager" / "web" / "templates"
        carrying = {p.name for p in tpl_dir.glob("_*.html")
                    if "ts_local" in p.read_text(encoding="utf-8")}
        routes_src = (_ROOT / "quam_state_manager" / "web" / "routes.py"
                      ).read_text(encoding="utf-8")
        route_tpl: dict[str, set] = {}
        heads = list(re.finditer(r'@bp\.route\("([^"]+)"', routes_src))
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(routes_src)
            body = routes_src[m.end():end]
            for t in carrying:
                if f'"{t}"' in body:
                    route_tpl.setdefault(m.group(1).split("<")[0], set()).add(t)

        checked, missing = [], []
        for js in sorted(_STATIC.glob("*.js")):
            src = js.read_text(encoding="utf-8")
            for m in re.finditer(r"""fetch\(\s*(["'])(/[^"'?]*)""", src):
                path = m.group(2)
                if path not in route_tpl:
                    continue
                end = src.find(".catch(", m.end())
                handler = src[m.end(): end if end > 0 else m.end() + 1500]
                where = f"{js.name}:{src.count(chr(10), 0, m.start()) + 1} {path}"
                checked.append(where)
                # a CALL, not the word: the docs/128 handler's own comment
                # names the function, which made a removed call read as present
                if "applyLocalTimes(" not in handler:
                    missing.append(where)
        assert {"/bulk/column-history", "/field/history",
                "/state/versions/"} <= {w.split(" ")[1] for w in checked}, \
            f"the scan must reach the known fetch sites; saw {checked}"
        assert not missing, (
            "these fetch+innerHTML loaders render a ts_local template without "
            f"applyLocalTimes, so the time stays invisible: {missing}")

    def test_the_five_display_sites_no_longer_slice_digits(self):
        from pathlib import Path
        tpl_dir = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"
        for name, needle in [
            ("_column_history.html", "ch-chip-when"),
            ("_column_history.html", "ch-run-when"),
            ("_field_history.html", "fh-ts"),
            ("_param_history_changes.html", "ph-change-when"),
        ]:
            html = (tpl_dir / name).read_text(encoding="utf-8")
            line = [l for l in html.splitlines() if needle in l][0]
            assert "ts_local" in line, f"{name}:{needle} still renders a pre-sliced string"

