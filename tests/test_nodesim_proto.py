"""Offline protocol pins for P2 spec 4 and review_r1 (D-11, D-13, D-14)."""

import hashlib
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest


def load_proto():
    """Load without a package context, as required by review P2-11."""
    path = Path(__file__).resolve().parents[1] / "quam_state_manager/generator/nodesim_proto.py"
    spec = importlib.util.spec_from_file_location("protocol_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load_proto()


def hello():
    """Return the worker handshake of P2 spec 4.2 and review P1-2."""
    return dict(op="hello", proto=1, key="key", scratch="scratch", chip_dir="copy", library="library",
                idle_s=1200, saas_host="192.0.2.10")


def test_hello_requires_saas_host():
    """Pin review P1-2's required handshake field."""
    msg = hello()
    assert p.validate(msg) is msg
    del msg["saas_host"]
    with pytest.raises(ValueError, match="saas_host"):
        p.validate(msg)


@pytest.mark.parametrize("host,port", [
    ("127.0.0.1", 443), ("10.1.2.3", 443), ("169.254.1.2", 443), ("0.0.0.0", 443),
    ("100.64.0.1", 443), ("240.0.0.1", 443),
    ("192.0.2.10", 9510), ("192.0.2.10", 19510), ("::1", 443), ("::", 443),
    ("fc00::1", 443), ("fe80::1", 443), ("::ffff:127.0.0.1", 443), ("224.0.0.1", 443),
    ("172.16.1.1", 443), ("192.168.1.1", 443), ("192.0.2.10", 0), ("192.0.2.10", True),
])
def test_host_refused(host, port):
    """Pin forbidden endpoints without contacting a host (D-11, review P1-2)."""
    assert not p.is_allowed_saas_host(host, port)


def test_host_allowed():
    """Pin the offline documentation address exception (review P1-2)."""
    assert p.is_allowed_saas_host("192.0.2.10", 443)


def test_resolver_all_addresses():
    """Pin injected DNS and reject mixed safe/unsafe answers (review P1-2)."""
    calls = []

    def resolver(host, port, **kwargs):
        """Return offline getaddrinfo-shaped answers (review P1-2)."""
        calls.append((host, port, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in answers]

    answers = ["192.0.2.10", "192.0.2.11"]
    assert p.is_allowed_saas_host("fixture.invalid", 443, resolver)
    answers.append("10.0.0.1")
    assert not p.is_allowed_saas_host("fixture.invalid", 443, resolver)
    answers.clear()
    assert not p.is_allowed_saas_host("fixture.invalid", 443, resolver)
    assert len(calls) == 3


def test_codec():
    """Pin marker isolation, compact ASCII, escaped newlines and mismatch (P2 spec 4.1)."""
    msg = hello()
    msg["scratch"] = "line\n" + chr(233)
    line = p.encode(msg)
    assert line.isascii() and line.count("\n") == 1 and line.endswith("\n")
    assert ": " not in line and ", " not in line
    assert p.decode(line) == msg
    assert p.decode(line.encode("ascii")) == msg
    assert p.decode(json.dumps(msg)) is None
    assert p.decode(b"foreign") is None
    msg["proto"] = 2
    with pytest.raises(ValueError, match="proto_mismatch"):
        p.decode(p.MARKER + json.dumps(msg))
    with pytest.raises(ValueError, match="JSON"):
        p.decode(p.MARKER + "{")


@pytest.mark.parametrize("msg,match", [
    ({"op": "unknown_op"}, "unknown_op"), ({"ev": "unknown_event"}, "unknown_event"),
    ({"op": "exit", "ev": "idle"}, "exactly one"), ({"op": []}, "unknown"),
    ({"op": "cluster", "seq": True}, "seq"), ({"op": "cluster"}, "seq"),
])
def test_invalid_messages(msg, match):
    """Pin names, required fields and exact types (P2 spec 4.2-4.5)."""
    with pytest.raises(ValueError, match=match):
        p.validate(msg)


def example(descriptor):
    """Build complete schema examples for table coverage (P2 spec 4)."""
    if isinstance(descriptor, dict):
        return {key: example(value) for key, value in descriptor.items()}
    if isinstance(descriptor, frozenset):
        return sorted(descriptor)[0]
    if isinstance(descriptor, tuple):
        return example(descriptor[0])
    return {str: "value", int: 1, float: 1.0, bool: True, list: [], dict: {}, type(None): None}[descriptor]


@pytest.mark.parametrize("tag,name", [(tag, name) for tag, schemas in
                         (("op", p.OP_FIELDS), ("ev", p.EVENT_FIELDS)) for name in schemas])
def test_every_schema(tag, name):
    """Pin all table messages, missing keys and wrong field types (P2 spec 4)."""
    schema = (p.OP_FIELDS if tag == "op" else p.EVENT_FIELDS)[name]
    msg = dict(example(schema), **{tag: name})
    if "limit" in msg:
        msg["limit"] = 3
    assert p.decode(p.encode(msg)) == msg
    for field in schema:
        broken = dict(msg)
        del broken[field]
        with pytest.raises(ValueError, match=field):
            p.validate(broken)
        broken[field] = object()
        with pytest.raises(ValueError, match=field):
            p.validate(broken)


def test_broker_hello():
    """Pin the separate broker handshake and hello_ok provenance (review P2-11)."""
    msg = dict(example(p.BROKER_HELLO), op="hello")
    assert p.validate(msg) is msg
    for field in p.BROKER_HELLO:
        broken = dict(msg)
        del broken[field]
        with pytest.raises(ValueError):
            p.validate(broken)
    with pytest.raises(ValueError, match="qm_saas_file"):
        p.validate(dict(ev="hello_ok", account="account"))


def test_review_names():
    """Pin review P2-5, P2-8 and P3-6 additions beyond the spec."""
    assert {"swapped"} <= p.BROKER_OPS
    assert "swapped" in p.BROKER_EVENTS
    assert {"swap_failed", "stopped_in_job"} <= p.RELEASE_WHYS
    assert "proto_mismatch" in p.WORKER_EVENTS & p.BROKER_EVENTS & p.ERROR_CODES
    for why in p.RELEASE_WHYS:
        p.validate(dict(op="release", instance_id="id", why=why))
    with pytest.raises(ValueError, match="why"):
        p.validate(dict(op="release", instance_id="id", why="unknown"))


def test_contract_table_coverage():
    """Independently pin all op/event names in P2 spec 4 and the review additions."""
    assert p.OPS == set("hello cluster lease drop_lease click stop exit acquire touch in_job cancel release "
                        "status bye swapped".split())
    assert p.EVENTS == set("ready cluster lease_ok lease_failed lease_dropped phase build_started build_done "
                           "need_lease result guard idle bye hello_ok account_mismatch login_failed spawning "
                           "lease no_slot spawn_failed cancelled closed status renew_offer replaced swapped "
                           "already_running proto_mismatch".split())
    assert set(p.OP_FIELDS) | {"hello"} == p.OPS
    assert set(p.EVENT_FIELDS) == p.EVENTS
    assert (p.KIND_NEEDS_INSTANCE, p.KIND_EXPIRED) == ("needs_instance", "expired")


@pytest.mark.parametrize("msg,match", [
    (dict(ev="ready", pid=1, proto=1, imports_s=1.0, quam_config_file="file", qm_version="version",
          sources={"file": [1, True]}), "sources"),
    (dict(ev="build_done", click_id="click", s=1, sources_new={"file": [1]}), "sources_new"),
    (dict(ev="phase", click_id="click", phase="build", phases=[dict(name="build", s=True)]), "phases"),
    (dict(ev="closed", instance_id="id", why="unknown"), "why"),
    (dict(ev="spawning", req="req", in_use_here=1, limit=4), "limit"),
    (dict(ev="proto_mismatch", proto=True), "proto"),
])
def test_nested_values(msg, match):
    """Pin nested stats, measured times, close reasons and version types (P2 spec 4)."""
    with pytest.raises(ValueError, match=match):
        p.validate(msg)


def test_cluster_hash_canonical_float():
    """Pin exact JSON spacing, float repr and key sorting (D-13, P2 spec 4.4)."""
    first = {"mw": [1, 0.1, 1e-07], "lf": [2]}
    second = {"lf": [2], "mw": [1, 0.1, 1e-07]}
    payload = b'{"lf": [2], "mw": [1, 0.1, 1e-07]}'
    assert p.cluster_hash(first) == hashlib.sha256(payload).hexdigest()[:16]
    assert p.cluster_hash(first) == p.cluster_hash(second)
    second["mw"][1] = 0.10000000000000002
    assert p.cluster_hash(first) != p.cluster_hash(second)


@pytest.mark.parametrize("field", ["seq", "store_id", "click_id"])
def test_snapshot_mismatch(field):
    """Reject stale copies and results on each identity (D-14, review P1-8)."""
    request = dict(seq=1, store_id="store", click_id="click")
    assert p.check_snapshot(request, dict(request)) is True
    result = dict(request)
    result[field] = 2 if field == "seq" else "other"
    with pytest.raises(ValueError, match=field):
        p.check_snapshot(request, result)


class _FakeCluster:
    """Record the ClusterConfig surface the P1 child proved on the cloud simulator."""

    def __init__(self):
        self.calls = []

    def controller(self, *args):
        self.calls.append(("controller",) + args)
        return SimpleNamespace(mw_fems=lambda *s: self.calls.append(("mw",) + s),
                               lf_fems=lambda *s: self.calls.append(("lf",) + s))


def test_cluster_spec_and_mapping():
    """Slots merged over controllers, non-MW counted as LF, one controller, plural calls (P2 spec 4.4)."""
    config = {"controllers": {"con_b": {"fems": {"2": {"type": "LF"}, "5": {"type": "OTHER"}}},
                              "con_a": {"fems": {3: {"type": "MW"}, 1: {"type": "MW"}}}}}
    spec = p.cluster_spec(config)
    assert spec == {"mw": [1, 3], "lf": [2, 5]}
    assert p.cluster_spec({}) == {"mw": [], "lf": []}
    cluster = p.cluster_config(spec, SimpleNamespace(ClusterConfig=_FakeCluster))
    assert cluster.calls == [("controller",), ("mw", 1, 3), ("lf", 2, 5)]
    empty = p.cluster_config({"mw": [], "lf": [4]}, SimpleNamespace(ClusterConfig=_FakeCluster))
    assert empty.calls == [("controller",), ("lf", 4)]


def test_cluster_config_matches_p1_child(monkeypatch):
    """The one mapping gives the same ClusterConfig calls as the P1 child's cluster_of (contract 10)."""
    import sys
    import types
    fake = types.ModuleType("qm_saas")
    fake.ClusterConfig = _FakeCluster
    monkeypatch.setitem(sys.modules, "qm_saas", fake)
    from quam_state_manager.generator import nodesim_click
    for config in ({"controllers": {"c1": {"fems": {"1": {"type": "MW"}, "3": {"type": "LF"}}},
                                    "c2": {"fems": {"2": {"type": "MW"}, "1": {"type": "LF"}}}}},
                   {"controllers": {"c1": {"fems": {"4": {"type": "LF"}}}}}):
        old = nodesim_click.cluster_of(config).calls
        new = p.cluster_config(p.cluster_spec(config), fake).calls
        assert new == old
