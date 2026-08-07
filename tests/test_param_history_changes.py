""""What changed" — the Param History change feed (docs/83, A2).

The curated dashboard answers "how has T1 drifted". This answers the question
users actually arrive with: *what did that run change?* — over every numeric
parameter, which only became affordable once they were stored as change points.

The feed is paged by SNAPSHOT, not by row. That is not cosmetic: a regenerate
rewrites thousands of parameters in one snapshot (2,716 measured on a real
chip), and a row-paged feed would spend its whole page on that one event and
hide every other. Each group therefore shows its TRUE count and only the first
rows, with the rest one click away.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


def _state(t1=1.0e-5, amp=0.1, extra=None):
    q = {"id": "qA1", "T1": t1, "f_01": 5.0e9,
         "xy": {"operations": {"x180": {"amplitude": amp}}}}
    if extra:
        q.update(extra)
    return {"qubits": {"qA1": q}, "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    _write(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    return {"app": app, "client": c, "live": live,
            "hm": app.config["history_manager"]}


def _snap(env, state, **kw):
    _write(env["live"], state)
    m = env["hm"].check_and_snapshot(str(env["live"]), kw.pop("trigger", "manual"),
                                     force=True, **kw)
    assert m is not None
    return m


def _get(env, url):
    return env["client"].get(url, headers={"HX-Request": "true"})


class TestTheFeed:
    def test_a_change_shows_old_new_and_delta(self, env):
        _snap(env, _state(t1=1.0e-5))
        _snap(env, _state(t1=2.0e-5), trigger="experiment",
              experiment_name="05_T1", run_id=7)
        r = _get(env, "/param-history/changes")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "qubits.qA1.T1" in html
        assert "05_T1" in html and "#7" in html
        assert "val-delta" in html, "the Δ must render, not just the two values"

    def test_unchanged_parameters_are_absent(self, env):
        _snap(env, _state(t1=1.0e-5, amp=0.1))
        _snap(env, _state(t1=2.0e-5, amp=0.1))       # amp untouched
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        newest = html.split("ph-change-group")[1]
        assert "qubits.qA1.T1" in newest
        assert "x180.amplitude" not in newest

    def test_groups_are_newest_first(self, env):
        _snap(env, _state(t1=1.0e-5))
        _snap(env, _state(t1=2.0e-5))
        _snap(env, _state(t1=2.0e-5, amp=0.9))       # only the amplitude moves
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert html.index("x180.amplitude") < html.index("qubits.qA1.T1")

    def test_wiring_parameters_are_in_the_feed_too(self, env):
        _snap(env, _state())
        w2 = json.loads(json.dumps(_WIRING))
        w2["ports"]["mw_outputs"]["con1"]["1"]["2"]["band"] = 3
        _write(env["live"], _state(t1=1.5e-5), w2)
        env["hm"].check_and_snapshot(str(env["live"]), "manual", force=True)
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert "ports.mw_outputs.con1.1.2.band" in html

    def test_the_first_recorded_value_says_so_instead_of_a_fake_delta(self, env):
        _snap(env, _state())
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert "first recorded" in html


class TestPagingBySnapshot:
    def test_a_big_snapshot_is_capped_with_its_true_count(self, env):
        """The regenerate case: one snapshot rewrites far more parameters than
        a page can hold. The group must still say how many."""
        big = _state()
        big["qubits"]["qA1"]["extras_numbers"] = {f"k{i}": i for i in range(80)}
        _snap(env, _state())
        _snap(env, big)
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert "Show all" in html and "from this snapshot" in html
        assert "and " in html and "more" in html

    def test_at_opens_one_snapshot_in_full(self, env):
        big = _state()
        big["qubits"]["qA1"]["extras_numbers"] = {f"k{i}": i for i in range(80)}
        _snap(env, _state())
        m = _snap(env, big)
        html = _get(env, f"/param-history/changes?at={m.timestamp}").get_data(as_text=True)
        assert html.count('class="ph-change-path"') >= 80
        assert "Back to all changes" in html

    def test_older_page_does_not_repeat_the_newest(self, env):
        """Each snapshot moves a DIFFERENT parameter, so the page boundary is
        visible in the content rather than in a timestamp string."""
        metas = []
        for i in range(4):
            st = _state()
            st["qubits"]["qA1"][f"marker{i}"] = float(i)
            metas.append(_snap(env, st))
        newest = _get(env, "/param-history/changes").get_data(as_text=True)
        assert "marker3" in newest
        older = _get(env, f"/param-history/changes?before={metas[-1].timestamp}") \
            .get_data(as_text=True)
        assert "marker3" not in older
        assert "marker2" in older


class TestFiltering:
    def test_a_prefix_scopes_the_feed(self, env):
        _snap(env, _state())
        w2 = json.loads(json.dumps(_WIRING))
        w2["ports"]["mw_outputs"]["con1"]["1"]["2"]["band"] = 3
        _write(env["live"], _state(t1=1.5e-5), w2)
        env["hm"].check_and_snapshot(str(env["live"]), "manual", force=True)
        html = _get(env, "/param-history/changes?prefix=ports").get_data(as_text=True)
        assert "ports.mw_outputs" in html
        assert "qubits.qA1.T1" not in html

    def test_a_prefix_matching_nothing_says_so(self, env):
        _snap(env, _state(t1=2e-5))
        html = _get(env, "/param-history/changes?prefix=zzz.nope").get_data(as_text=True)
        assert "No parameter matching" in html

    def test_typeahead_returns_paths_with_change_counts(self, env):
        _snap(env, _state(t1=1e-5))
        _snap(env, _state(t1=2e-5))
        j = env["client"].get("/param-history/param-search?q=T1").get_json()
        assert j["ok"] is True
        hit = next(h for h in j["results"] if h["path"] == "qubits.qA1.T1")
        assert hit["changes"] == 2

    def test_typeahead_is_empty_for_a_short_query(self, env):
        _snap(env, _state())
        assert env["client"].get("/param-history/param-search?q=").get_json()[
            "results"] == []


class TestTheSurface:
    def test_trends_offers_the_changes_tab(self, env):
        _snap(env, _state())
        html = _get(env, "/param-history").get_data(as_text=True)
        assert "/param-history/changes" in html

    def test_changes_offers_the_way_back(self, env):
        _snap(env, _state())
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert 'hx-get="/param-history"' in html

    def test_direct_navigation_renders_a_full_page(self, env):
        _snap(env, _state())
        html = env["client"].get("/param-history/changes").get_data(as_text=True)
        assert "<html" in html.lower() and "ph-change" in html

    def test_a_path_opens_its_own_timeline(self, env):
        _snap(env, _state(t1=1e-5))
        _snap(env, _state(t1=2e-5))
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert "/field/history?path=" in html

    def test_no_snapshots_is_an_honest_empty_state(self, env):
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        assert "No snapshots yet" in html or "Nothing has changed" in html

    def test_a_broken_index_degrades_instead_of_500ing(self, env, monkeypatch):
        """A 500 on an HX-Request swaps a Werkzeug error page into the menu —
        the dashboard already refuses to do that and so must this."""
        _snap(env, _state(t1=2e-5))
        monkeypatch.setattr(type(env["hm"]), "leaf_change_groups",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        r = _get(env, "/param-history/changes")
        assert r.status_code == 200
        assert "Nothing has changed" in r.get_data(as_text=True) \
            or "No snapshots yet" in r.get_data(as_text=True)
