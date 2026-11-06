"""Notice delivery must not change request locking or download semantics."""
import json
import threading

import pytest
from flask import Response

from quam_state_manager.web import routes
from tests.test_autosync_merge import env, _arm, _ctx, _state, _write_chip


@pytest.mark.parametrize("edit", [False, True])
def test_notice_hooks_leave_requests_free_during_pull_io(env, edit):
    ctx = _ctx(env)
    done = threading.Event()
    result = {}

    def request_while_pulling():
        try:
            client = env["app"].test_client()
            client.environ_base["HTTP_X_SM_WINDOW_ID"] = "other"
            response = (client.post("/field/edit", data={
                "dot_path": "qubits.qA1.T1", "value": "4.44e-5"})
                if edit else client.get("/state/tray"))
            result["status"] = response.status_code
        except BaseException as exc:
            result["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=request_while_pulling)
    with env["app"].test_request_context():
        # This is the lock a pull holds while reading and replacing files.
        with routes._active_wc_lock(ctx):
            thread.start()
            finished_during_io = done.wait(2)
    thread.join(10)
    assert not thread.is_alive()
    assert finished_during_io, "notice hooks blocked a request behind pull I/O"
    assert result == {"status": 200}, result
    if edit:
        assert ctx["store"].state["qubits"]["qA1"]["T1"] == 4.44e-5


def test_replace_discards_an_edit_during_pre_pull_preparation(env, monkeypatch):
    _arm(env, pull=True, push=False, replace=True)
    _write_chip(env["live"], _state(f01=7.7e9))
    ctx = _ctx(env)
    ctx["live_diverged"] = True
    before = routes.working_copy.sync_point(ctx["working_copy"])
    real = routes._leaf_snapshot
    edited = []

    def prepare(captured, *args, **kwargs):
        if not edited:
            edited.append(True)
            response = env["client"].post("/field/edit", data={
                "dot_path": "qubits.qA1.T1", "value": "4.44e-5"})
            assert response.status_code == 200
        return real(captured, *args, **kwargs)

    monkeypatch.setattr(routes, "_leaf_snapshot", prepare)
    response = env["client"].post("/auto-sync/pull")
    assert edited, "the pull never reached preparation"
    assert response.status_code == 200
    assert ctx["store"].state["qubits"]["qA1"]["T1"] != 4.44e-5
    assert not ctx["store"].change_log
    assert routes.working_copy.sync_point(ctx["working_copy"]) != before
    payload = json.loads(response.headers["HX-Trigger"])["autoSyncPulled"]
    assert payload["replaced"] is True
    assert payload["count"] >= 1


@pytest.mark.parametrize("passthrough", [False, True])
@pytest.mark.parametrize("notice_pending", [False, True])
def test_notice_delivery_never_reads_a_json_stream(env, passthrough, notice_pending):
    ctx = _ctx(env)
    if notice_pending:
        ctx["_auto_restore_notice"] = {"event": "pending", "at": routes.time.time()}
        ctx["_notice_recipients"] = {"download"}
        ctx["_notice_delivered"] = set()
    reads = []

    def content():
        reads.append(True)
        yield b'{"config": 1}'

    stream = content()
    response = Response(stream, mimetype="application/json", direct_passthrough=passthrough)
    response.headers["Content-Disposition"] = 'attachment; filename="config.json"'
    with env["app"].test_request_context(headers={"X-SM-Window-ID": "download"}):
        assert routes._finish_auto_restore_notice(response) is response
    assert reads == [], "the notice hook consumed the download"
    assert response.response is stream
    assert not ctx.get("_notice_delivered")
    assert b"".join(response.response) == b'{"config": 1}'
    response.close()
