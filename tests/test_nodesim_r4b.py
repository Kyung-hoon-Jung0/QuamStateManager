"""Node simulate fix R4B pins: re-login on a server 401, refused spawns not counted, cached provenance, honest monitor
sum, lane-label overlap and refused syntax-error nodes.

Broker pins use an in-process broker object with a fake qm_saas module; no network, no SaaS, no hardware.
SM_NODESIM_MUTATION_R4B=<row> loads a source-mutated broker so the matching pin can be shown red.
"""
import base64
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from quam_state_manager.core import nodesim_click as click
from tests import test_opx_monitor as tom
from tests.test_opx_monitor import env  # noqa: F401  (the monitor page rig fixture)

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "quam_state_manager" / "generator" / "nodesim_broker.py"

MUTATIONS = {
    # the old launch rule: only the local "no session" text is retried, the server 401 is re-raised
    "R4B-retry": ("            if launch and not unauthenticated(exc):",
                  '            if launch and str(exc) != "api client is unauthenticated":'),
    # a spawn the server refused as unauthenticated is kept as a counted ghost again
    "R4B-ghost": ('if row in self.rows and not row["instance_id"] and refused(exc):',
                  'if False:'),
    # FP3-B: only a 401 releases the reservation again, any other server refusal is kept as a counted ghost
    "FP3B": ('if row in self.rows and not row["instance_id"] and refused(exc):',
             'if row in self.rows and not row["instance_id"] and unauthenticated(exc):'),
}


def _broker_module(tmp_path):
    """The production broker file, or a copy (beside copies of its two helpers) with one R4B protection removed."""
    row = os.environ.get("SM_NODESIM_MUTATION_R4B")
    path = BROKER
    if row:
        old, new = MUTATIONS[row]
        source = BROKER.read_text(encoding="utf-8")
        assert old in source, row
        (tmp_path / "mut" / "generator").mkdir(parents=True)
        (tmp_path / "mut" / "core").mkdir()
        for helper in ("generator/nodesim_proto.py", "core/xlock.py"):
            text = (ROOT / "quam_state_manager" / helper).read_text(encoding="utf-8")
            (tmp_path / "mut" / helper).write_text(text, encoding="utf-8")
        path = tmp_path / "mut" / "generator" / "nodesim_broker.py"
        path.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("broker_r4b", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _jwt(exp):
    """A JWT-shaped session string with an exp claim; the signature is a dummy."""
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode("ascii")).rstrip(b"=").decode("ascii")
    return part(dict(alg="HS256")) + "." + part(dict(exp=exp, sub="tester")) + ".sig-sentinel-7c1d"


class FakeSaas:
    """A fake qm_saas module: login, versions and spawn, driven by a plan dict."""

    __version__ = "1.2.0"

    def __init__(self, exp_in=900.0):
        self.plan = dict(refuse=0, other=None, exp_in=exp_in)
        self.logins, self.launches, self.tokens = 0, [], []
        module = self

        class Client:
            def __init__(self, generation):
                self.generation = generation
                self._jwt = _jwt(time.time() + module.plan["exp_in"])
                module.tokens.append(self._jwt)

            def close_simulator(self, instance_id):
                pass

        class Instance:
            def __init__(self, version):
                self.version = version

            def spawn(self):
                module.launches.append(self.version)
                if module.plan["refuse"] > 0:
                    module.plan["refuse"] -= 1
                    raise Exception(f"Could not spawn simulator of version {self.version}: unauthenticated")
                if module.plan["other"]:
                    raise Exception(module.plan["other"])
                self.id = "inst-" + str(len(module.launches))
                self.token = "instance-token-sentinel-55aa"
                self.host, self.port = "192.0.2.20", 443
                self.expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=14)

        class QmSaas:
            def __init__(self, **kwargs):
                module.logins += 1
                self._client = Client(module.logins)

            def simulator(self, version, cluster):
                return Instance(version)

            def versions(self):
                return ["v3_7_3"]

        self.QmSaas = QmSaas
        self.QOPVersion = str


