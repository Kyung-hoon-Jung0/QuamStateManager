"""w9/pulsesall: the virtual per-page "All" view of the Pulses table.

A big library's "All" ships each row's TEXT once, keyed by a digest of exactly
that text, and the client renders only the rows near the viewport; a change
re-lists (path, digest) and fetches only the rows whose digest moved. These
pins hold the server half of that contract:

* the payload is the table's own row markup (``_pulse_row.html``), thumbnails
  left as sized placeholders, and nothing is drawn server-side;
* /pulses/vids, /pulses/vrows, /pulses/sparks and /pulse/row?vt=1 all speak
  the SAME digest for the same row;
* after a value edit only the touched rows' digests move, after a structural
  change the model equals a COLD render (a fresh process) row for row;
* the paged views and a library below the floor render exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_index as pi
from quam_state_manager.web.app import create_app

from tests.test_pulses_routes import _make_state, _make_wiring

XY = "qubits.qA1.xy.operations"


def _app(tmp_path, *, floor=1, name="chip"):
    folder = tmp_path / name
    if not folder.exists():
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / ("_inst_" + name)))
    if floor is not None:
        app.config["PULSES_VIRTUAL_MIN"] = floor
    client = app.test_client()
    assert client.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return app, client, folder


@pytest.fixture
def vt(tmp_path, monkeypatch):
    monkeypatch.setenv("SM_RAM_VERIFY", "1")   # every memo hit shadow-checked
    return _app(tmp_path)


_VDATA = re.compile(r'<script type="application/json" id="pulses-vdata">(.*?)</script>', re.S)


def _vdata(html: str) -> dict:
    m = _VDATA.search(html)
    assert m, "no #pulses-vdata in the page"
    return json.loads(m.group(1))


def _vids(client, qs="") -> dict:
    r = client.get("/pulses/vids" + qs)
    assert r.status_code == 200 and r.mimetype == "application/json"
    return r.get_json()


def test_all_ships_the_rows_as_data_and_draws_nothing(vt):
    app, client, _ = vt
    html = client.get("/pulses?per_page=0").data.decode()
    assert "<tbody data-pulses-virtual=\"1\">" in html
    d = _vdata(html)
    rows = d["rows"]
    assert d["n"] == len(rows) and len(rows) >= 6
    # nothing drawn server-side: every thumbnail is a sized placeholder
    table = html.split('id="pulses-table"', 1)[1].split("</table>", 1)[0]
    assert "<svg" not in table and "pulse-spark" not in table.split("<tbody", 1)[1]
    for path, ver, text in rows:
        assert text.startswith("<tr ") and text.endswith("</tr>")
        assert f'data-pulse-path="{path}"' in text
        assert ver == hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()
        assert "<svg" not in text
    by = {r[0]: r[2] for r in rows}
    # a pulse: the placeholder; an alias: its target text, drawn as before
    assert 'data-spark-lazy="1"><span class="pulse-spark-pending"' in by[f"{XY}.x180_DragCosine"]
    assert "data-spark-lazy" not in by[f"{XY}.x180"]
    assert "pulse-alias-target" in by[f"{XY}.x180"]
    # the rows payload of an htmx swap is the same model
    part = client.get("/pulses?rows=1&per_page=0", headers={"HX-Request": "true"}).data.decode()
    assert _vdata(part)["rows"] == rows


def test_vids_lists_digests_only_and_matches_the_page(vt):
    app, client, _ = vt
    full = _vdata(client.get("/pulses?rows=1&per_page=0").data.decode())
    ids = _vdata(client.get("/pulses?rows=1&per_page=0&vids=1").data.decode())
    assert [r[:2] for r in full["rows"]] == ids["rows"]
    assert all(len(r) == 2 for r in ids["rows"])
    assert _vids(client)["rows"] == ids["rows"]
    # the filter rides along, and is the page's own filter
    x = _vids(client, "?q=x180")
    page = _vdata(client.get("/pulses?rows=1&per_page=0&q=x180").data.decode())
    assert x["rows"] == [r[:2] for r in page["rows"]] and 0 < len(x["rows"]) < len(ids["rows"])
    assert _vids(client, "?channel=resonator")["rows"] == [
        r[:2] for r in _vdata(client.get(
            "/pulses?rows=1&per_page=0&channel=resonator").data.decode())["rows"]]
    none = _vids(client, "?q=zzzz_nothing")
    assert none["rows"] == [] and "No pulses found" in none["empty"]


def test_every_door_speaks_the_same_digest(vt):
    app, client, _ = vt
    ids = dict(_vids(client)["rows"])
    p = f"{XY}.x180_DragCosine"
    r = client.get(f"/pulse/row?path={p}&vt=1")
    assert r.headers.get("X-Pulse-Ver") == ids[p]
    plain = client.get(f"/pulse/row?path={p}")
    assert "X-Pulse-Ver" not in plain.headers           # the paged table's door is unchanged
    assert plain.data == r.data
    vr = client.post("/pulses/vrows", json={"paths": [p, f"{XY}.gone_for_good"]}).get_json()
    assert vr["rows"][0][:2] == [p, ids[p]] and vr["gone"] == [f"{XY}.gone_for_good"]
    sp = client.post("/pulses/sparks", json={"paths": [p, f"{XY}.x180"]}).get_json()
    assert sp["rows"][p][0] == ids[p]
    assert '<svg class="pulse-spark"' in sp["rows"][p][1]
    # the full row it answers IS the row /pulse/row renders
    assert sp["rows"][p][1] == plain.data.decode().strip()


def test_a_value_edit_moves_only_the_rows_it_touched(vt):
    app, client, _ = vt
    before = dict(_vids(client)["rows"])
    r = client.post("/pulse/edit", data={
        "path": f"{XY}.x180_DragCosine", "dot_path": f"{XY}.x180_DragCosine.amplitude",
        "mode": "value", "value": "0.2468"})
    assert r.status_code == 200
    touched = set(json.loads(r.headers["HX-Trigger"])["pulses-rows-changed"]["paths"])
    after = dict(_vids(client)["rows"])
    moved = {p for p in after if after[p] != before.get(p)}
    assert moved and moved <= touched, (moved, touched)
    assert f"{XY}.x180_DragCosine" in moved
    text = client.post("/pulses/vrows", json={"paths": [f"{XY}.x180_DragCosine"]}).get_json()
    assert "0.2468" in text["rows"][0][2]


def _cold_model(tmp_path, folder_state, folder_wiring, qs=""):
    """A FRESH process's model of the same content: the reference."""
    cold = tmp_path / "cold_chip"
    if cold.exists():
        import shutil
        shutil.rmtree(cold)
    cold.mkdir()
    (cold / "state.json").write_text(json.dumps(folder_state), encoding="utf-8")
    (cold / "wiring.json").write_text(json.dumps(folder_wiring), encoding="utf-8")
    app2 = create_app(testing=True, instance_path=str(tmp_path / "_inst_cold"))
    app2.config["PULSES_VIRTUAL_MIN"] = 1
    c2 = app2.test_client()
    c2.post("/load", data={"folder": str(cold)})
    return _vdata(c2.get("/pulses?rows=1&per_page=0" + qs).data.decode())["rows"]


