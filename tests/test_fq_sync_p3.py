"""w7 fq-sync P3: four pre-existing sync-surface defects (all reproduced on
e74b8ce as well), each pinned by the state that exposed it.

(a) the top-bar version chip stayed "unrecorded" after a capture: its memo
    was keyed on the live files alone, so a GET between a live write and the
    post-write capture pinned "no snapshot holds this" until the files moved;
(b) the Versions quick-diff compared an Apply's snapshot with a weeks-old run
    ("29049 values differ" after one edit): the changes-only filter hid the
    pre-apply row, whose zeros meant "no prior", once run ingests landed
    older rows beneath it;
(c) a freshly opened window toasted "Another State Manager window undid a
    change" for an undo made before it existed: its first tray (the full-page
    one) lacked the undo seq it seeds from;
(d) ``SM_RAM_VERIFY=1`` raised on every correct ``diff_cache.lookup`` hit (the
    lookup's "missing" compute was compared with the held diff).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 6.1e9}},
         "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return folder


@pytest.fixture
def app_client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return app, c


def _ctx(app):
    for ctx in app.config["contexts"].values():
        if isinstance(ctx, dict) and ctx.get("store") is not None:
            return ctx


# --------------------------------------------------------------------- (a)

def test_a_capture_that_lands_after_the_chip_looked_is_named(app_client, tmp_path):
    app, c = app_client
    c.post("/api/history/snapshot")                       # history exists
    live = tmp_path / "quam_state" / "state.json"
    doc = json.loads(live.read_text(encoding="utf-8"))
    doc["qubits"]["q1"]["f_01"] = 6.4e9
    live.write_text(json.dumps(doc), encoding="utf-8")    # a live write...
    body = c.get("/state/version").get_data(as_text=True)
    assert "unrecorded" in body                           # ...not captured yet
    c.post("/api/history/snapshot")                       # the capture lands; live files untouched
    body = c.get("/state/version").get_data(as_text=True)
    assert "unrecorded" not in body
    assert re.search(r"The live chip is on recorded version (\d{8}_\d{6}\S*)", body), body


# --------------------------------------------------------------------- (b)

def _meta(ts, *, kind, trigger, total, h):
    from quam_state_manager.core.history import SnapshotMeta
    return SnapshotMeta(timestamp=ts, trigger=trigger,
                        diff_summary={"added": 0, "removed": 0, "modified": total, "total": total},
                        new_experiments=[], source_path="", state_hash=h, kind=kind)


def test_a_no_prior_row_stays_visible_once_older_runs_land_beneath_it(tmp_path, monkeypatch):
    # S10 C3: snapshot filtering -> ledger versions, quick diff uses the adjacent recorded states.
    from tests.test_hub_versions import build_env, chip_state, apply_edit, ctx_of
    from quam_state_manager.web import routes
    env = build_env(tmp_path, [chip_state(t1=1e-5), chip_state(t1=2e-5)])
    live = env.live / "state.json"
    state = json.loads(live.read_text(encoding="utf-8"))
    state["qubits"]["qA1"]["T1"] = 3e-5
    live.write_text(json.dumps(state), encoding="utf-8")
    env.client.post("/api/history/snapshot")
    env.client.post("/state/sync", data={"mode": "discard"})
    apply_edit(env, "qubits.qA1.T1", 4e-5)
    with env.app.app_context():
        ctx = ctx_of(env)
        listing = routes._versions_read(ctx, env.app.config["history_manager"].list_snapshots(env.live))
    refs = [r["ts"] for r in listing["rows"]]
    assert len(refs) == 4 and listing["rows"][1]["badge"] == "seen"
    asked = []
    real = routes._version_quick_entries
    def checked(path, a, b):
        asked.append((a, b))
        return real(path, a, b)
    monkeypatch.setattr(routes, "_version_quick_entries", checked)
    body = env.client.get("/state/versions").get_data(as_text=True)
    shown = re.findall(r'sv-check" value="([^"]+)"', body)
    assert shown == refs
    assert asked == [(refs[1], refs[0])]
    assert "1 change" in body


def test_an_identical_copy_is_still_hidden(app_client, tmp_path):
    # S10 C3: snapshot zero-diff filter -> observed dedup, copies add no state rows.
    app, c = app_client
    live = tmp_path / "quam_state" / "state.json"
    for value in (6.1e9, 6.3e9, 6.3e9, 6.5e9):
        state = json.loads(live.read_text(encoding="utf-8"))
        state["qubits"]["q1"]["f_01"] = value
        live.write_text(json.dumps(state), encoding="utf-8")
        c.post("/api/history/snapshot")
    body = c.get("/state/versions").get_data(as_text=True)
    shown = re.findall(r'sv-check" value="([^"]+)"', body)
    assert len(shown) == 3 and all("_event-" in ref for ref in shown)
    # S10 walk (round 3): old -> new, the press on the identical copy writes no
    # snapshot at all (it only ever was a ~1 MB folder no list showed)
    assert len(app.config["history_manager"].list_snapshots(live.parent)) == 3
    assert "unchanged copy hidden" not in body


# --------------------------------------------------------------------- (c)

def test_the_full_page_tray_carries_the_newest_live_undo(app_client):
    app, c = app_client
    _ctx(app)["last_live_undo"] = {"seq": 3, "message": "Undone → live: qubits.q1.f_01"}
    page = c.get("/").get_data(as_text=True)
    m = re.search(r'<div id="pending-tray"[^>]*>', page, flags=re.S)
    assert m, "no tray on the page"
    assert 'data-live-undo-seq="3"' in m.group(0)
    # ...the same value the OOB tray renders, so the first swap is not "news"
    tray = c.get("/state/tray").get_data(as_text=True)
    assert 'data-live-undo-seq="3"' in tray


def test_a_window_seeds_what_it_has_seen_from_its_first_tray():
    import quam_state_manager
    src = (Path(quam_state_manager.__file__).parent / "web" / "static" / "app.js").read_text(encoding="utf-8")
    i = src.index("function _noticeForeignLiveUndo")
    body = src[i:i + 900]
    assert 'getAttribute("data-live-undo-seq")' in body
    assert "window._liveUndoSeen === undefined" in body


# --------------------------------------------------------------------- (d)

def test_a_lookup_hit_is_not_a_staleness_error_in_shadow_mode(monkeypatch):
    from quam_state_manager.core import diff_cache
    from quam_state_manager.core.differ import DiffEntry
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    diff_cache.PAIRS.clear()
    e = DiffEntry("qubits.q1.f_01", 6.1e9, 6.2e9, "modified")
    diff_cache.remember("A", "B", [e])
    assert diff_cache.lookup("A", "B") == [e]             # no StaleCacheError
    assert diff_cache.lookup("A", "C") is None
    diff_cache.remember("A", "B", [e])                    # re-remembered: still checked
    diff_cache.PAIRS.clear()
