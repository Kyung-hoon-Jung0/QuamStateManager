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
    assert c["file_sha"] == D.sha256_bytes(outside)
    r = client.post("/pulse-class/save", json={"cls": "FlatPulse", "text": mine, "base_sha": p["sha"],
                                               "keep": "sm", "file_sha": c["file_sha"], "hand_edit": True})
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
    _preview_ok(client)
    target = tmp_path / "flat_pulse.py"
    r = client.post("/pulse-class/save-file", json={"cls": "FlatPulse", "target": str(target)}).get_json()
    assert r["ok"] and r["status"] == f"Saved to {target}, same as draft"


def test_preview_refuses_an_unknown_interpreter(client):
    _new(client)
    r = client.post("/pulse-class/preview", json={"cls": "FlatPulse", "python": "C:/nowhere/python.exe"})
    assert r.status_code == 400 and not r.get_json()["ok"]


# Review round 1 fixes

def _preview_ok(client, cls="FlatPulse"):
    """A good preview of the current draft bytes, without a lab env."""
    inst = client.application.instance_path
    from quam_state_manager.web import routes
    with client.application.test_request_context():
        scope = routes._ctx_chip_key(routes._active_ctx()) or "no-chip"

    def run(py, req, timeout):
        data = open(req["draft"], "rb").read()
        return {"ok": True, "samples": [0.0], "n": 1, "fields": [], "sha256": D.sha256_bytes(data)}
    assert D.preview("py", inst, scope, cls, runner=run)["ok"]


def test_save_file_over_an_existing_file_is_a_question_then_a_confirmed_replace(client, tmp_path):
    p = _new(client)
    _preview_ok(client)
    t = tmp_path / "existing.py"
    t.write_bytes(b"# a module of the lab\n")
    r = client.post("/pulse-class/save-file", json={"cls": "FlatPulse", "target": str(t)})
    assert r.status_code == 409
    c = r.get_json()["confirm"]
    assert c["path"] == str(t) and t.read_bytes() == b"# a module of the lab\n"
    r = client.post("/pulse-class/save-file",
                    json={"cls": "FlatPulse", "target": str(t), "replace_sha": c["file_sha"]})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and t.read_bytes() == p["text"].encode()
    assert open(j["backup"], "rb").read() == b"# a module of the lab\n"


def test_save_file_refusals_are_honest_400s(client, tmp_path):
    _new(client)
    r = client.post("/pulse-class/save-file", json={"cls": "FlatPulse", "target": str(tmp_path / "a.py")})
    assert r.status_code == 400 and "not imported" in r.get_json()["error"]
    _preview_ok(client)
    (tmp_path / "x.py").mkdir()
    for target, words in ((str(tmp_path / "x.py"), "is a folder"), ("rel.py", "full path"),
                          (str(tmp_path / "CON.py"), "reserved")):
        r = client.post("/pulse-class/save-file", json={"cls": "FlatPulse", "target": target})
        assert r.status_code == 400 and words in r.get_json()["error"], target


def test_pane_save_refuses_a_region_change_unless_unlocked(client):
    p = _new(client)
    mine = p["text"].replace("0.1", "0.7")
    r = client.post("/pulse-class/save", json={"cls": "FlatPulse", "text": mine, "base_sha": p["sha"]})
    assert r.status_code == 400 and "'amplitude'" in r.get_json()["error"]
    assert open(p["path"], "rb").read() == p["text"].encode()
    r = client.post("/pulse-class/save", json={"cls": "FlatPulse", "text": mine, "base_sha": p["sha"],
                                               "hand_edit": True})
    assert r.status_code == 200 and open(p["path"], "rb").read() == mine.encode()


def test_form_door_still_rewrites_the_region(client):
    p = _new(client)
    fields = [{"name": "amplitude", "type": "float", "default": 0.3, "unit": "V"},
              {"name": "length", "type": "int", "default": 100, "unit": "ns"}]
    r = client.post("/pulse-class/form", json={"cls": "FlatPulse", "fields": fields,
                                               "formula": "amplitude * np.ones(n)",
                                               "text": p["text"], "base_sha": p["sha"]})
    assert r.status_code == 200 and r.get_json()["info"]["spec"]["fields"][0]["default"] == 0.3


def test_poll_conflict_names_the_file_content_it_is_about(client):
    p = _new(client)
    new = p["text"].replace("0.1", "0.4").encode()
    with open(p["path"], "wb") as f:
        f.write(new)
    st = os.stat(p["path"])
    os.utime(p["path"], ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    client.get("/pulse-class/poll?cls=FlatPulse&dirty=1")
    r = client.get("/pulse-class/poll?cls=FlatPulse&dirty=1").get_json()
    assert r["conflict"]["file_sha"] == D.sha256_bytes(new)


@pytest.mark.parametrize("lost", ["reset", "evicted"])
def test_unknown_region_base_is_a_hand_edit_409(client, lost):
    p = _new(client)
    outside = p["text"].replace("0.1", "0.4").encode()
    with open(p["path"], "wb") as f:
        f.write(outside)
    if lost == "reset":
        W._WATCHES.clear()
    else:
        for w in W._WATCHES.values():
            w.known.clear()
    r = client.post("/pulse-class/save", json={
        "cls": "FlatPulse", "text": p["text"] + "# mine\n", "base_sha": p["sha"],
        "keep": "sm", "file_sha": D.sha256_bytes(outside)})
    j = r.get_json()
    assert r.status_code == 409 and j["base_unknown"]
    assert "no longer known" in j["error"] and "Edit the template lines by hand" in j["error"]
    assert "conflict" not in j and "region" not in j
    assert open(p["path"], "rb").read() == outside


@pytest.mark.parametrize("name,reason", [
    ("CON .py", "reserved"), ("nUl .py", "reserved"), ("PRN... .py", "reserved"),
    ("aux .py", "reserved"), ("CoM9 .py", "reserved"), ("lPt1 .py", "reserved"),
    ("host:evil.py", "colon"), ("folder:stream/file.py", "colon")])
def test_target_edge_names_are_reasoned_400s(client, tmp_path, name, reason):
    _new(client)
    r = client.post("/pulse-class/save-file", json={"cls": "FlatPulse", "target": str(tmp_path / name)})
    assert r.status_code == 400 and reason in r.get_json()["error"]
    assert list(tmp_path.glob("*.py")) == []


def test_form_conflict_keeps_form_output_including_formula(client):
    p = _new(client)
    outside = p["text"].replace("0.1", "0.4").encode()
    with open(p["path"], "wb") as f:
        f.write(outside)
    body = {"cls": "FlatPulse", "fields": [
        {"name": "amplitude", "type": "float", "default": 0.65, "unit": "V"},
        {"name": "length", "type": "int", "default": 100, "unit": "ns"}],
        "formula": "amplitude * np.ones(n) * 2", "text": p["text"] + "# helper\n", "base_sha": p["sha"]}
    r = client.post("/pulse-class/form", json=body)
    assert r.status_code == 409
    c = r.get_json()["conflict"]
    r = client.post("/pulse-class/form", json=body | {"keep": "sm", "file_sha": c["file_sha"]})
    j = r.get_json()
    assert r.status_code == 200 and j["info"]["spec"]["fields"][0]["default"] == 0.65
    assert j["info"]["spec"]["formula"] == body["formula"] and j["text"].endswith("# helper\n")
    assert open(p["path"], "rb").read() == j["text"].encode()
