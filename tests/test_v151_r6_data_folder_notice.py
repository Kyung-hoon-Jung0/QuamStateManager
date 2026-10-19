"""v1.5.1 (r6, P2-3): the "Data folder not reachable" notice follows the folder.

The notice was the list taken when the chip was opened: after the folder was
back and re-pointed it stayed on every page and through reloads until the
project was reopened. Pinned here:

* each render asks again through the bounded folder rule (memoised: back to
  back renders do not ask twice), and a folder found again is paired as
  opening the chip pairs it -- the notice goes;
* a re-point (the sidebar's add / re-point) asks afresh and removes the notice
  on the page at once (an out-of-band delete in the same answer);
* a folder that is still not found keeps its notice.
"""
import json

import pytest

from quam_state_manager.core import folder_probe
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_NOTICE = "Data folder not reachable"
_OOB_DELETE = '<div id="chip-name-banner" hx-swap-oob="delete"></div>'


@pytest.fixture(autouse=True)
def _fresh_probes():
    folder_probe.forget()
    yield
    folder_probe.forget()


def _open(tmp_path, data_folder):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "f_01": 5.0e9}}, "qubit_pairs": {},
        "extras": {"chip_name": "deviceC", "data_folder": str(data_folder)},
    }), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(
        {"wiring": {}, "network": {"host": "10.0.0.1", "cluster_name": "C"}}), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = app.config["contexts"][app.config["active_context"]]
    return app, client, ctx


def _back(folder):
    (folder / "2026-09-07").mkdir(parents=True)


def test_the_notice_goes_on_the_next_render_once_the_folder_is_back(tmp_path, monkeypatch):
    data = tmp_path / "data_moved_away"
    app, client, ctx = _open(tmp_path, data)
    assert _NOTICE in client.get("/qubits").get_data(as_text=True)
    _back(data)
    monkeypatch.setattr(routes, "_DANGLING_RECHECK_S", 0.0, raising=False)  # the memo ran out
    html = client.get("/qubits").get_data(as_text=True)
    assert _NOTICE not in html and "cnb-dangling" not in html
    # paired exactly as opening the chip pairs it
    assert ctx["extras_data_dangling"] == []
    assert ctx["extras_data_roots"] == [str(data)]
    assert str(data) in {str(p) for p in app.config["workspace"].root_folders}
    assert _NOTICE not in client.get("/pairs").get_data(as_text=True)


def test_renders_reuse_the_answer_within_the_memo(tmp_path, monkeypatch):
    data = tmp_path / "not_here"
    _app, client, _ctx = _open(tmp_path, data)
    asked = []
    real = folder_probe._probe
    monkeypatch.setattr(folder_probe, "_probe", lambda p: (asked.append(p), real(p)))
    for _ in range(3):
        assert _NOTICE in client.get("/qubits").get_data(as_text=True)
    assert asked == [str(data)], asked


def test_a_re_point_removes_the_notice_at_once(tmp_path):
    data = tmp_path / "data_moved_away"
    app, client, ctx = _open(tmp_path, data)
    assert _NOTICE in client.get("/qubits").get_data(as_text=True)    # "not found" memoised
    _back(data)
    r = client.post("/workspace/add", data={"folder": str(data)})
    assert r.status_code == 200
    assert _OOB_DELETE in r.get_data(as_text=True)
    assert ctx["extras_data_dangling"] == []
    assert _NOTICE not in client.get("/qubits").get_data(as_text=True)


def test_a_folder_still_not_found_keeps_its_notice(tmp_path):
    data = tmp_path / "still_gone"
    other = tmp_path / "other_runs"
    _back(other)
    _app, client, ctx = _open(tmp_path, data)
    r = client.post("/workspace/add", data={"folder": str(other)})
    assert r.status_code == 200
    assert _OOB_DELETE not in r.get_data(as_text=True)
    assert ctx["extras_data_dangling"] == [str(data)]
    assert _NOTICE in client.get("/qubits").get_data(as_text=True)
