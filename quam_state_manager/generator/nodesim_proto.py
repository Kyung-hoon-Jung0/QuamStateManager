"""Shared stdlib IPC contract: P2 spec 4, D-11, D-13 and D-14; review_r1 wins."""

import hashlib
import ipaddress
import json
import math
import socket

PROTO = 1
MARKER = "@@NODESIM1 "
WORKER_OPS = frozenset("hello cluster lease drop_lease click stop exit".split())
BROKER_OPS = frozenset("hello acquire touch in_job cancel release status bye swapped".split())
WORKER_EVENTS = frozenset(
    "ready cluster cluster_failed lease_ok lease_failed lease_dropped phase build_started build_done need_lease "
    "result guard idle bye proto_mismatch".split()
)
BROKER_EVENTS = frozenset(
    "hello_ok account_mismatch login_failed spawning lease no_slot spawn_failed cancelled closed status "
    "renew_offer replaced swapped already_running proto_mismatch".split()
)
OPS = WORKER_OPS | BROKER_OPS
EVENTS = WORKER_EVENTS | BROKER_EVENTS
RELEASE_WHYS = frozenset(
    "worker_died_in_job hash_changed stop_spawned sm_exit swap_failed stopped_in_job expired host_refused "
    "cold_fallback".split()
)
CLOSED_WHYS = frozenset("idle expired release reaper".split())
#: worker bye reasons; protocol_error is the one W sends for an unreadable SM message (review S P3-8)
BYE_WHYS = frozenset("guard idle exit proto_mismatch saas_env protocol_error".split())
ERROR_CODES = frozenset(
    "proto_mismatch account_mismatch login_failed lease_failed no_slot spawn_failed".split()
)
NEED_LEASE_REASONS = frozenset(("none", "hash_changed"))
ACQUIRE_REASONS = frozenset(("intent", "click", "renew"))
PHASES = frozenset("copy lock build lease connect submit wait report".split())
KIND_NEEDS_INSTANCE = "needs_instance"
KIND_EXPIRED = "expired"

# Field descriptors: exact primitives (bool is never an integer), nested maps and lists.
NUMBER = (int, float)
NULL_STRING = (str, type(None))
SPEC = {"mw": list, "lf": list}
HEADERS = {"simulation-id": str, "simulation-auth": str}
LEASE = dict(instance_id=str, host=str, port=int, headers=HEADERS, expires_at=str,
             version=str, cluster_hash=str)
SNAPSHOT = dict(seq=int, store_id=str, click_id=str)
#: D-50 (review int r1 P3-3): the ids a result carries until W has read the copy's _snapshot.json
UNREAD = dict(seq=-1, store_id="", click_id="")
WORKER_HELLO = dict(proto=int, key=str, scratch=str, chip_dir=str, library=str, idle_s=NUMBER, saas_host=str)
BROKER_HELLO = dict(proto=int, sm_pid=int, sm_created=NUMBER,
                    creds=dict(email=str, host=str, port=int, password=str))
