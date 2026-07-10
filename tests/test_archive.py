"""Tests for the State Archive / Bookmark badge (feedback #3).

The badge force-captures a manual, PINNED snapshot annotated with a tag (label)
+ note, into the same store as State History. These pin: the snapshot is created
with the right metadata; a later label-only edit does NOT wipe the note (the
annotate_snapshot ``note`` sentinel); and the tag surfaces in State History.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _state() -> dict:
    return {"qubits": {"qA1": {"id": "qA1", "f_01": 6.25e9}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _wiring() -> dict:
    return {"network": {"host": "1.1.1.1"}}


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    inst = tmp_path / "_i"
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    c._inst = str(inst)  # type: ignore[attr-defined]
    return c


def _find_meta(inst: str, needle: str):
    """Return (meta_dict, timestamp) of the snapshot whose note/label match needle."""
    for p in glob.glob(os.path.join(inst, "history", "**", "meta.json"), recursive=True):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        if needle == (d.get("note") or "") or needle == (d.get("label") or ""):
            return d, Path(p).parent.name
    return None, None


class TestArchiveBadge:
    def test_creates_pinned_snapshot_with_tag_and_note(self, client):
        r = client.post("/state/archive",
                        data={"tag": "before CZ retune", "note": "qA1 looking good"})
        body = r.get_data(as_text=True)
        assert "archive-ok" in body and "Bookmarked" in body
        meta, _ts = _find_meta(client._inst, "qA1 looking good")
        assert meta is not None, "archive snapshot not found in the history store"
        assert meta["label"] == "before CZ retune"
        assert meta["note"] == "qA1 looking good"
        assert meta["pinned"] is True
        assert meta["trigger"] == "manual"

    def test_no_context_is_graceful_not_500(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        r = app.test_client().post("/state/archive", data={"tag": "x"})
        assert r.status_code == 200
        assert "archive-err" in r.get_data(as_text=True)

    def test_relabel_preserves_note(self, client):
        # the annotate_snapshot note-sentinel: renaming a bookmark's tag via the
        # State History label route must NOT clobber its note.
        client.post("/state/archive", data={"tag": "t1", "note": "keepme"})
        _meta, ts = _find_meta(client._inst, "keepme")
        assert ts is not None
        client.post(f"/state-history/{ts}/label", data={"label": "t2", "pinned": "1"})
        meta2, _ = _find_meta(client._inst, "keepme")
        assert meta2 is not None
        assert meta2["label"] == "t2" and meta2["note"] == "keepme"

    def test_tag_surfaces_in_state_history(self, client):
        client.post("/state/archive", data={"tag": "snapshot-alpha", "note": "n"})
        body = client.get("/state-history", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "snapshot-alpha" in body

    def test_empty_tag_still_bookmarks(self, client):
        r = client.post("/state/archive", data={"tag": "", "note": ""})
        assert "archive-ok" in r.get_data(as_text=True)

    def test_dirty_bookmark_warns_edits_not_included(self, client):
        # audit P2: a bookmark captures LIVE; if the working copy holds unapplied edits,
        # say so (don't silently capture live while the user views their edits).
        client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "6.3e9"}]})
        body = client.post("/state/archive", data={"tag": "mid-edit"}).get_data(as_text=True)
        assert "archive-ok" in body and "archive-warn" in body
        meta, _ = _find_meta(client._inst, "mid-edit")  # note also records it
        # the note carries the captured-live disclosure
        m2 = None
        import glob as _g, json as _j
        for p in _g.glob(__import__("os").path.join(client._inst, "history", "**", "meta.json"), recursive=True):
            d = _j.loads(open(p).read())
            if d.get("label") == "mid-edit":
                m2 = d
        assert m2 is not None and "unapplied" in (m2.get("note") or "")


class TestSnapshotMetaForwardCompat:
    def test_unknown_meta_key_does_not_drop_the_snapshot(self, tmp_path):
        from quam_state_manager.core.history import HistoryManager
        hm = HistoryManager(tmp_path / "instance")
        chip = tmp_path / "data" / "chipA" / "quam_state"
        (chip).mkdir(parents=True)
        (chip / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (chip / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
        meta = hm.check_and_snapshot(chip, "manual", force=True)
        assert meta is not None
        # inject a forward/foreign key into the meta.json
        meta_p = hm._history_dir(chip) / meta.timestamp / "meta.json"
        d = json.loads(meta_p.read_text())
        d["some_future_field"] = {"x": 1}
        meta_p.write_text(json.dumps(d))
        hm.clear_cache()
        snaps = hm.list_snapshots(chip)
        # the snapshot must STILL appear (degrade the unknown key, don't drop the row)
        assert any(s.timestamp == meta.timestamp for s in snaps)

    def test_archive_response_carries_local_time_span(self, client):
        # C1/C2: the success span includes the snapshot time as a ts-local span — it
        # shows the user's local time AND makes a 2nd save with the same tag distinct.
        r = client.post("/state/archive", data={"tag": "t", "note": "n"})
        body = r.get_data(as_text=True)
        assert "archive-ok" in body
        assert 'class="ts-local"' in body and "data-utc=" in body


class TestLocalTimeFilter:
    def test_ts_local_filter_renders_span_with_utc_iso(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        f = app.jinja_env.filters["ts_local"]
        out = str(f("20260516_112511"))
        assert 'class="ts-local"' in out
        assert 'data-utc="2026-05-16T11:25:11Z"' in out
        assert "2026-05-16 11:25:11 UTC" in out          # graceful fallback text
        # format_ts (the attribute-site filter) is untouched + still plain text
        assert app.jinja_env.filters["format_ts"]("20260516_112511") == "2026-05-16 11:25:11 UTC"

    def test_ts_local_tolerates_iso_instant(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        out = str(app.jinja_env.filters["ts_local"]("2026-05-16T11:25:11.123Z"))
        assert 'data-utc="2026-05-16T11:25:11Z"' in out

    def test_applylocaltimes_in_app_js(self):
        js = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
              / "static" / "app.js").read_text(encoding="utf-8")
        assert "function applyLocalTimes" in js and "toLocaleString()" in js
        assert ".ts-local[data-utc]:not([data-localized])" in js
