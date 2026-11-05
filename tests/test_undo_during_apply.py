"""An undo pressed while an Apply/Auto-Sync push is still writing the chip
undoes the NEWEST action, never an older one.

The push saves the working copy first (the change log is cleared) and journals
the outgoing edits only once the live write is decided. An undo that arrived
in between saw an empty log, walked the journal, and undid the PREVIOUS
applied action -- leaving the newest one in effect (a pair pointing at a qubit
that the undo had just removed). The undo now waits for the in-flight push.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    state = {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9,
                                "z": {"joint_offset": 0.08}}},
             "qubit_pairs": {}, "active_qubit_names": ["qA1"]}
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live}


def _edit(c, path, value):
    r = c.post("/field/edit-batch", json={
        "updates": [{"dot_path": path, "value": str(value)}], "expect_chip": ""})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _live(env):
    q = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]
    return q["z"]["joint_offset"], q["f_01"]


def test_an_undo_during_a_push_undoes_the_pushed_action(env, monkeypatch):
    c = env["client"]
    _edit(c, "qubits.qA1.z.joint_offset", 0.1)          # action 1
    assert c.post("/state/apply-to-live").status_code == 200
    _edit(c, "qubits.qA1.f_01", 5.5e9)                   # action 2, the newest

    in_write = threading.Event()
    release = threading.Event()
    real = working_copy.apply_to_live

    def slow_apply(*a, **k):
        in_write.set()
        assert release.wait(10)
        return real(*a, **k)

    monkeypatch.setattr(working_copy, "apply_to_live", slow_apply)
    out = {}

    def push():
        with env["app"].test_client() as c2:
            out["push"] = c2.post("/state/apply-to-live").status_code

    t = threading.Thread(target=push)
    t.start()
    assert in_write.wait(10), "the push never reached the live write"

    def undo():
        with env["app"].test_client() as c3:
            out["undo"] = c3.post("/undo").status_code

    u = threading.Thread(target=undo)
    u.start()
    u.join(0.5)
    release.set()
    t.join(15)
    u.join(15)
    assert out.get("push") == 200
    assert out.get("undo") == 200
    off, f01 = _live(env)
    # the undo removed action 2 (f_01) and left action 1 in effect
    assert f01 == 5.0e9, f"newest action not undone (f_01={f01})"
    assert off == 0.1, f"an OLDER action was undone instead (joint_offset={off})"


def test_live_writer_inventory_is_marked():
    """Inventory pin: a new caller must declare its whole press boundary."""
    import ast
    root = Path(__file__).resolve().parents[1] / 'quam_state_manager'
    found = set()
    for file in root.rglob('*.py'):
        tree = ast.parse(file.read_text(encoding='utf-8'))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and isinstance(n.func.value, ast.Name)
                     and n.func.value.id == 'working_copy'
                     and n.func.attr == 'apply_to_live']
            if calls:
                found.add((file.relative_to(root).as_posix(), fn.name))
                decorators = [ast.unparse(d) for d in fn.decorator_list]
                assert any('press' in d for d in decorators), (file, fn.name)
    assert found == {
        ('web/routes.py', 'state_history_restore_live'),
        ('web/routes.py', '_sync_pull_apply_to_live'),
        ('web/routes.py', 'state_apply_to_live'),
        ('core/autofit/writer.py', '_apply_live_with_one_retry'),
    }
    core = ast.parse((root / 'core/working_copy.py').read_text(encoding='utf-8'))
    writer = next(n for n in core.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'apply_to_live')
    assert any('live_press.marked' in ast.unparse(d) for d in writer.decorator_list)
    autofit = ast.parse((root / 'core/autofit/writer.py').read_text(encoding='utf-8'))
    for fn in autofit.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in {
                'apply_rows', 'revert_patches', 'restore_values'}:
            assert any('live_press.marked' in ast.unparse(d) for d in fn.decorator_list)


@pytest.mark.parametrize('as_keyword', [False, True])
def test_shared_helper_marks_the_captured_context(env, monkeypatch, as_keyword):
    from quam_state_manager.web import routes as R
    ctx = next(iter(env['app'].config['contexts'].values()))
    seen = []
    def inspect_mark(*a, **k):
        seen.append(not R._wait_for_presses(ctx, timeout=0))
    # A different thread must see the captured chip as busy, even if active flips.
    def apply(*a, **k):
        t = threading.Thread(target=inspect_mark)
        t.start()
        t.join(2)
        raise ValueError('stop before disk write')
    monkeypatch.setattr(working_copy, 'apply_to_live', apply)
    with env['app'].test_request_context('/state/sync', method='POST'):
        _edit(env['client'], 'qubits.qA1.f_01', 5.5e9)
        env['app'].config['active_context'] = None
        if as_keyword:
            R._sync_pull_apply_to_live(ctx=ctx, replay=None)
        else:
            R._sync_pull_apply_to_live(ctx, None)
    assert seen == [True]


@pytest.mark.parametrize('door', ['/undo', '/redo'])
def test_wait_rechecked_after_journal_read(env, monkeypatch, door):
    from quam_state_manager.web import routes as R
    calls = []
    def wait(ctx, timeout=10):
        calls.append(ctx)
        return len(calls) == 1
    monkeypatch.setattr(R, '_wait_for_presses', wait)
    r = env['client'].post(door)
    assert r.status_code == 409 and len(calls) >= 2
    assert 'still writing' in r.get_data(as_text=True).lower()


def test_wait_is_ten_seconds_and_nested_press_is_thread_local(env):
    from quam_state_manager.web import routes as R
    import inspect
    assert inspect.signature(R._wait_for_presses).parameters['timeout'].default == 10
    ctx = next(iter(env['app'].config['contexts'].values()))
    @R._journal_press
    def inner():
        assert R._wait_for_presses(ctx, timeout=0)
    @R._journal_press
    def outer():
        inner()
        seen = []
        t = threading.Thread(target=lambda: seen.append(R._wait_for_presses(ctx, timeout=0)))
        t.start()
        t.join(2)
        assert seen == [False]
    with env['app'].test_request_context():
        outer()
    assert R._wait_for_presses(ctx, timeout=0)


def test_a_press_that_finishes_during_journal_read_forces_a_reread(env, monkeypatch):
    from quam_state_manager.web import routes as R
    ctx = next(iter(env['app'].config['contexts'].values()))
    reads = []
    def sync(ctx):
        reads.append(1)
        if len(reads) == 1:
            with working_copy.live_press.mark(ctx['working_copy']):
                pass
    monkeypatch.setattr(R, '_journal_sync', sync)
    with env['app'].test_request_context():
        assert R._journal_ready_refusal(ctx) is None
    assert len(reads) == 2


def test_shared_core_callers_mark_preparation_and_completion():
    import ast
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'quam_state_manager/web/routes.py').read_text(encoding='utf-8'))
    found = set()
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == '_sync_pull_apply_to_live' for n in ast.walk(fn)):
            found.add(fn.name)
            assert any(ast.unparse(d) == '_journal_press' for d in fn.decorator_list), fn.name
    assert found == {'state_sync', '_flush_walk_step_live', 'dataset_load_state'}


def test_repeated_crossing_presses_share_one_wait_budget(env, monkeypatch):
    from quam_state_manager.web import routes as R
    import time
    ctx = next(iter(env['app'].config['contexts'].values()))
    class Clock:
        now = 0
        def monotonic(self):
            self.now += 6
            return self.now
        def __getattr__(self, name):
            return getattr(time, name)
    reads = []
    def sync(ctx):
        reads.append(1)
        if len(reads) > 2:
            raise AssertionError('wait budget was renewed on retry')
        with working_copy.live_press.mark(ctx['working_copy']):
            pass
    monkeypatch.setattr(R, 'time', Clock())
    monkeypatch.setattr(R, '_journal_sync', sync)
    with env['app'].test_request_context():
        response = R._journal_ready_refusal(ctx)
    assert response is not None and response[1] == 409


def test_core_press_decorator_preserves_keyword_calls(env):
    wc = next(iter(env['app'].config['contexts'].values()))['working_copy']
    @working_copy.live_press.marked(lambda wc: wc)
    def write(wc):
        return wc
    assert write(wc=wc) is wc
