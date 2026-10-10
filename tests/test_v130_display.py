"""Flask render pins for the compact display fixes."""
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import render_template, render_template_string

from quam_state_manager.core import story, value_history
from quam_state_manager.core.hub_store import CHIP_UNCERTAIN
from quam_state_manager.core.timefmt import local_text, to_utc
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-15T08:22:35Z"


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))

    @app.get("/_display/<surface>")
    def display(surface):
        if surface == "writer":
            point = dict(eid=1, provenance="run_saved", is_current=True,
                         display="1", fill="1", t=STAMP, flag_text=[],
                         label="08_readout_square-fast <script>", sub="saved in run #1")
            return render_template("_field_history_ledger.html", points=[point],
                                   dot_path="qubits.q1.T1", ledger={"events": 1},
                                   total=1, current_value=1)
        if surface == "first":
            flags = story._flags_of(dict(first=True, flags=CHIP_UNCERTAIN,
                                         changes=[{}] * 5352))
            return render_template_string(
                "{% from '_journal_card.html' import card_body %}{{ card_body(c, '') }}",
                c=dict(kind="run", flags=flags, first_state_n=5352))
        if surface == "idle":
            return render_template("_hub_notes.html", notes=value_history.notes(
                {"state": "idle"}, {"last_run": STAMP}))
        entries = [SimpleNamespace(change_type=kind, dot_path=kind,
                                   old_value=None, new_value=None)
                   for kind in ("added", "removed", "modified")]
        return render_template(surface + ".html", entries=entries,
                               timestamp=STAMP, ts=STAMP, total=3,
                               summary=dict(added=1, removed=1, modified=1, total=3))

    return app.test_client()


def test_writer_breaks_only_at_separators_and_escapes_html(client):
    body = client.get("/_display/writer").get_data(as_text=True)
    assert '08_<wbr>readout_<wbr>square-<wbr>fast <wbr>&lt;script&gt;' in body
    assert 'squar<wbr>e' not in body and '<script>' not in body


def test_first_state_card_is_consistent(client):
    first = story._flags_of(dict(first=True, flags=CHIP_UNCERTAIN, changes=[{}] * 5352))
    assert first[0]["label"] == "starting values"
    body = client.get("/_display/first").get_data(as_text=True)
    assert ("The chip&#39;s first recorded state: 5,352 values. They are its starting "
            "values, not changes, so they are not listed here.") in body
    assert "5,352 values recorded; there is no earlier state to compare against." in body
    assert "identity does not match" not in body and "not counted" not in body
    other = story._flags_of(dict(first=False, flags=CHIP_UNCERTAIN, changes=[{}]))
    assert "identity does not match" in other[0]["note"]


@pytest.mark.parametrize("surface", ["_history_detail", "_version_diff", "_diff"])
def test_added_removed_null_and_absence_in_diff_rows(client, surface):
    body = client.get("/_display/" + surface).get_data(as_text=True)
    for kind, expected in (("added", ["(absent)", "null"]),
                           ("removed", ["null", "(absent)"]),
                           ("modified", ["null", "null"])):
        row = re.search(r'class="[^\"]*diff-row-' + kind + r'[^\"]*"(.*?)(?:</tr>|</div>\s*</div>)',
                        body, re.S)
        assert row, body
        values = re.findall(r'>\s*(\(absent\)|null)\s*<', row.group(1))
        assert values[:2] == expected, row.group(0)


def test_idle_note_has_a_localizable_time_and_plain_text_fallback(client, tmp_path):
    note = value_history.notes({"state": "idle"}, {"last_run": STAMP})[0]
    assert local_text(to_utc(STAMP)) in note["text"]
    assert STAMP not in note["text"]
    body = client.get("/_display/idle").get_data(as_text=True)
    assert 'class="ts-local" data-utc="' + STAMP + '"' in body
    assert 'runs after <span' in body
    assert '2026-09-15 08:22:35 UTC</span> may be missing.' in body
    file = tmp_path / "note.html"
    file.write_text(body, encoding="utf8")
    result = subprocess.run(["node", "tests/v130_display_selfcheck.cjs", str(file)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_drawer_chart_and_kind_selfcheck():
    result = subprocess.run(["node", "tests/v130_display_selfcheck.cjs"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed" in result.stdout


def test_two_recorded_versions_distinguish_null_from_absence(tmp_path):
    from quam_state_manager.core import hub
    from tests.test_hub_versions import build_env, chip_state
    before, after = chip_state(), chip_state()
    before["qubits"]["qA1"]["removed_null"] = None
    after["qubits"]["qA1"]["added_null"] = None
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    try:
        env = build_env(tmp_path, [before, after])
        chip = env.chip_dir.name
        body = env.client.get(
            f"/diff?a=hist:{chip}/{env.refs[0]}&b=hist:{chip}/{env.refs[1]}&view=list",
            headers={"HX-Request": "true"}).get_data(as_text=True)
        for leaf, expected in (("added_null", ["(absent)", "null"]),
                               ("removed_null", ["null", "(absent)"])):
            row = re.search(r'<tr class="diff-row-[^\"]+"[^>]*>.*?' + leaf + r'.*?</tr>', body, re.S)
            assert row, body
            values = re.findall(r'class="diff-list-[ab]"[^>]*>(.*?)</td>', row.group(0), re.S)
            assert [re.sub(r'<[^>]+>', '', v).strip() for v in values][-2:] == expected
    finally:
        hub.set_inline(old)
