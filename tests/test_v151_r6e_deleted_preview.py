"""R6e: selected-leaf preview names a removed run folder."""
from tests.test_ds_pick import env, _preview, FILT


def test_selected_preview_names_the_removed_run_folder(env):
    run = env["snap"].parent
    run.rename(run.with_name("removed_run"))
    before = (env["live"] / "state.json").read_bytes()
    r = _preview(env, [FILT])
    assert r.status_code == 404
    assert "This run's folder is no longer on disk" in r.get_json()["error"]
    assert (env["live"] / "state.json").read_bytes() == before
    with env["app"].app_context():
        from quam_state_manager.web import routes
        assert not routes._active_ctx()["store"].change_log


def test_selected_preview_distinguishes_an_existing_run_without_state(env):
    snap = env["snap"]
    snap.rename(snap.with_name("removed_state"))
    r = _preview(env, [FILT])
    assert r.status_code == 404
    assert r.get_json()["error"] == "No quam_state in this run"