class Peer:
    """A pipe peer that records every event sent to it."""

    def __init__(self):
        self.owner = (os.getpid(), None)
        self.events = []

    def send(self, event, **fields):
        self.events.append(dict(fields, ev=event))
        return True


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """An in-process broker over a fake qm_saas module, logged in once."""
    b = _broker_module(tmp_path)
    monkeypatch.setattr(b.proto, "cluster_config", lambda spec, module: {})
    saas = FakeSaas()
    directory = tmp_path / "broker"
    directory.mkdir()
    broker = b.Broker(directory, saas)
    broker.task = lambda function, *args: function(*args) or True
    broker.login(dict(email="user@example.invalid", password="pw-sentinel-0b9e", host="192.0.2.10", port=443))
    return SimpleNamespace(b=b, broker=broker, saas=saas, peer=Peer(), directory=directory)


def _spawn(rig, req="r1"):
    """Reserve and spawn one row the way acquire does; return the peer's last event."""
    message = dict(op="acquire", req=req, version="v3_7_3", hash="cluster-a", reason="click", spec={},
                   owner=dict(sm_pid=os.getpid(), worker_pid=None))
    event, fields, row, old = rig.broker.reserve(rig.peer, message)
    assert event == "spawning", (event, fields)
    rig.broker.spawn(row, rig.peer, message, old)
    return rig.peer.events[-1]


def _log(rig):
    return (rig.directory / "broker.log").read_text(encoding="ascii")


def _no_token_in_log(rig):
    text = _log(rig)
    for token in rig.saas.tokens + ["instance-token-sentinel-55aa", "pw-sentinel-0b9e"]:
        assert token not in text
    assert "sig-sentinel" not in text


def test_server_401_logs_in_once_and_retries_once(rig):
    """P1-2 / D-53: a server 401 -> one fresh login -> one retry succeeds; exactly one instance; no token logged."""
    rig.saas.plan["refuse"] = 1
    logins = rig.saas.logins
    event = _spawn(rig)
    assert event["ev"] == "lease", event
    assert rig.saas.logins == logins + 1
    assert len(rig.saas.launches) == 2
    live = [row for row in rig.broker.rows if row["state"] == "live"]
    assert len(live) == 1 and live[0]["instance_id"] == event["instance_id"]
    assert "unauthenticated" in _log(rig)
    _no_token_in_log(rig)


def test_second_401_is_shown_verbatim_without_a_third_attempt(rig):
    """P1-2 / D-53: 401, fresh login, 401 again -> that message verbatim; no third spawn."""
    rig.saas.plan["refuse"] = 2
    event = _spawn(rig)
    assert event["ev"] == "spawn_failed"
    assert event["line"] == "Could not spawn simulator of version v3_7_3: unauthenticated"
    assert len(rig.saas.launches) == 2
    assert click.line("spawn_failed", message=event["line"]) == (
        "The cloud simulator instance could not start: Could not spawn simulator of version v3_7_3: unauthenticated")
    _no_token_in_log(rig)


def test_other_launch_error_is_not_retried(rig):
    """D-43 unchanged: a non-auth launch failure after the POST is raised once, never retried, no re-login."""
    rig.saas.plan["other"] = "Could not spawn simulator of version v3_7_3: internal error"
    logins = rig.saas.logins
    event = _spawn(rig)
    assert event["ev"] == "spawn_failed" and event["line"].endswith("internal error")
    assert len(rig.saas.launches) == 1 and rig.saas.logins == logins


