"""The /pulse-class doors: new draft, load, in-SM save, outside edit by poll,
the two-editor conflict, the status phrases and the preview env guard."""
from __future__ import annotations

import os

import pytest

from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_watch as W


@pytest.fixture
def client(tmp_path, monkeypatch):
    from quam_state_manager.core import config_generator
    from quam_state_manager.web.app import create_app
    monkeypatch.setattr(config_generator, "discover_envs", lambda refresh=False: [])
    W._WATCHES.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def _new(client, cls="FlatPulse"):
    r = client.post("/pulse-class/new", json={"cls": cls})
    assert r.status_code == 200, r.get_json()
    p = client.get(f"/pulse-class/load?cls={cls}").get_json()
    assert p["ok"]
    return p


def test_page_renders_the_list_and_the_edit_view(client):
    html = client.get("/pulse-class").get_data(as_text=True)
    assert "data-pce-new" in html and "pulse-class-edit.js" in html
    _new(client)
    html = client.get("/pulse-class?cls=FlatPulse").get_data(as_text=True)
    for hook in ("data-pce-code", "data-pce-status", "data-pce-wave", "data-pce-conflict"):
        assert hook in html


def test_new_twice_is_refused(client):
    _new(client)
    assert client.post("/pulse-class/new", json={"cls": "FlatPulse"}).status_code == 409


def test_load_protects_exactly_the_region_markers(client):
    p = _new(client)
    a, b = p["info"]["protect"]
    from quam_state_manager.core import pulse_class_template as T
    assert p["text"][a:].startswith(T.REGION_BEGIN)
    assert p["text"][:b].endswith(T.REGION_END)


def test_save_with_a_stale_base_is_a_conflict_and_writes_nothing(client):
    p = _new(client)
    path = p["path"]
    outside = p["text"].replace("0.1", "0.4").encode()
    with open(path, "wb") as f:
        f.write(outside)
    mine = p["text"].replace("0.1", "0.7")
    r = client.post("/pulse-class/save", json={"cls": "FlatPulse", "text": mine, "base_sha": p["sha"]})
    assert r.status_code == 409
    c = r.get_json()["conflict"]
    assert {x["keep"] for x in c["choices"]} == {"sm", "file"}
    assert open(path, "rb").read() == outside
    r = client.post("/pulse-class/save", json={"cls": "FlatPulse", "text": mine, "base_sha": p["sha"], "keep": "sm"})
    assert r.status_code == 200 and open(path, "rb").read() == mine.encode()
    assert r.get_json()["status"] == "Draft, not saved"


def test_outside_edit_reaches_the_poll_after_it_settles(client):
    p = _new(client)
    path = p["path"]
    with open(path, "wb") as f:
        f.write(p["text"].replace("0.1", "0.4").encode())
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    first = client.get("/pulse-class/poll?cls=FlatPulse&dirty=0").get_json()
    assert first["status"] == "settling"
    second = client.get("/pulse-class/poll?cls=FlatPulse&dirty=1").get_json()
    assert second["status"] == "changed" and second["info"]["spec"]["fields"][0]["default"] == 0.4
    assert "conflict" in second            # the pane had unsaved changes
    assert second["status_line"] == "Draft, not saved"


def test_save_file_moves_the_status_phrase(client, tmp_path):
    _new(client)
    target = tmp_path / "flat_pulse.py"
    r = client.post("/pulse-class/save-file", json={"cls": "FlatPulse", "target": str(target)}).get_json()
    assert r["ok"] and r["status"] == f"Saved to {target}, same as draft"


def test_preview_refuses_an_unknown_interpreter(client):
    _new(client)
    r = client.post("/pulse-class/preview", json={"cls": "FlatPulse", "python": "C:/nowhere/python.exe"})
    assert r.status_code == 400 and not r.get_json()["ok"]
