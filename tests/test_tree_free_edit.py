"""Stage A behavior pins against shipped JS, with executable mutations."""
import os
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def run(mutation=None):
    env = dict(os.environ)
    if mutation:
        env["TREE_MUTATION"] = mutation
    return subprocess.run(["node", "tests/tree_free_edit_selfcheck.cjs"], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)

def test_tree_free_edit():
    r = run()
    assert r.returncode == 0 and "ALL OK" in r.stdout, r.stdout + r.stderr

@pytest.mark.parametrize("mutation", ["auto_blur", "auto_hidden", "auto_pagehide", "auto_nav", "pointer_layout", "pointer_cached", "draft_jump", "sync_snapshot", "suggestion_flow", "suggestion_outside", "press_mount", "pointer_hint", "pointer_toast", "pointer_flash", "idle_pointer", "draft_count", "input_width", "input_literal", "input_echo", "sync_count", "draft_style", "relink", "echo_target", "draft_storage", "unload_drafts", "refusal_clear", "lazy_resume", "nav_drafts", "one", "escape", "caret", "flight", "cache", "visible", "tab", "undo", "text_undo", "global", "brackets", "multi", "search", "off", "render", "pane", "fsp", "fsp_cancel", "refusal", "peek_retry", "tooltip", "pointer", "chip_context", "stale_token", "source", "source_key", "display_order"])
def test_tree_free_edit_mutations(mutation):
    r = run(mutation)
    assert "mutation target exists" not in r.stderr, r.stderr
    assert r.returncode != 0 and "AssertionError" in r.stderr, r.stdout + r.stderr


def test_pointer_server_paths(tmp_path):
    """A relink edits the file string; a plain number retains value mode."""
    import json
    from quam_state_manager.web.app import create_app

    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"resonator": {"operations": {
            "readout": {"amplitude": 0.2},
            "other": {"amplitude": 0.4},
            "alias": {"amplitude": "#/qubits/q1/resonator/operations/readout/amplitude"},
            "sibling": {"amplitude": "#/qubits/q1/resonator/operations/readout/amplitude"},
        }}}}}))
    (folder / "wiring.json").write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post('/load', data={'folder': str(folder)})
    prefix = 'qubits.q1.resonator.operations.'
    alias, target, other = [prefix + p + '.amplitude' for p in ('alias', 'readout', 'other')]
    sharing = client.get('/field/peek', query_string={'dot_path': alias}).json['resolved'][alias]
    assert sharing['resolved_path'] == target
    assert sharing['shared_by'] == ['sibling']
    pointer = '#/' + other.replace('.', '/')
    reply = client.post('/field/edit', data={
        'dot_path': alias, 'value': pointer, 'value_quoted': '1', 'pointer_mode': 'relink'})
    assert reply.status_code == 200, reply.json
    values = client.get('/field/peek', query_string=[('dot_path', p) for p in (alias, target, other)]).json['values']
    assert values == {alias: pointer, target: 0.2, other: 0.4}
    assert reply.json['stored'] == values[alias]
    reply = client.post('/field/edit', data={'dot_path': alias, 'value': '0.6'})
    assert reply.status_code == 200, reply.json
    values = client.get('/field/peek', query_string=[('dot_path', p) for p in (alias, target, other)]).json['values']
    assert values == {alias: pointer, target: 0.2, other: 0.6}
    assert reply.json['resolved_path'] == other
    assert reply.json['raw_value'] == pointer


def test_commit_on_leave_default_is_versioned(tmp_path):
    """Auto-save defaults ON; a pre-version file does not hold a choice
    against that default, an OFF saved now is respected."""
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    (tmp_path / "instance").mkdir(exist_ok=True)
    (tmp_path / "instance" / "tree_edit.json").write_text('{"commit_on_leave": false}')
    assert client.get('/settings/tree-edit').json['commit_on_leave'] is True
    assert client.post('/settings/tree-edit', data={'commit_on_leave': '0'}).json['commit_on_leave'] is False
    assert client.get('/settings/tree-edit').json['commit_on_leave'] is False
    assert client.post('/settings/tree-edit', data={'commit_on_leave': '1'}).json['commit_on_leave'] is True
    assert client.get('/settings/tree-edit').json['commit_on_leave'] is True