def test_server_refused_spawn_releases_its_slot_at_once(rig):
    """FP3-B: the server answered the spawn with an error and no id -> no ghost row, 0 in use; the next Simulate
    counts only its own pending row ("1 of 3")."""
    rig.saas.plan["other"] = "Could not spawn simulator of version v3_7_3: internal error"
    event = _spawn(rig)
    assert event["ev"] == "spawn_failed"
    assert rig.broker.rows == []
    rig.broker.dispatch(rig.peer, dict(op="status"))
    status = rig.peer.events[-1]
    assert status["ev"] == "status" and status["rows"] == [] and status["in_use_here"] == 0
    rig.saas.plan["other"] = None
    message = dict(op="acquire", req="r2", version="v3_7_3", hash="cluster-a", reason="click", spec={},
                   owner=dict(sm_pid=os.getpid(), worker_pid=None))
    ev, fields, row, old = rig.broker.reserve(rig.peer, message)
    assert ev == "spawning" and fields["in_use_here"] == 1
    assert "1 of 3 instances of this account in use on this PC." in click.line("starting", **fields)
    rig.broker.spawn(row, rig.peer, message, old)
    assert rig.peer.events[-1]["ev"] == "lease"
    assert [r["state"] for r in rig.broker.rows] == ["live"]


@pytest.mark.parametrize("text", [
    "HTTPSConnectionPool(host='192.0.2.10', port=443): Read timed out. (read timeout=None)",
    "Could not spawn simulator of version v3_7_3: non-JSON response body: '<html>504 Gateway Time-out</html>'",
])
def test_unknown_spawn_outcome_keeps_a_counted_ghost(rig, text):
    """D-43 unchanged: a timeout or a proxy page may hide an instance on the account, so the ghost still counts."""
    rig.saas.plan["other"] = text
    event = _spawn(rig)
    assert event["ev"] == "spawn_failed"
    assert [row["state"] for row in rig.broker.rows] == ["ghost"]
    rig.broker.dispatch(rig.peer, dict(op="status"))
    assert rig.peer.events[-1]["in_use_here"] == 1


def test_session_expiring_soon_logs_in_before_the_operation(rig):
    """D-53: a held JWT with 30 s left -> login first, then the spawn runs once on the fresh session."""
    rig.saas.plan["exp_in"] = 30
    rig.broker.login()
    logins = rig.saas.logins
    rig.saas.plan["exp_in"] = 900
    assert rig.b.jwt_left(rig.broker.client._client._jwt) < 60
    event = _spawn(rig)
    assert event["ev"] == "lease"
    # versions() and spawn both run on the session renewed before the first operation
    assert rig.saas.logins == logins + 1 and len(rig.saas.launches) == 1
    assert rig.b.jwt_left(rig.broker.client._client._jwt) > 600
    _no_token_in_log(rig)


def test_session_with_time_left_is_not_renewed(rig):
    """D-53: a JWT with 15 min left is used as is."""
    logins = rig.saas.logins
    assert _spawn(rig)["ev"] == "lease"
    assert rig.saas.logins == logins


def test_failed_spawns_are_not_counted(rig):
    """r3 P2-1: one live + two spawns refused as unauthenticated -> in_use_here == 1, one row shown.

    The r3 ghosts came from server 401 refusals, which created nothing (D-53); they are released at once.
    """
    for req in ("f1", "f2"):
        rig.saas.plan["refuse"] = 2
        assert _spawn(rig, req)["line"].endswith(": unauthenticated")
    rig.saas.plan["refuse"] = 0
    lease = _spawn(rig, "ok")
    assert lease["ev"] == "lease"
    assert [row["state"] for row in rig.broker.rows] == ["live"]
    rig.broker.dispatch(rig.peer, dict(op="status"))
    status = rig.peer.events[-1]
    assert status["ev"] == "status" and status["in_use_here"] == 1
    assert [row["state"] for row in status["rows"]] == ["live"]
    text = click.line("instance", expires_at=status["rows"][0]["expires_at"], in_use_here=status["in_use_here"])
    assert text.endswith("1 of 3 in use on this PC.")


def test_ghost_rows_never_fill_the_slots(rig):
    """r3 P2-1: three spawns refused as unauthenticated leave no slot taken; the next spawn is offered."""
    for req in ("a", "b", "c"):
        rig.saas.plan["refuse"] = 2
        assert _spawn(rig, req)["ev"] == "spawn_failed"
    rig.saas.plan["refuse"] = 0
    assert _spawn(rig, "d")["ev"] == "lease"


