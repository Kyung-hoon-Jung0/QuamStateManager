"""v1.5.1 C1 + MINOR 5: what the REAL v1.3.0 does with this SM's checkpoints.

Each test extracts v1.3.0 from the repository's own tag (``git archive``)
into a temporary folder and runs it in a subprocess on the files this SM
wrote -- never a hand-written imitation of the older reader.

C1. Checkpoint rows leave ``origin: null`` out so v1.3.0 (whose rows have no
``origin`` field) can read a checkpoint of plain edits. But the re-apply stash
of a failed Apply can hold a plot-click edit's 4-element tag
``["set", value, group, origin]``; v1.3.0 decodes only 2- and 3-element tags,
kept the list as a plain value and its re-apply wrote the LIST into the chip
(``q1.T1 = ['set', 1.2e-05, None, {...}]``, reported ``applied: 2, failed: []``).
Such a checkpoint must keep the null fields, so v1.3.0 refuses it whole.

MINOR 5. v1.3.0 deletes ``<key>.pending_tray.unrecovered-*.json`` beyond its
newest five each time it refuses a checkpoint. This SM keeps every refused
checkpoint listed until the user decides -- so it names them outside that
pattern, and renames an older-named one when it opens the chip.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray, safe_io
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_REPO = Path(__file__).resolve().parents[1]
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
_STATE = {"qubits": {"q1": {"id": "q1", "T1": 1.0e-5, "z": {"joint_offset": 0.08}}},
          "qubit_pairs": {}, "active_qubit_names": ["q1"]}


@pytest.fixture(scope="module")
def v130_tree(tmp_path_factory):
    """The v1.3.0 package, extracted from the repository's tag."""
    try:
        out = subprocess.run(["git", "-C", str(_REPO), "archive", "--format=tar", "v1.3.0",
                              "quam_state_manager"], capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"git is not available ({exc})")
    if out.returncode != 0 or not out.stdout:
        pytest.skip("the v1.3.0 tag is not in this repository: "
                    + out.stderr.decode("utf-8", "replace")[:200])
    dest = tmp_path_factory.mktemp("v130")
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tf:
        tf.extractall(dest, filter="data")
    assert (dest / "quam_state_manager" / "core" / "pending_tray.py").is_file()
    return dest


def _run_v130(tree: Path, script: str, *args) -> dict:
    """Run *script* under the extracted v1.3.0 package; its RESULT line."""
    env = dict(os.environ, PYTHONPATH=str(tree), PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", script, str(tree), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", timeout=600,
                       env=env, cwd=str(tree))
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert r.returncode == 0 and lines, r.stdout[-3000:] + r.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT "):])


_V130_OPEN_AND_REAPPLY = r'''
import json, sys
from pathlib import Path
tree, root, live = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
import quam_state_manager
assert Path(quam_state_manager.__file__).resolve().is_relative_to(tree.resolve()), quam_state_manager.__file__
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app
routes._quam_cache.clear()
app = create_app(testing=True, instance_path=str(root / "_inst"))
c = app.test_client()
c.post("/load", data={"folder": str(live)})
ctx = app.config["contexts"][app.config["active_context"]]
out = {"notice": str(ctx.get("_tray_recovery_notice") or ""),
       "rows": [e.dot_path for e in ctx["store"].change_log],
       "stash": ctx.get("pending_reapply")}
r = c.post("/state/sync", data={"mode": "reapply"})
out["reapply_status"] = r.status_code
out["t1"] = ctx["store"].get_value("qubits.q1.T1")
print("RESULT " + json.dumps(out, default=str))
'''

_V130_PRUNE = r'''
import json, sys
from pathlib import Path
tree, sidecar = Path(sys.argv[1]), Path(sys.argv[2])
import quam_state_manager
assert Path(quam_state_manager.__file__).resolve().is_relative_to(tree.resolve())
from quam_state_manager.core import pending_tray
pending_tray._prune_unrecovered(sidecar)
print("RESULT " + json.dumps(sorted(p.name for p in sidecar.parent.iterdir())))
'''


