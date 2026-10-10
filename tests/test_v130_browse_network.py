"""v1.3.0 B1 P2: the folder browser never waits out an unreachable network share.

``GET /browse?path=\\\\host\\share\\...`` on a share that does not answer held
the request for the OS's SMB timeout (45 s+ measured, a server thread held the
whole time). The share is now asked on a daemon thread with a time budget; past
it the browser answers at once with an honest "Network share not reachable"
(the dialog shows it with Retry), and a share still being asked is not asked a
second time."""
import sys
import threading
import time

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="UNC / drive letters are Windows paths")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_NET_PROBES", {})
    monkeypatch.setattr(routes, "_NET_PROBE_BUDGET_S", 0.3)
    return create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()


@pytest.mark.parametrize("raw,root", [
    (r"\\host\share\a\b", r"\\host\share"), ("//host/share/a", r"\\host\share"),
    (r"\\host\share", r"\\host\share"), (r"\\host", None), ("C:\\Windows", None)])
def test_network_root(raw, root):
    from pathlib import Path
    assert routes._network_root(Path(raw)) == root


def test_an_unanswering_share_is_refused_within_the_budget(client, monkeypatch):
    calls = []
    release = threading.Event()

    def hangs(root):
        calls.append(root)
        release.wait(10)
        return True
    monkeypatch.setattr(routes, "_net_probe", hangs)
    # no answer reuse: the one-question-per-share rule alone must hold the second ask
    monkeypatch.setattr(routes, "_NET_PROBE_TTL_S", 0.0)
    t = time.monotonic()
    r = client.get("/browse", query_string={"path": r"\\host\share\data"})
    dt = time.monotonic() - t
    body = r.get_json()
    assert r.status_code == 200 and dt < 2.0, dt
    assert body["dirs"] == [] and "Network share not reachable" in body["error"]
    assert r"\\host\share" in body["error"]
    # asked again while the first question still hangs: answered at once, not asked twice
    t = time.monotonic()
    r2 = client.get("/browse", query_string={"path": r"\\host\share\other"})
    assert time.monotonic() - t < 0.2 and "not reachable" in r2.get_json()["error"]
    assert calls == [r"\\host\share"]
    release.set()


def test_a_reachable_share_is_listed(client, monkeypatch, tmp_path):
    (tmp_path / "data" / "2026-10-01").mkdir(parents=True)
    base = str(tmp_path / "data")
    monkeypatch.setattr(routes, "_network_root",
                        lambda p: r"\\host\share" if str(p).startswith(base) else None)
    monkeypatch.setattr(routes, "_net_probe", lambda root: True)
    body = client.get("/browse", query_string={"path": base}).get_json()
    assert "error" not in body and any(d.endswith("2026-10-01") for d in body["dirs"]), body


def test_the_drive_list_names_a_network_drive_without_touching_it(client, monkeypatch):
    from pathlib import Path
    touched = []
    real_exists = Path.exists

    def exists(self, *a, **k):
        touched.append(str(self))
        return real_exists(self, *a, **k)
    monkeypatch.setattr(routes, "_network_root",
                        lambda p: "Q:\\" if str(p).upper().startswith("Q:") else None)
    monkeypatch.setattr(Path, "exists", exists)
    body = client.get("/browse").get_json()
    assert "Q:\\" in body["dirs"]
    assert "Q:\\" not in touched