OP_FIELDS = {
    "cluster": dict(seq=int),
    "lease": dict(LEASE, lease_seq=int),
    "drop_lease": dict(lease_seq=int),
    "click": dict(SNAPSHOT, node=str, targets=list, overrides=dict, window_ns=int, wait_cap_s=NUMBER),
    "stop": dict(click_id=str), "exit": {},
    "acquire": dict(req=str, owner=dict(sm_pid=int, worker_pid=int), version=str, spec=SPEC,
                    hash=str, reason=ACQUIRE_REASONS),
    "touch": dict(instance_id=str, click_at=NUMBER),
    "in_job": dict(instance_id=str, click_id=NULL_STRING),
    "cancel": dict(req=str), "release": dict(instance_id=str, why=RELEASE_WHYS),
    "status": {}, "bye": {}, "swapped": dict(old=str, new=str),
}
EVENT_FIELDS = {
    "ready": dict(pid=int, proto=int, imports_s=NUMBER, quam_config_file=str, qm_version=str, sources=dict),
    "cluster": dict(seq=int, spec=SPEC, hash=str),
    "cluster_failed": dict(seq=int, message=str),
    "lease_ok": dict(lease_seq=int, connect_s=(int, float, type(None))),
    "lease_failed": dict(lease_seq=int, message=str), "lease_dropped": dict(lease_seq=int),
    "phase": dict(click_id=str, phase=PHASES, phases=list),
    "build_started": dict(click_id=str), "build_done": dict(click_id=str, s=NUMBER, sources_new=dict),
    "need_lease": dict(click_id=str, spec=SPEC, hash=str, reason=NEED_LEASE_REASONS),
    "result": dict(SNAPSHOT, ok=bool, kind=str, message=str, report=(dict, type(None)), script=str,
                   script_ok=bool, program_chars=int, qop_version=str, window_ns=int,
                   last_adc_end_ns=(int, float, type(None)), lease_seq=(int, type(None)),
                   cluster_hash=str, dropped_overrides=list, replaced_hosts=list),
    "guard": dict(click_id=str, line=str), "idle": {}, "bye": dict(why=BYE_WHYS),
    "hello_ok": dict(account=str, qm_saas_file=str), "account_mismatch": {},
    "login_failed": dict(line=str), "spawning": dict(req=str, in_use_here=int, limit=int),
    "lease": dict(LEASE, req=str, spawned=bool, spawn_s=NUMBER),
    "no_slot": dict(req=str, in_use_here=int, earliest_close=NULL_STRING), "spawn_failed": dict(req=str, line=str),
    "cancelled": dict(req=str), "closed": dict(instance_id=str),
    "status": dict(rows=list, in_use_here=int, limit=int),
    "renew_offer": dict(instance_id=str, expires_at=str), "replaced": dict(old=str, lease=LEASE),
    "swapped": dict(old=str, new=str),
    "already_running": {}, "proto_mismatch": dict(proto=int),
}


def _check(value, descriptor, path):
    """Check a field descriptor (P2 spec 4.2-4.5)."""
    if isinstance(descriptor, dict):
        if type(value) is not dict:
            raise ValueError(path + " must be an object")
        for key, expected in descriptor.items():
            if key not in value:
                raise ValueError(path + "." + key + " is required")
            _check(value[key], expected, path + "." + key)
    elif isinstance(descriptor, frozenset):
        if type(value) is not str or value not in descriptor:
            raise ValueError(path + " has an unknown value")
    else:
        types = descriptor if isinstance(descriptor, tuple) else (descriptor,)
        if type(value) not in types or (type(value) is float and not math.isfinite(value)):
            raise ValueError(path + " has an invalid type or value")


def validate(msg):
    """Validate either channel; return the message or raise ValueError (P2 spec 4)."""
    if type(msg) is not dict or ("op" in msg) == ("ev" in msg):
        raise ValueError("exactly one op or ev is required")
    tag = "op" if "op" in msg else "ev"
    name = msg[tag]
    schemas = OP_FIELDS if tag == "op" else EVENT_FIELDS
    if tag == "op" and name == "hello":
        schema = BROKER_HELLO if "creds" in msg or "sm_pid" in msg else WORKER_HELLO
    elif type(name) is not str or name not in schemas:
        raise ValueError("unknown " + tag + ": " + str(name))
    else:
        schema = schemas[name]
    if "proto" in msg:
        _check(msg["proto"], int, "proto")
        if msg["proto"] != PROTO and name != "proto_mismatch":
            raise ValueError("proto_mismatch")
    _check(msg, schema, name)
    for key in ("sources", "sources_new"):
        if key in msg:
            for path, stat in msg[key].items():
                if type(path) is not str or type(stat) is not list or len(stat) != 2:
                    raise ValueError(key + " must map paths to [mtime_ns, size]")
                for number in stat:
                    _check(number, int, key)
    if name == "phase":
        for phase in msg["phases"]:
            _check(phase, dict(name=str, s=NUMBER), "phases")
    if name == "closed" and "why" in msg:
        _check(msg["why"], CLOSED_WHYS, "closed.why")
    if "limit" in msg and msg["limit"] != 3:
        raise ValueError("limit must be 3")
    return msg


