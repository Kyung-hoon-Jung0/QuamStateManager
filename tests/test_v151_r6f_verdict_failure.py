"""Comparison failures never pretend a writer is busy."""
import pytest
from quam_state_manager.core import sync_conflict, working_copy
from quam_state_manager.web import routes
from tests.test_v151_r4_merge_live_values import chip


@pytest.mark.parametrize("failure", ["compare", "classify", "working_copy", "store"])
def test_comparison_failure_is_honest_and_logged(chip, monkeypatch, caplog, failure):
    client, live = chip
    with client.application.app_context():
        ctx = routes._active_ctx()
    before = (live / "state.json").read_bytes()
    pulled = []
    monkeypatch.setattr(working_copy, "sync_from_live", lambda wc: pulled.append(True))
    def broken(*args, **kwargs):
        raise RuntimeError("comparison probe detail")
    if failure == "compare":
        monkeypatch.setattr(routes, "_working_vs_live_entries", broken)
    elif failure == "classify":
        monkeypatch.setattr(sync_conflict, "classify", broken)
    else:
        monkeypatch.setitem(ctx, failure, None)
    with caplog.at_level("WARNING"):
        try:
            if failure in ("compare", "classify"):
                response = client.post("/state/sync", data={"mode": "apply", "check_collisions": "1"})
                status, body = response.status_code, response.get_json()
            else:
                with client.application.test_request_context():
                    verdict = routes._auto_pull_verdict(ctx, ())
                    response, status = routes._sync_replay_refusal(ctx, verdict, {}, "apply")
                    body = response.get_json()
        except Exception as exc:
            status, body = 599, {"unexpected": str(exc)}
    assert status == 409 and body.get("error_code") == "verdict_failed", body
    assert body["message"] == "SM could not compare your edits with the live chip; nothing was written."
    assert "being written" not in body["message"] and "try again" not in body["message"]
    assert "auto-pull verdict" in caplog.text
    if failure in ("compare", "classify"):
        assert "comparison probe detail" in caplog.text
    else:
        assert "missing working copy or store" in caplog.text
    assert not pulled and (live / "state.json").read_bytes() == before