def _working(client, app):
    with app.test_request_context():
        from quam_state_manager.web import routes
        st = routes._store()
        return st.state, st.wiring


def test_after_structural_changes_the_model_equals_a_cold_render(vt, tmp_path):
    app, client, _ = vt
    first = dict(_vids(client)["rows"])
    assert client.post("/api/pulse/duplicate", data={
        "path": f"{XY}.x90_DragCosine", "new_name": "x90_v2"}).status_code == 200
    assert client.post("/api/pulse/rename", data={
        "path": f"{XY}.saturation", "new_name": "sat_renamed"}).status_code == 200
    assert client.post("/api/pulse/delete", data={"path": f"{XY}.mystery"}).status_code == 200
    ids = _vids(client)["rows"]
    paths = [p for p, _ in ids]
    assert f"{XY}.x90_v2" in paths and f"{XY}.sat_renamed" in paths
    assert f"{XY}.saturation" not in paths and f"{XY}.mystery" not in paths
    # what the client would fetch = exactly the rows whose digest moved
    moved = [p for p, v in ids if first.get(p) != v]
    assert set(moved) >= {f"{XY}.x90_v2", f"{XY}.sat_renamed"}
    got = client.post("/pulses/vrows", json={"paths": paths}).get_json()["rows"]
    state, wiring = _working(client, app)
    cold = _cold_model(tmp_path, state, wiring)
    assert got == cold, "the warm model differs from a fresh process"
    # and the memo carried the unchanged rows across the rebuilds
    with app.test_request_context():
        from quam_state_manager.web import routes
        memo = routes._pulse_vt_memo()
        assert memo.stats["carry"] > 0, memo.stats


def test_undo_restores_the_digest(vt):
    app, client, _ = vt
    before = dict(_vids(client)["rows"])
    client.post("/pulse/edit", data={
        "path": f"{XY}.x180_DragCosine", "dot_path": f"{XY}.x180_DragCosine.alpha",
        "mode": "value", "value": "-0.5"})
    mid = dict(_vids(client)["rows"])
    assert mid[f"{XY}.x180_DragCosine"] != before[f"{XY}.x180_DragCosine"]
    tray = client.get("/state/tray").data.decode()
    seen = re.search(r'data-edit-seq="([^"]*)"', tray)
    r = client.post("/undo", data={"seen_changes": seen.group(1) if seen else ""})
    assert r.status_code == 200, r.data[:300]
    assert dict(_vids(client)["rows"]) == before


