""""What changed" — the Param History change feed (docs/83, A2).

The curated dashboard answers "how has T1 drifted". This answers the question
users actually arrive with: *what did that run change?* — over every numeric
parameter, which only became affordable once they were stored as change points.

The feed is paged by EVENT, not by row. That is not cosmetic: a regenerate
rewrites thousands of parameters in one event (2,716 measured on a real
chip), and a row-paged feed would spend its whole page on that one event and
hide every other. Each group therefore shows its TRUE count and only the first
rows, with the rest one click away.

S10 C3: the feed always reads the chip's change ledger. A Param History
capture of the open folder reaches it as an observed event; a capture with
trigger "experiment" is not imported (runs come only from linked data
folders). The old snapshot pages (``?at=``/``?before=`` as snapshot
timestamps) are gone: both now take the ledger's own references, read from
the page itself.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web import routes
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


def _groups(html: str) -> list[str]:
    """The Changes page's groups (raw HTML), newest first."""
    return html.split('<div class="ph-change-group">')[1:]


def _row(group: str, path: str) -> str:
    """The one row of *group* naming *path* (raw HTML)."""
    hits = [r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", group, re.S)
            if f">{path}</button>" in r]
    assert len(hits) == 1, (path, group[:2000])
    return hits[0]


class TestTheFeed:
    def test_a_change_shows_old_new_and_delta(self, env):
        # S10 C3: experiment capture named "05_T1 #7" -> two manual captures plus an
        # experiment capture that must add nothing, since runs come only from linked folders
        _snap(env, _state(t1=1.0e-5))
        _snap(env, _state(t1=2.0e-5))
        _snap(env, _state(t1=3.0e-5), trigger="experiment",
              experiment_name="05_T1", run_id=7)
        r = _get(env, "/param-history/changes")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        groups = _groups(html)
        row = _row(groups[0], "qubits.qA1.T1")
        assert re.search(r'class="ba-old">1e-05</span>\s*<span class="ba-arrow">'
                         r'.*?class="ba-new">2e-05</span>', row, re.S), row
        assert "val-delta" in row, "the delta must render, not just the two values"
        shown = "".join(groups)
        assert "3e-05" not in shown and "05_T1" not in shown and "#7" not in shown, \
            "an experiment capture is not a run of the ledger and is never imported"

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
        # S10 C3: snapshot feed's "first recorded" cell -> the ledger's first event names its
        # op in words and draws no arrow from an invented "before" and no delta
        _snap(env, _state())
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        (group,) = _groups(html)
        row = _row(group, "qubits.qA1.T1")
        assert 'class="ba-new">1e-05</span>' in row, row
        assert "first recorded" in row and "added" not in row, row
        assert "ba-arrow" not in row and "ba-old" not in row and "val-delta" not in row, row


class TestPagingBySnapshot:
    def test_a_big_snapshot_is_capped_with_its_true_count(self, env):
        """The regenerate case: one event rewrites far more parameters than
        a page can hold. The group must still say how many."""
        # S10 C3: "Show all ... from this snapshot" -> the ledger event's group shows the
        # first _CHANGES_ROWS rows, its TRUE count, "Show all N from this event" and the rest
        big = _state()
        big["qubits"]["qA1"]["extras_numbers"] = {f"k{i}": i for i in range(80)}
        _snap(env, _state())
        _snap(env, big)
        html = _get(env, "/param-history/changes").get_data(as_text=True)
        group = _groups(html)[0]
        assert group.count('class="ph-change-path"') == routes._CHANGES_ROWS
        assert "80 parameters" in group
        assert "Show all 80 from this event" in group
        assert f"and {80 - routes._CHANGES_ROWS} more" in group

    def test_at_opens_one_snapshot_in_full(self, env):
        # S10 C3: ?at=<snapshot timestamp> -> ?at=<event id> read from the group's own
        # "Show all" link; it opens THAT event (not the newest) whole; an old link is refused
        big = _state()
        big["qubits"]["qA1"]["extras_numbers"] = {f"k{i}": i for i in range(80)}
        _snap(env, _state())
        m = _snap(env, big)
        _snap(env, _state(t1=2.0e-5))                 # a newer event that is not the big one
        page = _get(env, "/param-history/changes").get_data(as_text=True)
        big_group = next(g for g in _groups(page) if "80 parameters" in g)
        link = re.search(r'hx-get="(/param-history/changes\?at=\d+)"', big_group)
        assert link, big_group[-1500:]
        html = _get(env, link.group(1)).get_data(as_text=True)
        (group,) = _groups(html)
        assert group.count('class="ph-change-path"') == 80
        assert "80 parameters" in group and "Show all" not in group
        assert "qubits.qA1.T1" not in html, "?at= opens that event, never the newest"
        assert "Back to all changes" in html and "Load older changes" not in html
        old = _get(env, f"/param-history/changes?at={m.timestamp}")
        assert old.status_code == 400 and "open Changes again" in old.get_data(as_text=True)

    def test_older_page_does_not_repeat_the_newest(self, env, monkeypatch):
        """Each event moves a DIFFERENT parameter, so the page boundary is
        visible in the content rather than in a timestamp string."""
        # S10 C3: ?before=<snapshot timestamp> -> the ledger's own "Load older changes"
        # cursor, two events a page: the pages split the four events, none repeated
        monkeypatch.setattr(routes, "_CHANGES_SNAPS", 2)
        for i in range(4):
            st = _state()
            st["qubits"]["qA1"][f"marker{i}"] = float(i)
            _snap(env, st)
        newest = _get(env, "/param-history/changes").get_data(as_text=True)
        assert len(_groups(newest)) == 2
        assert "marker3" in newest and "marker0" not in newest
        link = re.search(r'hx-get="(/param-history/changes\?before=[^"&]+)"', newest)
        assert link, "an older page is offered"
        older = _get(env, link.group(1)).get_data(as_text=True)
        assert len(_groups(older)) == 2
        assert "marker3" not in older and "marker2" not in older
        assert "marker1" in older and "marker0" in older
        assert "Load older changes" not in older, "the oldest page offers nothing older"


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
        # S10 C3: "No snapshots yet" from the snapshot path -> the empty ledger's own
        # sentence, with no group and none of the snapshot path's words
        r = _get(env, "/param-history/changes")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "No change is recorded in this chip's change ledger yet." in html
        assert "ph-change-group" not in html
        assert "No snapshots yet" not in html and "Nothing has changed" not in html

    def test_a_broken_index_degrades_instead_of_500ing(self, env, monkeypatch):
        """A 500 on an HX-Request swaps a Werkzeug error page into the menu --
        the dashboard already refuses to do that and so must this."""
        # S10 C3: broken leaf index -> "nothing has changed" becomes a broken ledger read ->
        # the terminal "could not be read" line, no rows, no re-ask (never a denial of changes)
        from quam_state_manager.core import value_history
        _snap(env, _state(t1=2e-5))

        def boom(*_a, **_k):
            raise RuntimeError("boom")
        monkeypatch.setattr(value_history, "read", boom)
        r = _get(env, "/param-history/changes")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        # S10 walk: old -> new, the reason in plain words (never the raw "(unreadable)")
        assert "The change history file could not be read." in html
        assert "Nothing older is shown in its place." in html
        assert "ph-change-group" not in html and "qubits.qA1.T1" not in html
        assert "Nothing has changed" not in html and "No snapshots yet" not in html
        assert "load delay:" not in html, "a terminal error is not asked again"
