"""Every POST door must be audited before it can share the plan's store."""
import pytest

from tests.state_model.repros._rig import Chip

# Audited POSTs that select/read state or write separate settings, metadata,
# history, queues or generated output; none stages an edit into the chip store.
# A new POST requires classification here or inclusion in the mutator lock.
_NON_STAGING_POSTS = {
    # v1.3.0: old POST inventory -> classify map analysis/local dismissal, neither stages state.
    'main.dataset_map_analyze',
    'main.qualibrate_dismiss_running',
    'agent.note_set',
    'agent.journal_append',
    'agent.journal_root',
    'agent.event_post',
    'agent.limits_route',
    'agent.session_stop',
    'agent.queue_clear',
    'agent.session_arm',
    'agent.session_disarm',
    'agent.plans_add',
    'agent.plan_mode',
    'agent.plan_cancel',
    'journal.journal_claim',
    'journal.journal_adopt',
    'agent_chat.start',
    'agent_chat.send',
    'agent_chat.end',
    'agent_chat.clear',
    'agent_chat.archive_delete',
    'agent_chat.ask',
    'agent_setup.connect',
    'agent_setup.disconnect',
    'agent_setup.journal_setup',
    'agent_setup.context_post',
    'agent_setup.test_call',

    'main.api_pulse_compare',
    'main.api_pulse_lab_waveform',
    'main.api_pulse_synth',
    'main.api_working_copies_gc',
    'main.auto_apply_arm',
    'main.auto_apply_disarm',
    'main.auto_sync_set',
    'main.autofit_abort',
    'main.autofit_ai_probe',
    'main.autofit_ai_set',
    'main.autofit_diagnose_rows',
    'main.autofit_resolve',
    'main.autofit_start',
    'main.bulk_column_history',
    'main.bulk_phys',
    'main.chip_compare',
    'main.chip_data_folder_decline',
    'main.chip_identity_decline',
    'main.chip_name_decline',
    'main.chip_spec_clear',
    'main.chip_spec_set',
    'main.chip_status_report_finalize',
    'main.compare',
    'main.compare_hub_map_save',
    'main.compare_hub_stash',
    'main.dataset_add_tag',
    'main.dataset_apply_selected_preview',
    'main.dataset_autofit_diagnose',
    'main.dataset_bookmark',
    'main.dataset_set_note',
    'main.datasets_rescan',
    'main.diagnostics_env_probe',
    'main.diff_view',
    'main.disk_reclaim',
    'main.disk_settings',
    'main.env_ack_revoke',
    'main.env_ack_save',
    'main.env_schema_dismiss',
    'main.env_schema_verdict',
    'main.env_schema_verdict_revoke',
    'main.field_lab_watch',
    'main.field_type_assign',
    'main.field_type_unassign',
    'main.fit_audit_cancel',
    'main.fit_audit_set_source_root',
    'main.generate_capabilities',
    'main.generate_import_port_csv',
    'main.generate_presets_save',
    'main.generate_probe',
    'main.generate_select_env',
    'main.history_snapshot',
    'main.hub_link_folder',
    'main.hub_unlink_folder',
    # v1.5.1 (T1-F9): records a data folder for the chip's history; stages nothing
    'main.hub_link_root',
    # UX round B2: the grid CSV export is POSTed (a long column list); it reads, never stages
    'main.export_csv',
    'main.instrument_compare',
    'main.instrument_preview',
    'main.load',
    'main.make_directory',
    'main.note_delete',
    'main.note_readdress',
    'main.note_save',
    'main.open_folder',
    'main.param_history_backfill',
    'main.param_history_decide_chip',
    'main.param_history_dismiss_chip_swap',
    'main.project_roots_confirm',
    'main.project_time_skew_answer',
    'main.project_time_skew_shown',
    'main.project_time_watch',
    'main.project_time_zone',
    'main.pulse_class_modules',
    'main.pulses_sparks',
    'main.pulses_vrows',
    'main.qualibrate_locate',
    'main.qualibrate_open_project',
    'main.qualibrate_project_env',
    'main.qualibrate_use_default_location',
    'main.qualibrate_use_location',
    'main.scheduler_cancel',
    'main.scheduler_effective_config',
    'main.scheduler_pause',
    'main.scheduler_preflight',
    'main.scheduler_preset_load',
    'main.scheduler_presets',
    'main.scheduler_queue_add',
    'main.scheduler_queue_mutate',
    'main.scheduler_register_storage',
    'main.scheduler_scan_params',
    'main.scheduler_settings',
    'main.scheduler_start',
    'main.settings_undo_live',
    'main.state_archive',
    'main.state_history_label',
    'main.state_history_snapshot',
    'main.trend',
    'main.trend_chart',
    'main.type_alarm_dismiss',
    'main.workspace_add',
    'main.workspace_refresh',
    'main.workspace_remove',
    'main.workspace_select',
}