def _rc_open(root: Path):
    live = root / "chips" / "live"
    live.mkdir(parents=True, exist_ok=True)
    (live / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(root / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return app, c, live, app.config["contexts"][app.config["active_context"]]


def _edit(c, path, value):
    r = c.post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def test_a_stash_older_sm_cannot_decode_makes_it_refuse_the_checkpoint(tmp_path, v130_tree,
                                                                      monkeypatch):
    app, c, live, ctx = _rc_open(tmp_path)
    # 1. a plot-click value
    _edit(c, "qubits.q1.T1", "1.2e-05")
    ctx["store"].change_log[0].origin = {"src": "plot_click", "run_uid": "r1", "text": "fit"}
    # 2. Apply: the live write is denied -- the save stands, the stash keeps
    #    the plot-click row as a 4-element tag
    real = safe_io.write_state_wiring_bytes

    def deny(folder, *a, **kw):
        if Path(folder).resolve() == live.resolve():
            raise PermissionError(13, "Access is denied", str(folder))
        return real(folder, *a, **kw)

    monkeypatch.setattr(safe_io, "write_state_wiring_bytes", deny)
    r = c.post("/state/apply-to-live", headers={"Accept": "application/json"})
    monkeypatch.setattr(safe_io, "write_state_wiring_bytes", real)
    assert r.status_code == 409
    assert len(ctx["pending_reapply"]["qubits.q1.T1"]) == 4      # the state C1 needs
    # 3. any plain edit -> a checkpoint whose rows have no origin of their own
    _edit(c, "qubits.q1.z.joint_offset", "0.09")
    pending_tray.flush(ctx)
    doc = json.loads(pending_tray.sidecar_path(ctx["working_copy"]).read_text(encoding="utf-8"))
    assert doc["entries"] and all(e.get("origin") is None for e in doc["entries"])

    # 4. the real v1.3.0 opens the instance, then re-applies
    out = _run_v130(v130_tree, _V130_OPEN_AND_REAPPLY, tmp_path, live)
    assert not isinstance(out["t1"], list), out          # never the tag as a value
    assert out["t1"] == 1.2e-05, out      # what the failed Apply's save left on disk
    assert out["rows"] == [], out
    assert "could not be recovered" in out["notice"], out
    # how: the rows kept their null fields (this SM reads them as defaults)
    assert all("origin" in e for e in doc["entries"]), doc["entries"]


def test_a_checkpoint_of_plain_edits_is_still_read_by_the_older_sm(tmp_path, v130_tree):
    app, c, live, ctx = _rc_open(tmp_path)
    _edit(c, "qubits.q1.z.joint_offset", "0.09")
    pending_tray.flush(ctx)
    doc = json.loads(pending_tray.sidecar_path(ctx["working_copy"]).read_text(encoding="utf-8"))
    assert all("origin" not in e for e in doc["entries"]), doc["entries"]
    out = _run_v130(v130_tree, _V130_OPEN_AND_REAPPLY, tmp_path, live)
    assert out["rows"] == ["qubits.q1.z.joint_offset"], out
    assert "could not be recovered" not in out["notice"], out


def test_the_older_sm_prune_never_reaches_a_refused_checkpoint(tmp_path, v130_tree):
    app, c, live, ctx = _rc_open(tmp_path)
    _edit(c, "qubits.q1.z.joint_offset", "0.09")
    pending_tray.flush(ctx)
    wc = ctx["working_copy"]
    side = pending_tray.sidecar_path(wc)
    # seven refusals this SM kept, and six an older SM wrote (its own name)
    for _ in range(7):
        pending_tray.set_aside(wc, list(ctx["store"].change_log))
    older = [side.with_name(f"{side.stem}.unrecovered-older{i}.json") for i in range(6)]
    for i, f in enumerate(older):
        f.write_text(side.read_text(encoding="utf-8"), encoding="utf-8")
        os.utime(f, (1_000_000 + i, 1_000_000 + i))
    # this SM opens the chip again
    routes._quam_cache.clear()
    app2 = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    assert app2.test_client().post("/load", data={"folder": str(live)}).status_code in (200, 302)
    listed = pending_tray.unrecovered(wc)
    assert len(listed) == 13, listed
    assert {f"older{i}" for i in range(6)} <= {d["id"] for d in listed}, listed
    before = sorted(p.name for p in side.parent.iterdir() if ".unrecovered-" in p.name)
    assert len(before) == 13, before
    # the real v1.3.0 prune (it keeps the newest five of ITS name pattern)
    names = _run_v130(v130_tree, _V130_PRUNE, side)
    after = sorted(n for n in names if ".unrecovered-" in n)
    assert after == before, sorted(set(before) - set(after))
    assert not any(f.exists() for f in older)      # renamed, out of its reach