def test_the_paged_views_and_a_small_library_render_as_before(tmp_path):
    app, client, _ = _app(tmp_path, floor=None)        # the shipped floor (400)
    for qs in ("?per_page=0", "?per_page=50", "?per_page=25", "?rows=1&per_page=0"):
        html = client.get("/pulses" + qs).data.decode()
        assert "pulses-vdata" not in html and "data-pulses-virtual" not in html, qs
        assert '<svg class="pulse-spark"' in html, qs          # drawn server-side, as before
    app.config["PULSES_VIRTUAL_MIN"] = 1
    for qs in ("?per_page=50", "?per_page=100", "?rows=1&per_page=25"):
        html = client.get("/pulses" + qs).data.decode()
        assert "pulses-vdata" not in html and "data-pulses-virtual" not in html, qs


def test_the_virtual_row_is_the_table_row_but_for_its_thumbnail(vt):
    """The text a virtual row renders IS the paged table's row with the
    waveform cell's content swapped for the placeholder -- the same template,
    not a second one."""
    app, client, _ = vt
    ids = {p: t for p, _, t in _vdata(client.get("/pulses?rows=1&per_page=0").data.decode())["rows"]}
    paged = client.get("/pulses?rows=1&per_page=50").data.decode()
    cell = re.compile(r'(<td class="pulse-spark-cell"[^>]*>).*?(</td>)', re.S)
    for p, text in ids.items():
        m = re.search(r'(<tr class="clickable-row" data-pulse-path="%s".*?</tr>)' % re.escape(p), paged, re.S)
        assert m, p
        a = cell.sub(r"\1\2", m.group(1)).replace(' data-spark-lazy="1"', "")
        b = cell.sub(r"\1\2", text).replace(' data-spark-lazy="1"', "")
        assert a == b, p


# ---- RowMemo ---------------------------------------------------------------

def test_rowmemo_identity_equality_and_change(monkeypatch):
    monkeypatch.delenv("SM_RAM_VERIFY", raising=False)
    m = pi.RowMemo()
    calls = []

    def f(r):
        calls.append(r["path"])
        return (r["path"], r["amplitude"])

    a = {"path": "p", "amplitude": 1}
    assert m.get("k", a, f) == ("p", 1)
    assert m.get("k", a, f) == ("p", 1)                  # identity hit
    b = {"path": "p", "amplitude": 1}                    # an equal row from a rebuild
    assert m.get("k", b, f) == ("p", 1) and m.stats["carry"] == 1
    c = {"path": "p", "amplitude": 2}                    # a changed row
    assert m.get("k", c, f) == ("p", 2)
    assert calls == ["p", "p"]
    assert m.get("other", c, f) == ("p", 2)              # kinds are separate
    small = pi.RowMemo(max_entries=2)
    for i in range(5):
        small.get("k", {"path": str(i)}, lambda r: r["path"])
    assert len(small) <= 2


def test_rowmemo_verify_mode_catches_an_impure_compute(monkeypatch):
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    from quam_state_manager.core.ramcache import StaleCacheError
    m = pi.RowMemo()
    box = {"n": 0}

    def impure(r):
        box["n"] += 1
        return box["n"]

    row = {"path": "p"}
    m.get("k", row, impure)
    with pytest.raises(StaleCacheError):
        m.get("k", row, impure)


# ---- the client half (jsdom): tests/pulses_vt_selfcheck.cjs --------------------

def test_the_virtual_view_selfcheck():
    """pulses-vt.js + its app.js hand-overs under jsdom with a geometry shim:
    a window of the model is rendered, sort / keyboard / compare selection
    work on the MODEL, an off-screen named row is patched (not "missing"),
    ``pulses-changed`` becomes the in-place refresh fetching only the moved
    rows, swaps ask for digests only, thumbnails follow the stamp."""
    import shutil
    import subprocess
    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    if not (root / "node_modules" / "jsdom").exists():
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(root / "tests" / "pulses_vt_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=300, cwd=str(root))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "all ok" in r.stdout, out
    for pin in ("only a window is rendered",
                "D: col 7 x2 sorts the model as the table sorts its rows",
                "E: 150 x ArrowDown selects the 150th row of the model",
                "F: re-rendered checked",
                "G: no whole-table re-list for rows the view holds",
                "H: fetched exactly the created + the changed row",
                "H: the row at the top of the view kept its place",
                "I: the All view asks for digests only",
                "J: a moved stamp re-asks every rendered thumbnail",
                "K: a history restore re-fetches the rows through the table"):
        assert "ok - " + pin in r.stdout, pin
