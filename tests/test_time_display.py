"""docs/244 -- one display form for every absolute time, offsets kept.

* ``core.timefmt.to_utc`` reads every spelling as an INSTANT: SM's UTC stamp,
  ISO with an offset (node.json ``run_start = ...+09:00``), ISO with ``Z``,
  naive ISO (SM's own UTC records), epoch seconds; anything else is None.
* ``ts_local`` / ``format_ts`` read through it -- the old reader kept the
  first 19 characters and appended ``Z``, so a Korean run at 19:55+09:00 was
  shown nine hours late.
* The page renders ``YYYY-MM-DD HH:MM:SS (UTC+9)`` in the viewer's zone
  (``SnapTime.display``, pinned by tests/time_display_selfcheck.cjs), never
  the browser locale's words ("오후 7:55").
* The short title reads "QSM".
"""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import timefmt
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "quam_state_manager" / "web" / "static"
KST = timezone(timedelta(hours=9))


@pytest.mark.parametrize("ts, want", [
    ("2026-09-30T19:55:46.424+09:00", "2026-09-30T10:55:46Z"),   # node.json, offset kept
    ("2026-09-30T19:55:46+09:00", "2026-09-30T10:55:46Z"),
    ("2026-09-30T10:55:46Z", "2026-09-30T10:55:46Z"),
    ("2026-09-30T10:55:46", "2026-09-30T10:55:46Z"),              # SM's naive ISO is UTC
    ("2026-09-30 10:55:46", "2026-09-30T10:55:46Z"),
    ("20260930_105546", "2026-09-30T10:55:46Z"),                  # SM's snapshot stamp
    ("20260930_105546_123456", "2026-09-30T10:55:46Z"),
    ("2026-09-30T06:55:46-04:00", "2026-09-30T10:55:46Z"),
])
def test_every_spelling_reads_as_its_instant(ts, want):
    assert timefmt.iso_z(timefmt.to_utc(ts)) == want


def test_epoch_and_aware_datetimes():
    assert timefmt.iso_z(timefmt.to_utc(0)) == "1970-01-01T00:00:00Z"
    assert timefmt.iso_z(timefmt.to_utc(datetime(2026, 9, 30, 19, 55, 46, tzinfo=KST))) == "2026-09-30T10:55:46Z"


@pytest.mark.parametrize("bad", ["", "not a time", None, True, "2026-13-45T99:99:99"])
def test_a_non_time_is_none_never_a_guess(bad):
    assert timefmt.to_utc(bad) is None


@pytest.mark.parametrize("hours, mins, want", [
    (9, 0, "UTC+9"), (0, 0, "UTC"), (-4, 0, "UTC-4"), (5, 30, "UTC+5:30"), (-3, -30, "UTC-3:30")])
def test_offset_label(hours, mins, want):
    d = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=hours, minutes=mins)))
    assert timefmt.offset_label(d) == want


def test_local_text_names_its_offset():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(UTC([+-]\d{1,2}(:\d{2})?)?\)",
                        timefmt.local_text())


@pytest.fixture(scope="module")
def filters(tmp_path_factory):
    app = create_app(testing=True, instance_path=str(tmp_path_factory.mktemp("inst")))
    return app.jinja_env.filters


def test_ts_local_keeps_the_offset(filters):
    """The bug: 19:55+09:00 was emitted as 19:55Z (nine hours late)."""
    html = str(filters["ts_local"]("2026-09-30T19:55:46.424+09:00"))
    assert 'data-utc="2026-09-30T10:55:46Z"' in html
    assert "2026-09-30 10:55:46 UTC" in html                 # the no-JS fallback is honest UTC


def test_ts_local_short_and_non_time(filters):
    html = str(filters["ts_local"]("20260930_105546", short=True))
    assert 'data-fmt="short"' in html and ">09-30 10:55<" in html
    assert 'data-utc' not in str(filters["ts_local"]("pending"))


def test_format_ts_converts_instead_of_relabelling(filters):
    assert filters["format_ts"]("2026-09-30T19:55:46+09:00") == "2026-09-30 10:55:46 UTC"
    assert filters["format_ts"]("20260930_105546") == "2026-09-30 10:55:46 UTC"
    assert filters["format_ts"]("whatever") == "whatever"


def test_no_browser_locale_dates_in_the_ui_code():
    """Every absolute time goes through SnapTime.display -- a locale-default
    date call renders the browser language's words ("오후", "PM")."""
    bad = []
    pat = re.compile(r"toLocaleTimeString\(|toLocaleDateString\(|toLocaleString\(\s*undefined"
                     r"|new Date\([^)]*\)\.toLocaleString\(")
    for p in STATIC.glob("*.js"):
        if ".min." in p.name or "plotly" in p.name:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and "docs/244" not in line:
                bad.append(f"{p.name}:{n}: {line.strip()[:100]}")
    assert not bad, "\n".join(bad)


def test_the_short_title_reads_qsm():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert len(re.findall(r'\.app-title::after \{ content: "QSM";', css)) == 2
    assert 'content: "SM";' not in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_time_display_selfcheck():
    r = subprocess.run(["node", str(ROOT / "tests" / "time_display_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "FAIL" not in out, out[-3000:]
