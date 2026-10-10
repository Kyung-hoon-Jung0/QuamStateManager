"""Calendar calibration ages and shared inline snapshot labels."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from quam_state_manager.core import project_time, report_card
from quam_state_manager.web import routes
from tests.test_hub_versions import _bookmark, _inline, _meta, env  # noqa: F401 -- fixtures
from tests.test_report_card import _engine

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (zone, run, now, days, label)
    for zone, offset in [("UTC", "+00:00"), ("Etc/GMT-9", "+09:00"),
                         ("Etc/GMT+7", "-07:00")]
    for run, now, days, label in [
        ("2026-10-09T22:00:00" + offset, "2026-10-10T03:00:00" + offset, 1, "yesterday"),
        ("2026-10-10T01:00:00" + offset, "2026-10-10T23:00:00" + offset, 0, "today"),
        ("2026-09-05T22:00:00" + offset, "2026-10-10T03:00:00" + offset, 35, "35 days ago"),
    ]
] + [
    ("Europe/Berlin", "2026-03-28T22:00:00+01:00", "2026-03-29T03:00:00+02:00", 1, "yesterday"),
    ("Europe/Berlin", "2026-10-25T00:30:00+02:00", "2026-10-25T23:30:00+01:00", 0, "today"),
]


def _clock_case(case):
    zone, run, now, days, label = case
    return dict(zone=zone, run_ms=int(datetime.fromisoformat(run).timestamp() * 1000),
                now_ms=int(datetime.fromisoformat(now).timestamp() * 1000), days=days, label=label)


@pytest.mark.parametrize("case", CASES)
def test_calendar_age_in_display_zone(case):
    c = _clock_case(case)
    now = datetime.fromtimestamp(c["now_ms"] / 1000, ZoneInfo(c["zone"]))
    days = report_card._days_ago(c["run_ms"], now)
    assert days == c["days"]
    assert report_card._age(days) == c["label"]
    assert report_card._days_ago(None, now) is None
    assert report_card._age(None) == "no timestamp"
    topo = _engine().get_topology()
    topo["summary"].update(oldest_calibration=c["run_ms"], newest_calibration=c["run_ms"])
    report = report_card.build_report(SimpleNamespace(get_topology=lambda: topo),
                                      chip_name="device", generated_at=now)
    assert report["counts"]["oldest_calibration_days"] == c["days"]
    assert report["counts"]["newest_calibration_days"] == c["days"]
    for render in (report_card.render_markdown, report_card.render_html):
        assert c["label"] in render(report)
    assert f'oldest_calibration_days,{c["days"]},' in report_card.render_csv(report)


def _node(script, *args):
    proc = subprocess.run(["node", str(ROOT / "tests" / script), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_calendar_age_js_python_parity(tmp_path):
    fixture = tmp_path / "ages.json"
    fixture.write_text(json.dumps([_clock_case(c) for c in CASES]), encoding="utf-8")
    _node("v130_calendar_age_selfcheck.cjs", fixture)


def test_report_export_uses_project_zone(env, monkeypatch):
    project_time.set_zone(env.app.instance_path, "project", "Etc/GMT+7")
    fixed = datetime.fromisoformat("2026-10-10T03:00:00-07:00")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz or timezone.utc)

    monkeypatch.setattr(routes, "datetime", Clock)
    topo = _engine().get_topology()
    run_ms = datetime.fromisoformat("2026-10-09T22:00:00-07:00").timestamp() * 1000
    topo["summary"].update(oldest_calibration=run_ms, newest_calibration=run_ms)
    monkeypatch.setattr(routes, "_engine", lambda: SimpleNamespace(get_topology=lambda: topo))
    response = env.client.get("/topology/report")
    assert response.status_code == 200
    assert "2026-10-10T03:00:00-07:00" in response.get_data(as_text=True)
    assert "| Oldest calibration | yesterday |" in response.get_data(as_text=True)


LISTINGS = [
    ("/state/versions?limit=80", "#state-version-panel", "state-versions", "listing=versions&limit="),
    ("/state-history?body=1&per_page=50", "#state-history-body", "sh-timeline", "body=1&page=1&per_page=50"),
    ("/api/history?per_page=50", "#history-content", "history-timeline", "listing=drawer&page=1&per_page=50"),
]


def _label_form(body, ts):
    import re
    forms = re.findall(r'<form\b.*?</form>', body, re.S)
    matches = [f for f in forms if f"/state-history/{ts}/label?" in f]
    assert len(matches) == 1, "The snapshot has one inline label form"
    return matches[0]


@pytest.mark.parametrize("listing", LISTINGS)
@pytest.mark.parametrize("pinned", [False, True])
def test_label_set_clear_and_pin_untouched(env, listing, pinned):
    from tests.test_hub_versions import _parsed, ctx_of
    ts = _bookmark(env, label="before", note="kept note")
    with env.app.app_context():
        routes._history().annotate_snapshot(ctx_of(env)["path"], ts, pinned=pinned)
    url, target, marker, query = listing
    response = env.client.get(url)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    form = _label_form(body, ts)
    tags = _parsed(form)
    attrs = next(t["attrs"] for t in tags if t["tag"] == "form")
    assert attrs["hx-target"] == target
    assert query in attrs["hx-post"]
    inputs = [t["attrs"] for t in tags if t["tag"] == "input"]
    assert [(i.get("name"), i.get("maxlength"), i.get("value")) for i in inputs] == [("label", "80", "before")]
    assert "aria-label" in inputs[0]
    for label, expected in [("  after & <safe>  ", "after & <safe>"), ("", None)]:
        response = env.client.post(attrs["hx-post"], data={"label": label}, headers={"HX-Request": "true"})
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert marker in body
        assert '<html' not in body
        meta = _meta(env, ts)
        assert (meta.label, meta.pinned, meta.note) == (expected, pinned, "kept note")
        assert f'value="{escape(expected or "", quote=True)}"' in _label_form(body, ts)
        # Every listing reads the updated annotation, including the visible label.
        for other_url, _, _, _ in LISTINGS:
            other = env.client.get(other_url).get_data(as_text=True)
            assert ("after &amp; &lt;safe&gt;" in other) == bool(expected)


def test_label_length_limit_and_missing_label_keeps_bookmark(env):
    ts = _bookmark(env, label="before")
    url = f"/state-history/{ts}/label?body=1"
    assert env.client.post(url, data={"label": "x" * 80}).status_code == 200
    assert _meta(env, ts).label == "x" * 80
    assert env.client.post(url, data={"label": "x" * 81, "pinned": "1"}).status_code == 400
    assert (_meta(env, ts).label, _meta(env, ts).pinned) == ("x" * 80, False)
    assert env.client.post(url, data={"pinned": "1"}).status_code == 200
    assert (_meta(env, ts).label, _meta(env, ts).pinned) == ("x" * 80, True)


@pytest.mark.parametrize("listing", LISTINGS)
def test_ledger_without_snapshot_has_no_label_control(env, listing):
    body = env.client.get(listing[0]).get_data(as_text=True)
    assert "data-version-label-open" not in body
    assert env.client.post(f"/state-history/{env.refs[0]}/label", data={"label": "blocked"}).status_code == 409


@pytest.mark.parametrize("listing", LISTINGS)
def test_inline_label_keyboard_from_flask_render(env, tmp_path, listing):
    ts = _bookmark(env, label="before")
    body = env.client.get(listing[0]).get_data(as_text=True)
    form = _label_form(body, ts)
    assert "version-label.js" in env.client.get("/state-history").get_data(as_text=True)
    fixture = tmp_path / "listing.html"
    fixture.write_text(body, encoding="utf-8")
    _node("v130_version_label_selfcheck.cjs", fixture, unescape(form.split('hx-post="')[1].split('"')[0]))
