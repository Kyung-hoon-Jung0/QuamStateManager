"""Reading order of the Pulses pages: the conclusion first, then the numbers
that matter as tiles, then the evidence in marked zones.

Pinned here: the gate-pulse callout and its length tiles read from the very
sentence the page shows; the key-value tiles of a pulse section are the values
of its own rows; the Used by list folds behind a count; one primary action and
Delete as the quiet danger that still opens its confirm; the New pulse form in
three numbered zones with every field in its old submission order; the Z-line
banner counts the rows the table renders. The chip-layout fold is pinned by
pulses_reading_order_fold_selfcheck.cjs.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.test_pulse_played_copy import PLAYED, SLOT, _detail, _state, client_for  # noqa: F401
from tests.test_zline_page import _chip as _zline_chip
from tests.test_zline_page import client as zline_client  # noqa: F401

_ROOT = Path(__file__).resolve().parent.parent


def _stats(html):
    """[(label, value)] of every stat tile, in order."""
    return re.findall(r'<div class="ui-stat-label">([^<]*)</div><p class="ui-stat-value">([^<]*)', html)


class TestPlayedCallout:
    def test_the_callout_comes_first_and_carries_both_lengths(self, client_for):
        html, _ = _detail(client_for(), SLOT)
        call = html.index('data-played-note="1"')
        assert call < html.index('id="pulse-detail-plot"') and call < html.index('class="pulse-actions')
        block = html[call:html.index("</div></div></div>", call)]
        assert "ui-callout ui-callout--danger" in block
        assert "Macro pulse and played op differ" in block
        note = re.search(r'<p class="ui-callout-text">([^<]*)</p>', block).group(1)
        m = re.search(r"length (\S+ ns) played vs (\S+ ns) in the copy", note)
        assert m, note
        # the tiles are the two lengths of the sentence, not a second reading
        assert _stats(block) == [("Length the gate plays", m.group(1)),
                                 ("Length in the macro copy", m.group(2))]
        assert m.group(1) != m.group(2)

    def test_no_callout_when_the_copy_is_what_plays(self, client_for):
        html, _ = _detail(client_for(same=True), SLOT)
        assert "ui-callout--danger" not in html and "data-played-note" not in html


class TestSectionTilesAndUsedBy:
    @staticmethod
    def _section(tmp_path, params):
        app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
        sec = dict(path="pulse", index=0, color="", role="Pulse", owner="", short="pulse",
                   params=params, used_by=[], named_slots=[])
        return app.jinja_env.get_template("_pulse_params_section.html").render(
            sec=sec, main_path="pulse", view_paths=[])

    @staticmethod
    def _param(**overrides):
        return dict(dict(key="length", value=40, unit="ns", is_list=False,
                         is_runtime=False, is_pointer=False, ref_known=True,
                         dot_path="pulse.length", kind="int"), **overrides)

    @pytest.mark.parametrize("guard,value,flags", [
        ("list", [40, 80], {"is_list": True}),
        ("runtime", "unresolved", {"is_runtime": True}),
        ("empty", "", {}),
        ("none", None, {}),
    ], ids=["list", "runtime", "empty", "none"])
    def test_each_unreadable_value_gets_no_tile(self, tmp_path, guard, value, flags):
        # Each fixture disables just one guard; the other guards still pass.
        html = self._section(tmp_path, [self._param(value=value, **flags)])
        assert 'data-copy="pulse.length"' in html
        assert _stats(html) == [], guard
        assert 'class="ui-stats"' not in html

    def test_tile_order_and_units_follow_the_key_numbers(self, tmp_path):
        html = self._section(tmp_path, [
            self._param(key="amplitude", value=0.25, unit="V", dot_path="pulse.amplitude"),
            self._param(),
        ])
        assert _stats(html) == [("Length", "40"), ("Amplitude", "0.25")]
        units = re.findall(r'<span class="ui-stat-unit">([^<]*)</span>', html)
        assert units == ["ns", "V"]

    def test_tiles_are_the_rows_own_values(self, client_for):
        html, _ = _detail(client_for(), "qubits.q1.xy.operations.x180")
        sec = html[html.index('class="detail-section pulse-sec"'):]
        tiles = dict(_stats(sec))
        rows = dict(re.findall(r'data-param="(length|amplitude)".*?value="([^"]*)"', sec, re.S))
        assert tiles and tiles == {"Length": rows["length"], "Amplitude": rows["amplitude"]}
        assert sec.index('class="ui-stats"') < sec.index('class="prop-table"')

    def test_used_by_folds_behind_its_count(self, client_for):
        html, _ = _detail(client_for(pointer=True), PLAYED)
        m = re.search(r'<details class="ui-disclosure"><summary class="ui-disclosure-summary">Used by '
                      r'<span class="ui-count">(\d+)</span></summary><div class="ui-disclosure-body">\s*'
                      r'<p class="muted pulse-sec-usedby">(.*?)</p>', html, re.S)
        assert m, "Used by is not inside a disclosure"
        assert int(m.group(1)) == m.group(2).count("<code") >= 1


    def test_used_by_counts_the_named_slots_too(self, client_for):
        # the default chip's slot holds a copy whose id names the played op: it plays
        # it by name, so it is listed as a named slot and counted with the rest
        html, _ = _detail(client_for(), PLAYED)
        m = re.search(r'Used by <span class="ui-count">(\d+)</span></summary><div class="ui-disclosure-body">\s*'
                      r'<p class="muted pulse-sec-usedby">(.*?)</p>', html, re.S)
        assert m, "Used by is not inside a disclosure"
        assert "(plays by name)" in m.group(2)
        assert int(m.group(1)) == m.group(2).count("<code") >= 1

    def test_an_unresolved_pointer_gets_no_key_value_tile(self, tmp_path):
        doc = _state()
        doc["qubits"]["q1"]["xy"]["operations"]["x180"].update(
            length="#/nowhere/length", amplitude="#/nowhere/amplitude")
        d = tmp_path / "chip_dangling"
        d.mkdir()
        (d / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        (d / "wiring.json").write_text("{}", encoding="utf-8")
        c = create_app(testing=True, instance_path=str(d / "_app_instance")).test_client()
        assert c.post("/load", data={"folder": str(d)}).status_code in (200, 302)
        html, _ = _detail(c, "qubits.q1.xy.operations.x180")
        sec = html[html.index('class="detail-section pulse-sec"'):]
        for key in ("length", "amplitude"):  # the rows stay, as unresolved references
            assert f'data-copy="qubits.q1.xy.operations.x180.{key}"' in sec
        assert sec.count('class="row-pointer"') == 2
        labels = [lab for lab, _v in _stats(sec)]
        assert "Length" not in labels and "Amplitude" not in labels
        assert not any(v.startswith("#/") for _l, v in _stats(sec))


class TestDetailActions:
    def test_one_primary_and_delete_is_the_quiet_danger(self, client_for):
        html, _ = _detail(client_for(), "qubits.q1.xy.operations.x180")
        bar = html[html.index('class="pulse-actions'):]
        bar = bar[:bar.index("</div>")]
        assert 'role="group"' in bar and "ui-actions" in bar
        classes = [c.split() for c in re.findall(r'<button type="button" class="([^"]*)"', bar)]
        primary = [c for c in classes if not ({"outline", "secondary", "danger"} & set(c))]
        assert primary == [["btn-sm", "pulse-verify-btn"]]
        assert classes[-1] == ["btn-sm", "danger", "pulse-delete-btn"]
        assert "PulsesPage.askDelete(this)" in bar
        # the confirm step and its route are unchanged
        assert 'class="pulse-delete-confirm" hidden' in html and 'hx-post="/api/pulse/delete"' in html


_FORM_NAMES = ["target_kind", "target_kind", "target_kind", "qubit", "channel", "pair", "gate", "slot",
               "new_gate_name", "pc_pair", "pc_channel", "pulse_type", "op_name", "qclass"]


class TestCreateForm:
    def test_three_numbered_zones_and_the_old_field_order(self, client_for):
        html = client_for().get("/pulse/new").get_data(as_text=True)
        form = html[html.index('<form class="pulse-create-form"'):html.index("</form>")]
        steps = re.findall(r'<span class="ui-zone-step" aria-hidden="true">(\d)</span>', form)
        assert steps == ["1", "2", "3"]
        zones = form.split('<section class="ui-zone"')[1:]
        assert len(zones) == 3
        assert "pulse-target-row" in zones[0] and 'id="pulse-create-type"' in zones[1]
        assert 'id="pulse-create-plot"' in zones[2] and 'type="submit"' in zones[2]
        names = re.findall(r'\bname="([^"]+)"', form)
        assert [n for n in names if n in set(_FORM_NAMES)] == _FORM_NAMES

    def test_the_action_row_is_the_start_aligned_ui_actions(self, client_for):
        html = client_for().get("/pulse/new").get_data(as_text=True)
        row = re.search(r'<div class="([^"]*)" role="group" aria-label="Create or cancel">', html)
        assert row, "the Create / Cancel row is gone"
        assert {"ui-actions", "ui-actions--start", "pulse-create-actions"} <= set(row.group(1).split())

    def test_no_env_is_a_warning_notice(self, client_for):
        html = client_for().get("/pulse/new").get_data(as_text=True)
        strip = html[html.index('id="pulse-env-strip"'):]
        if "static catalog" not in strip[:800]:
            pytest.skip("an environment is selected in this test setup")
        notice = strip[strip.index('<div class="ui-notice ui-notice--warn"'):]
        notice = notice[:notice.index("</div>")]
        assert "no Python environment selected" in notice and 'href="/generate"' in notice
        # one plain sentence under a short key: the text does not repeat the key
        key = re.search(r'<strong class="ui-notice-key">([^<]*)</strong>', notice).group(1)
        text = re.search(r'<span class="ui-notice-text">(.*?)</span>', notice, re.S).group(1)
        assert key == "Built-in classes only." and key.rstrip(".").lower() not in text.lower()
        assert 'role="status"' in notice and "built-in list" in text

    def test_the_no_env_branch_of_the_template_is_ascii_and_uses_the_macro(self):
        src = (_ROOT / "quam_state_manager" / "web" / "templates" / "_pulse_env_strip.html").read_text(encoding="utf-8")
        branch = src[src.index("{% if not env_card.selected %}"):src.index("{% elif not env_card.selected_exists %}")]
        assert branch.isascii(), [ch for ch in branch if ord(ch) > 127]
        assert "ui_notice(" in branch and 'class="ui-notice' not in branch


class TestZlineBanner:
    @pytest.mark.parametrize("levels,tone,role", [
        (["info", "warn", "block"], "danger", "alert"),
        (["info", "warn"], "warn", "status"),
        (["info"], None, None),
    ], ids=["blocking", "warning", "clean"])
    def test_banner_tone_follows_the_worst_row(self, tmp_path, levels, tone, role):
        app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
        rows = [dict(id=f"line{i}", channel_path=f"line{i}", port_path="",
                     n_exp=0, n_taps=0, ff_sum=1, models={}, worst=level,
                     notes=[dict(level=level, text="Filter note")])
                for i, level in enumerate(levels)]
        html = app.jinja_env.get_template("_zline.html").render(
            rows=rows, selected="line0", missing=None, qdac_only=[], bias_tee=[])
        if tone is None:
            assert "data-zline-flagged" not in html
            assert "ui-notice" not in html
        else:
            assert f'class="ui-notice ui-notice--{tone}" role="{role}"' in html
            assert f'data-zline-flagged="{len(levels) - 1}" data-zline-total="{len(levels)}"' in html

    def test_the_banner_counts_the_rendered_rows(self, zline_client):
        html = zline_client.get("/zline", headers={"HX-Request": "true"}).get_data(as_text=True)
        rows = re.findall(r'<td class="zline-row-note">(.*?)</td>', html, re.S)
        flagged = [r for r in rows if "ui-pill--ok" not in r]
        assert 0 < len(flagged) < len(rows), "the fixture must flag some lines, not all"
        m = re.search(r'data-zline-flagged="(\d+)" data-zline-total="(\d+)"', html)
        assert m and (int(m.group(1)), int(m.group(2))) == (len(flagged), len(rows))
        assert f"{len(flagged)} of {len(rows)} flux lines need a look." in html
        assert html.index("data-zline-flagged") < html.index('id="zline-table"')


def _zline_client(tmp_path, name, drop=()):
    state, wiring = _zline_chip()
    for q in drop:
        state["qubits"].pop(q)
        wiring["wiring"]["qubits"].pop(q)
    live = tmp_path / name
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / (name + "_inst"))).test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


class TestZlineBannerGrammar:
    @pytest.mark.parametrize("drop", [(), ("q3",)])
    def test_the_verb_follows_the_flagged_count(self, tmp_path, drop):
        html = _zline_client(tmp_path, "chip", drop).get("/zline", headers={"HX-Request": "true"}).get_data(as_text=True)
        m = re.search(r'data-zline-flagged="(\d+)" data-zline-total="(\d+)"', html)
        assert m
        k, n = int(m.group(1)), int(m.group(2))
        noun = "flux line" if n == 1 else "flux lines"
        verb = "needs" if k == 1 else "need"
        assert f"{k} of {n} {noun} {verb} a look." in html
        assert f"{noun} {'need' if k == 1 else 'needs'} a look." not in html

    def test_both_counts_are_exercised(self, tmp_path):
        seen = set()
        for i, drop in enumerate([(), ("q3",)]):
            html = _zline_client(tmp_path, f"c{i}", drop).get("/zline", headers={"HX-Request": "true"}).get_data(as_text=True)
            m = re.search(r'data-zline-flagged="(\d+)"', html)
            seen.add(int(m.group(1)) if m else 0)
        assert 1 in seen and any(k >= 2 for k in seen), seen


class TestPlayedCalloutIsAnnounced:
    def test_the_danger_callout_is_an_alert(self, client_for):
        html, _ = _detail(client_for(), SLOT)
        wrap = re.search(r'<div data-played-note="1"([^>]*)>', html)
        assert wrap and 'role="alert"' in wrap.group(1)
        parser = _OpeningTags()
        parser.feed(wrap.group(0))
        attrs = parser.attrs
        assert "hidden" not in attrs
        # The wrapper needs no classes; this also rejects every hiding class.
        assert not attrs.get("class", "").split()
        assert "style" not in attrs


class _OpeningTags(HTMLParser):
    def handle_starttag(self, tag, attrs):
        self.attrs = dict(attrs)


def test_pulses_map_fold():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / "pulses_reading_order_fold_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "ALL OK" in r.stdout, r.stdout + r.stderr