def test_cached_result_keeps_the_simulation_time_and_says_cached(tmp_path):
    """r3 P2-2: a cache hit keeps the first simulation's time as provenance and one visible cached line."""
    from tests.test_nodesim_click import Rig
    rig = Rig(tmp_path)
    first = rig.wait(rig.start()["id"])
    assert first["state"] == "done" and first["cached"] is False
    prov1 = first["result"]["provenance"]
    assert prov1["cached_line"] is None and "Cached" not in prov1["line"]
    time.sleep(1.2)                                   # a cache hit one second later must not show its own time
    second = rig.start()
    assert second["state"] == "done" and second["cached"] is True
    prov2 = second["result"]["provenance"]
    assert prov2["time"] == prov1["time"]
    hhmmss = prov1["time"].split(" ")[-1]
    assert prov2["cached_line"] == f"Cached result from {hhmmss} (same node, chip copy and library)."
    assert prov2["line"] == prov2["text"] + ". " + prov2["cached_line"]
    assert prov2["text"].endswith(prov1["time"])


def _pc_zone(html):
    """The PC-names zone of the settings page."""
    start = html.index('aria-label="PC names"')
    return html[start:html.index("Save PC names", start)]


@pytest.fixture
def monitor(env, tmp_path, monkeypatch):
    """The OPX monitor page rig of test_opx_monitor (fake worker, hand-polled), with no PC names on file."""
    monkeypatch.setattr(tom.om, "marker_dir", lambda: tmp_path / "markers")
    return tom, env


def _settings(tom, app):
    return _pc_zone(tom.page(app, "settings"))


def test_pc_names_sum_follows_the_read_state(monitor):
    """r3 P3-a: never read / read keep their words; an error says the read failed, a stale list says so."""
    tom, (app, m, worker, clock, _) = monitor
    zone = _settings(tom, app)
    assert "0 named - not read yet" in zone and "No tags named yet.</p>" in zone
    m.poll_once()
    assert m.status()["state"] == "ok"
    zone = _settings(tom, app)
    assert "0 named - 0 seen in open QMs" in zone and "No tags named yet and none seen in an open QM.</p>" in zone
    clock.t += 5
    worker.script[:] = [tom.om.WorkerError("Connection refused")]
    m.poll_once()
    assert m.status()["state"] == "stale"
    zone = _settings(tom, app)
    assert "0 named - 0 seen at the last good read" in zone
    assert "none seen in an open QM at the last good read; whether one is open now is unknown." in zone


def test_pc_names_sum_in_the_error_state_names_the_failed_read(monitor):
    """r3 P3-a: no successful read yet -> "unavailable" and the failed read, never "none seen in an open QM"."""
    tom, (app, m, worker, clock, _) = monitor
    worker.script[:] = [tom.om.WorkerError("Connection refused")]
    m.poll_once()
    clock.t += 5
    m.poll_once()
    assert m.status()["state"] == "error" and m.status()["answer"] is None
    zone = _settings(tom, app)
    assert "0 named - unavailable" in zone
    assert "Open QM list: no successful read yet, so the tags in open QMs are unknown." in zone
    assert "none seen" not in zone


def test_syntax_error_node_is_listed_and_refused(tmp_path, monkeypatch):
    """r3 P3-4: a node file that fails to parse stays in the list, refused, naming the line and the message."""
    from quam_state_manager.core.hub_index import close_readers
    from tests.test_nodesim_routes import App
    app = App(tmp_path, monkeypatch)
    try:
        (app.calib / "12_broken.py").write_text('"""Broken."""\nx = 1\ndef (:\n', encoding="utf-8")
        (app.calib / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
        j = app.c.get("/api/nodesim/nodes").get_json()
        rows = {x["file_name"]: x for x in j["items"]}
        assert "helpers.py" not in rows                      # a plain helper is still not a node entry
        row = rows["12_broken.py"]
        assert row["refused"] is True
        assert "syntax error at line 3: invalid syntax" in row["reason"]
        assert row["refused_line"] == click.line("refused", reason=row["reason"])
        assert rows["11_rabi_demo.py"]["refused"] is False
    finally:
        close_readers()