def encode(msg):
    """Return an ASCII protocol line, including its terminating newline (P2 spec 4.1)."""
    validate(msg)
    return MARKER + json.dumps(msg, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n"


def encode_request(msg):
    """Return one plain JSON line for SM -> worker stdin; the marker frames worker -> SM only (D-45)."""
    validate(msg)
    return json.dumps(msg, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n"


def same_created(a, b):
    """One PID-reuse check for two creation times of the same process: |a - b| <= 0.01 s (D-46)."""
    return type(a) in (int, float) and type(b) in (int, float) and abs(a - b) <= 0.01


def decode(line):
    """Ignore foreign lines; reject malformed marked messages (P2 spec 4.1, review P3-6)."""
    if isinstance(line, bytes):
        if not line.startswith(MARKER.encode("ascii")):
            return None
        line = line.decode("ascii")
    if not line.startswith(MARKER):
        return None
    try:
        msg = json.loads(line[len(MARKER):])
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid protocol JSON") from exc
    return validate(msg)


def check_snapshot(request, snapshot):
    """Reject a mismatched copy or result using all identities (D-14, review P1-8)."""
    _check(request, SNAPSHOT, "request")
    _check(snapshot, SNAPSHOT, "snapshot")
    for key in SNAPSHOT:
        if request[key] != snapshot[key]:
            raise ValueError("snapshot mismatch: " + key)
    return True


def snapshot_unread(result):
    """True when every snapshot id of a result is still the UNREAD mark: W never read the copy (D-50)."""
    return all(result.get(key) == value and type(result.get(key)) is type(value) for key, value in UNREAD.items())


def cluster_hash(spec):
    """Hash canonical stdlib JSON with its default spacing (D-13, P2 spec 4.4)."""
    _check(spec, SPEC, "spec")
    payload = json.dumps(spec, sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


def cluster_spec(config):
    """The config's FEM slots by type, read exactly as the P1 child's ``cluster_of`` reads them (P2 spec 4.4):
    slots merged over all controllers (first type seen wins), MW slots under "mw", every other FEM under "lf",
    each list sorted."""
    slots = {}
    for controller in (config.get("controllers") or {}).values():
        for slot, fem in (controller.get("fems") or {}).items():
            slots.setdefault(int(slot), fem.get("type"))
    return {"mw": sorted(s for s, kind in slots.items() if kind == "MW"),
            "lf": sorted(s for s, kind in slots.items() if kind != "MW")}


def cluster_config(spec, module):
    """The one spec -> ``module.ClusterConfig`` mapping (review P3-3), with the calls the P1 child proved on the
    cloud simulator: one controller, ``mw_fems(*slots)`` and ``lf_fems(*slots)``."""
    _check(spec, SPEC, "spec")
    for kind in ("mw", "lf"):
        for slot in spec[kind]:
            _check(slot, int, "spec." + kind)
    cluster = module.ClusterConfig()
    controller = cluster.controller()
    if spec["mw"]:
        controller.mw_fems(*spec["mw"])
    if spec["lf"]:
        controller.lf_fems(*spec["lf"])
    return cluster


def is_allowed_saas_host(host, port, resolver=None):
    """Check every resolved address, allowing offline TEST-NET fixtures (D-11, review P1-2)."""
    if type(host) is not str or not host or type(port) is not int or not 1 <= port <= 65535:
        return False
    if port in (9510, 19510):
        return False
    try:
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            resolve = socket.getaddrinfo if resolver is None else resolver
            addresses = [ipaddress.ip_address(row[4][0]) for row in resolve(host, port, type=socket.SOCK_STREAM)]
        if not addresses:
            return False
        for address in addresses:
            address = getattr(address, "ipv4_mapped", None) or address
            test_net = address.version == 4 and any(address in ipaddress.ip_network(net) for net in
                                                   ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
            if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
                return False
            if not address.is_global and not test_net:
                return False
        return True
    except (OSError, ValueError, TypeError, IndexError):
        return False
