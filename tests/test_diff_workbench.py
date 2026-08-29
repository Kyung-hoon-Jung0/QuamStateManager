"""The diff workbench — two sources, four tabs, differences only (docs/84).

The Compare hub works and users do not use it: three different "Compare
selected" buttons led to three different surfaces, and the one they reached
asked for a comparison context, a tolerance preset and sometimes an entity
mapping before showing anything. What they ask for is what an IDE gives them —
show the differences and how big they are.

Two measured facts shape the engine and are pinned here:

* Neighbouring snapshots of a real chip differ in **4 nodes out of 15,285**, so
  "differences only" is a PRUNED document, not a filter over a full one.
* A first-vs-last comparison reports 2,758 differences of which only 117 are
  numeric — the rest are keys a regenerate added or removed. Rows therefore
  carry their change class, and numeric moves rank first.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import json_diff
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


def _state(t1=1.0e-5, f01=5.0e9, extra=None):
    q = {"id": "qA1", "T1": t1, "f_01": f01,
         "xy": {"operations": {"x180": {"amplitude": 0.1}}}}
    if extra:
        q.update(extra)
    return {"qubits": {"qA1": q}, "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# The engine
# ──────────────────────────────────────────────────────────────────────────


class TestFlatten:
    def test_leaves_use_the_house_path_grammar(self):
        flat, _t = json_diff.flatten({"a": {"b": [1, 2]}})
        assert flat == {"a.b.0": 1, "a.b.1": 2}

    def test_an_empty_container_is_a_leaf(self):
        """"this list became empty" is a difference worth showing."""
        flat, _t = json_diff.flatten({"a": [], "b": {}})
        assert flat == {"a": [], "b": {}}

    def test_an_empty_document_has_no_leaves(self):
        assert json_diff.flatten({}) == ({}, False)

    def test_the_cap_is_honest(self):
        flat, trunc = json_diff.flatten({"a": list(range(50))}, cap=10)
        assert trunc is True and len(flat) == 10


class TestDiffRows:
    def test_the_three_change_classes(self):
        a = {"same": 1, "moved": 2, "gone": 3}
        b = {"same": 1, "moved": 5, "new": 4}
        res = json_diff.diff_rows(a, b)
        by = {r["path"]: r for r in res["rows"]}
        assert by["moved"]["kind"] == "changed"
        assert by["new"]["kind"] == "added" and by["new"]["a"] is None
        assert by["gone"]["kind"] == "removed" and by["gone"]["b"] is None
        assert "same" not in by
        assert res["counts"] == {"changed": 1, "added": 1, "removed": 1,
                                 "same": 1, "total": 3, "numeric": 1}

    def test_the_delta_is_the_house_arithmetic(self):
        """Same numbers as the Review tray (docs/76) — grouped digits and a
        percentage, not %+.3e."""
        res = json_diff.diff_rows({"f": 5.1e9}, {"f": 5.2e9})
        d = res["rows"][0]["delta"]
        assert d["text"] == "+100,000,000"
        assert d["pct_text"] == "+1.96%"

    def test_a_meaningless_delta_is_absent_not_zero(self):
        res = json_diff.diff_rows({"s": "a", "b": True}, {"s": "z", "b": False})
        assert all(r["delta"] is None for r in res["rows"])

    def test_numeric_moves_rank_first_and_biggest_first(self):
        a = {"small": 100.0, "big": 100.0, "text": "x", "gone": 1}
        b = {"small": 101.0, "big": 200.0, "text": "y"}
        rows = json_diff.diff_rows(a, b)["rows"]
        assert [r["path"] for r in rows[:2]] == ["big", "small"]
        assert {r["path"] for r in rows[2:]} == {"text", "gone"}

    def test_a_cap_is_reported(self):
        a = {f"k{i}": i for i in range(50)}
        b = {f"k{i}": i + 1 for i in range(50)}
        res = json_diff.diff_rows(a, b, cap=10)
        assert len(res["rows"]) == 10 and res["truncated"] is True
        assert res["counts"]["changed"] == 50, "the COUNT is the true one"


class TestPruning:
    def test_pruned_documents_contain_exactly_the_differences(self):
        a = {"keep": {"x": 1, "y": 2}, "drop": {"z": 3}}
        b = {"keep": {"x": 9, "y": 2}, "drop": {"z": 3}}
        res = json_diff.build(a, b)
        assert res["tree_a"] == {"keep": {"x": 1}}
        assert res["tree_b"] == {"keep": {"x": 9}}

    def test_an_added_key_is_only_in_the_b_tree(self):
        res = json_diff.build({"a": 1}, {"a": 1, "b": 2})
        assert res["tree_a"] == {} and res["tree_b"] == {"b": 2}

    def test_a_removed_key_is_only_in_the_a_tree(self):
        res = json_diff.build({"a": 1, "b": 2}, {"a": 1})
        assert res["tree_a"] == {"b": 2} and res["tree_b"] == {}

    def test_list_elements_keep_their_index_paths(self):
        """A pruned list would either keep holes or renumber; an index-keyed
        object keeps the dot paths the rest of the app uses."""
        res = json_diff.build({"m": [1, 2, 3]}, {"m": [1, 9, 3]})
        assert res["tree_b"] == {"m": {"1": 9}}

    def test_rows_can_be_left_out_for_the_tree_view(self):
        res = json_diff.build({"a": 1}, {"a": 2}, with_rows=False)
        assert res["rows"] == [] and res["tree_b"] == {"a": 2}
        assert res["counts"]["changed"] == 1


# ──────────────────────────────────────────────────────────────────────────
# The surface
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    _write(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    hm = app.config["history_manager"]
    metas = []
    for t1 in (1.0e-5, 2.0e-5, 3.0e-5):
        _write(live, _state(t1=t1))
        metas.append(hm.check_and_snapshot(str(live), "manual", force=True))
    chip = Path(hm.resolve_chip_dir(live)[0]).name
    return {"app": app, "client": c, "live": live, "hm": hm, "chip": chip,
            "metas": metas,
            "refs": [f"hist:{chip}/{m.timestamp}" for m in metas],
            "working": f"working:{live}"}


def _get(env, url):
    return env["client"].get(url, headers={"HX-Request": "true"})


class TestTheDiffPage:
    def test_two_snapshots_show_only_what_differs(self, env):
        a, b = env["refs"][0], env["refs"][2]
        html = _get(env, f"/diff?a={a}&b={b}").get_data(as_text=True)
        assert "diff-tree-payload" in html
        payload = json.loads(html.split('id="diff-tree-payload" type="application/json">')[1]
                             .split("</script>")[0])
        assert payload["a"] == {"qubits": {"qA1": {"T1": 1.0e-5}}}
        assert payload["b"] == {"qubits": {"qA1": {"T1": 3.0e-5}}}

    def test_the_counts_lead_with_what_changed(self, env):
        html = _get(env, f"/diff?a={env['refs'][0]}&b={env['refs'][2]}").get_data(as_text=True)
        assert "1 changed" in html and "identical" in html

    def test_an_identical_pair_says_so(self, env):
        html = _get(env, f"/diff?a={env['refs'][0]}&b={env['refs'][0]}").get_data(as_text=True)
        assert "identical" in html

    def test_the_list_view_is_ranked_and_carries_deltas(self, env):
        html = _get(env, f"/diff?a={env['refs'][0]}&b={env['refs'][2]}&view=list") \
            .get_data(as_text=True)
        assert "diff-wb-list" in html and "val-delta" in html
        assert "qubits.qA1.T1" in html

    def test_a_bare_diff_opens_on_what_you_changed(self, env):
        """No picking required: the newest snapshot vs the loaded chip."""
        html = _get(env, "/diff").get_data(as_text=True)
        assert f'data-a="{env["refs"][2]}"' in html
        assert 'data-b="working:' in html

    def test_the_wiring_tab_diffs_wiring(self, env):
        w2 = json.loads(json.dumps(_WIRING))
        w2["ports"]["mw_outputs"]["con1"]["1"]["2"]["band"] = 3
        _write(env["live"], _state(t1=4.0e-5), w2)
        m = env["hm"].check_and_snapshot(str(env["live"]), "manual", force=True)
        newest = f"hist:{env['chip']}/{m.timestamp}"
        html = _get(env, f"/diff?a={env['refs'][2]}&b={newest}&tab=wiring") \
            .get_data(as_text=True)
        assert "1 changed" in html
        # ...and the state tab of the same pair does NOT show the wiring change
        html2 = _get(env, f"/diff?a={env['refs'][2]}&b={newest}&tab=state") \
            .get_data(as_text=True)
        assert "band" not in html2

    def test_tabs_with_nothing_behind_them_say_why(self, env):
        for tab in ("node", "data"):
            html = _get(env, f"/diff?a={env['refs'][0]}&b={env['refs'][1]}&tab={tab}") \
                .get_data(as_text=True)
            assert "not an experiment run" in html

    def test_an_unknown_tab_falls_back_to_state(self, env):
        r = _get(env, f"/diff?a={env['refs'][0]}&b={env['refs'][1]}&tab=nope")
        assert r.status_code == 200 and 'data-tab="state"' in r.get_data(as_text=True)

    def test_a_bad_ref_reports_instead_of_500ing(self, env):
        r = _get(env, f"/diff?a=nonsense:x&b={env['refs'][0]}")
        assert r.status_code == 200
        assert "Unrecognised" in r.get_data(as_text=True)

    def test_direct_navigation_renders_a_full_page(self, env):
        html = env["client"].get(f"/diff?a={env['refs'][0]}&b={env['refs'][1]}") \
            .get_data(as_text=True)
        assert "<html" in html.lower() and "diff-root" in html

    def test_the_json_endpoint_ships_the_pruned_trees(self, env):
        j = env["client"].get(
            f"/diff/data?a={env['refs'][0]}&b={env['refs'][2]}&tab=state").get_json()
        assert j["ok"] is True and j["counts"]["changed"] == 1
        assert j["tree_b"] == {"qubits": {"qA1": {"T1": 3.0e-5}}}


class TestOneFrontDoor:
    """Three "Compare selected" buttons used to lead three different places."""

    def test_two_snapshots_land_on_the_diff_oldest_first(self, env):
        a, b = env["metas"][2].timestamp, env["metas"][0].timestamp
        r = _get(env, f"/diff/snapshots?ts_a={a}&ts_b={b}")
        target = r.headers["HX-Redirect"]
        assert target.startswith("/diff?a=hist:")
        assert env["metas"][0].timestamp in target.split("&b=")[0], "oldest is A"

    def test_a_plain_browser_click_gets_a_real_redirect(self, env):
        r = env["client"].get(
            f"/diff/snapshots?ts_a={env['metas'][0].timestamp}"
            f"&ts_b={env['metas'][1].timestamp}")
        assert r.status_code == 302 and r.headers["Location"].startswith("/diff?")

    def test_missing_timestamps_land_on_the_diff_anyway(self, env):
        r = _get(env, "/diff/snapshots")
        assert r.headers["HX-Redirect"] == "/diff"

    def test_unresolvable_runs_land_on_the_diff_anyway(self, env):
        r = _get(env, "/diff/runs?uids=nope:1,nope:2")
        assert r.headers["HX-Redirect"] == "/diff"


class TestTheHubIsStillThere:
    """The hub owns the hard cases (mapping entities across devices). Demoting
    the front door must not break the legacy URLs that reach it."""

    def test_the_legacy_form_post_still_redirects(self, env):
        r = env["client"].post("/diff", data={"path_a": str(env["live"]),
                                              "path_b": str(env["live"])})
        assert r.status_code == 302
        assert r.headers["Location"].startswith("/compare-hub?src=")

    def test_a_hub_shaped_get_still_redirects(self, env):
        r = env["client"].get(f"/diff?src=ws:{env['live']}")
        assert r.status_code == 302
        assert r.headers["Location"].startswith("/compare-hub?")

    def test_the_diff_no_longer_links_to_it(self, env):
        # docs/141 4y: the hub is retired as a destination -- the workbench
        # carries no "Advanced" link into it any more
        html = _get(env, f"/diff?a={env['refs'][0]}&b={env['refs'][1]}") \
            .get_data(as_text=True)
        assert "/compare-hub?src=" not in html and "Advanced" not in html