def test_every_post_route_is_locked_or_explicitly_non_staging(tmp_path):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    posts = {r.endpoint for r in chip.app.url_map.iter_rules()
             if "POST" in r.methods}
    assert not posts - routes._SCHEDULER_MUTATOR_ENDPOINTS - _NON_STAGING_POSTS
    assert not _NON_STAGING_POSTS & routes._SCHEDULER_MUTATOR_ENDPOINTS
    assert _NON_STAGING_POSTS <= posts


def test_map_analysis_and_config_dismissal_do_not_stage_chip_edits(tmp_path, monkeypatch):
    import copy
    import json
    import numpy as np
    from types import SimpleNamespace
    from quam_state_manager.web import routes

    chip = Chip(tmp_path)
    axis = np.linspace(-2, 2, 21)
    z = (axis[None, :] - .2) ** 2 + (axis[:, None] + .3) ** 2
    trace = {"type": "heatmap", "full_resolution": True, "x": axis.tolist(),
             "y": axis.tolist(), "z": z.tolist()}
    figure = {"axes": [{"xlabel": "a_scan", "ylabel": "b_over_a", "title": "q1", "traces": [trace]}]}
    # v1.4.1: unproven target -> accepted node write, so analysis still returns updates without staging.
    (tmp_path / "node.json").write_text(json.dumps({"patches": [
        {"op": "replace", "path": "/quam/qubits/q1/T1", "old": .1, "value": .2}]}))
    converted = {"data": [trace], "clickable": {"evidence_tier": "applied", "targets": [
        {"path": "qubits.q1.T1", "axis": "x"}]}}
    run = SimpleNamespace(folder_path=str(tmp_path), id=1, experiment_name="synthetic")
    monkeypatch.setattr(routes, "_map_figure", lambda *args: (run, figure, converted))
    store = chip.ctx["store"]
    before = (copy.deepcopy(store.merged), chip.log(), store.mutation_seq,
              chip.live_state(), chip.journal())
    response = chip.c.post("/dataset/run/map/analyze", json={"fig": "map"})
    assert response.status_code == 200 and response.get_json().get("updates"), response.data
    response = chip.c.post("/qualibrate/dismiss-running", data={"path": str(tmp_path / "server.toml")})
    assert response.status_code in (200, 302)
    assert (store.merged, chip.log(), store.mutation_seq, chip.live_state(), chip.journal()) == before


@pytest.mark.parametrize("url", [
    "/pair/q1-q2/gate-inspector/switch-moving", "/api/pulse/copy",
    "/api/pulse/gaussian-cz", "/api/pulse/delete-together",
    "/api/pulse/create", "/api/pulse/delete", "/pulse/edit",
    "/field/create", "/field/delete", "/qubit/q1/edit",
    "/pair/q1-q2/edit", "/pair/q1-q2/gate", "/diagnostics/apply-fix",
    "/type-fix/apply", "/auto-apply/revert", "/state/take-live/restore",
    "/chip-profile/set", "/app/reload", "/api/agent/undo-mine",
    "/api/agent/run-node", "/api/agent/approvals/probe/approve",
    "/api/agent/plans/probe/start",
])
@pytest.mark.parametrize("owner", ["agent", "scheduler", "autofit"])
def test_staging_doors_refuse_before_mutating_a_held_chip(tmp_path, monkeypatch, url, owner):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    if owner == "agent":
        chip.app.config["agent_edit_lock"] = {"path": chip.ctx["path"], "node": "plan"}
    elif owner == "scheduler":
        monkeypatch.setattr(routes.scheduler, "is_active", lambda _: True)
    else:
        from types import SimpleNamespace
        monkeypatch.setattr(routes, "_loaded_autofit_engine",
                            lambda: SimpleNamespace(locks_chip=lambda _: True))
    before = (chip.log(), chip.journal(), chip.live_state())
    response = chip.c.post(url)
    if owner in ("scheduler", "agent") and url in ("/api/agent/run-node", "/api/agent/plans/probe/start"):
        # v1.3.0: 409 scheduler_running / agent_running -> the door's own gate, why:
        # it answers run_active and names a live orphan / foreign owner, and clears
        # a dead own row (tests/test_agent_runs.py, tests/test_run_failure_class.py);
        # it stages nothing.
        assert response.status_code >= 400, response.get_data(as_text=True)
    else:
        assert response.status_code == 409, response.get_data(as_text=True)
    assert (chip.log(), chip.journal(), chip.live_state()) == before
