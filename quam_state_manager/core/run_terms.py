"""What a run IS, read one way everywhere (docs/254).

An approval is a promise about exactly what a person saw on the card: the
node, its targets and its params. The ask-all check in ``run_node``, the
"same ask twice is one request" dedupe and the cards all read a run through
these functions, so a run the person never saw can never match one they
allowed (D-05: an "Allow run" for ``{load_data_id: 9}`` was spent on
``{num_shots: 100000}``).

Normalization (deliberately narrow -- a difference is a new approval):

* targets: a SET (order and repeats do not change what runs), natural order;
* params: keys sorted; an integral float equals its int (``100.0 == 100``:
  the node's own parameter model reads them as one value); a bool is never
  a number (``True != 1``); a string is never a number (``"100" != 100``);
  lists keep their order.

The request checks below are pure; the web layer hands them the chip's
names and a run lookup. Every refusal is data: ``refused`` + ``how``.
"""

from __future__ import annotations

import difflib
import json
import math
from typing import Any, Callable

from quam_state_manager.core.loader import natural_key

_MISSING = object()


def canon(v: Any) -> Any:
    """One JSON value in the form two runs are compared in."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if math.isfinite(v) and v.is_integer() and abs(v) < 2 ** 53:
            return int(v)
        return v
    if isinstance(v, dict):
        return {str(k): canon(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [canon(x) for x in v]
    return str(v)


def targets_of(targets) -> list[str]:
    if isinstance(targets, str):
        targets = targets.replace(",", " ").split()
    return sorted({str(t).strip() for t in (targets or []) if str(t).strip()}, key=natural_key)


def terms(node: str | None, targets, params: dict | None, plan_id: str | None = None) -> dict:
    """The run as compared: node, target SET, canonical params, the plan it belongs to."""
    return {"node": str(node or ""), "targets": targets_of(targets),
            "params": canon(dict(params or {})), "plan_id": plan_id or None}


def _j(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def key(node: str | None, targets, params: dict | None, plan_id: str | None = None) -> str:
    return _j(terms(node, targets, params, plan_id))


def differences(allowed: dict, asked: dict) -> list[dict]:
    """What separates the run a person allowed from the run asked for: one
    row per field (``node``, ``targets``, ``plan_id``) or param
    (``params.<name>``), each with both values. Empty = the same run."""
    a, b = terms(**allowed), terms(**asked)
    out: list[dict] = []
    for f in ("node", "targets", "plan_id"):
        if _j(a[f]) != _j(b[f]):
            out.append({"field": f, "allowed": a[f], "asked": b[f]})
    for k in sorted(set(a["params"]) | set(b["params"]), key=natural_key):
        av, bv = a["params"].get(k, _MISSING), b["params"].get(k, _MISSING)
        if av is _MISSING or bv is _MISSING or _j(av) != _j(bv):
            out.append({"field": f"params.{k}",
                        "allowed": "(not set)" if av is _MISSING else av,
                        "asked": "(not set)" if bv is _MISSING else bv})
    return out


def params_text(params: dict | None, limit: int = 160) -> str:
    """``num_shots=100 load_data_id=9`` -- the journal's spelling of a run's
    params; ``node defaults`` when there are none."""
    p = canon(dict(params or {}))
    if not p:
        return "node defaults"
    parts = [f"{k}={_j(v) if not isinstance(v, str) else v}" for k, v in p.items()]
    s = " ".join(parts)
    return s if len(s) <= limit else s[:limit - 2].rstrip() + " …"


# ------------------------------------------------------------ the request

def same_node(experiment_name: str | None, node_name: str | None) -> bool:
    """A run folder belongs to a node when its experiment name starts with the
    node's name, both normalized -- the rule run attribution uses."""
    want = _norm(node_name)
    return bool(want) and _norm(experiment_name).startswith(want)


def _norm(name: str | None) -> str:
    from quam_state_manager.core import story
    return story._norm(name or "")


def available_names(available, asked: str | None = None) -> tuple[list[str], list[str]]:
    """Every node name in the calibrations folder (A-20: the refusal listed 40
    of 187), and the few closest to what was asked."""
    names = sorted({getattr(i, "name", None) or str(i) for i in (available or [])}, key=natural_key)
    close = difflib.get_close_matches(str(asked or ""), names, n=5, cutoff=0.5) if asked else []
    return names, close


def reserved_params(params: dict | None, targets_name: str | None = None) -> list[str]:
    """Param names SM owns for every run (Dry run, the targets): the chassis
    drops them silently, so a request naming one is refused instead."""
    from quam_state_manager.core.node_inject import RESERVED_OVERRIDE_KEYS
    reserved = set(RESERVED_OVERRIDE_KEYS) | ({targets_name} if targets_name else set())
    return sorted((k for k in (params or {}) if k in reserved), key=natural_key)


def request_refusal(node_info, targets: list[str], params: dict | None, *, qubits, pairs,
                    get_run: Callable[[int], dict | None] | None = None) -> dict | None:
    """Why this run cannot be what the person is asked about, or None.

    * ``reserved_params`` -- ``simulate`` / ``qubits`` / ``qubit_pairs`` /
      ``targets`` (and the node's own targets field) are SM's; a run that
      names them would silently run with other values than the card shows.
    * ``wrong_target_kind`` -- a qubit for a pair node (or the reverse): the
      node's targets field never receives it and the node runs on its own
      defaults.
    * ``replay_other_node`` -- ``load_data_id`` names a run another node
      made; the replay would analyse data this node never took.
    """
    params = params or {}
    tname = getattr(node_info, "targets_name", None)
    res = reserved_params(params, tname)
    if res:
        return {"refused": "reserved_params", "params": res,
                "how": f"{', '.join(res)} {'is' if len(res) == 1 else 'are'} SM's to set (Dry run from the Runner "
                       "settings, the targets from `targets`); remove "
                       f"{'it' if len(res) == 1 else 'them'} from params"}
    if tname in ("qubits", "qubit_pairs"):
        want = set(pairs or []) if tname == "qubit_pairs" else set(qubits or [])
        other = set(qubits or []) if tname == "qubit_pairs" else set(pairs or [])
        wrong = [t for t in targets if t not in want and t in other]
        if wrong:
            kind = "qubit pairs" if tname == "qubit_pairs" else "qubits"
            return {"refused": "wrong_target_kind", "node": node_info.name, "targets_name": tname,
                    "wrong": wrong, "known": sorted(want, key=natural_key)[:80],
                    "how": f"{node_info.name} runs on {kind}; {', '.join(wrong)} "
                           f"{'is' if len(wrong) == 1 else 'are'} not one -- the node would never receive "
                           f"{'it' if len(wrong) == 1 else 'them'} and would run on its own defaults"}
    rid = params.get("load_data_id")
    if rid is not None:
        try:
            if isinstance(rid, bool) or float(rid) != int(float(rid)):
                raise ValueError
            rid_i = int(float(rid))
        except (TypeError, ValueError):
            return {"refused": "bad_load_data_id", "load_data_id": rid,
                    "how": "load_data_id is a run number (an integer)"}
        run = None
        if get_run is not None:
            try:
                run = get_run(rid_i)
            except Exception:  # noqa: BLE001 -- an unreadable store proves nothing
                run = None
        if run and not same_node(run.get("experiment_name"), node_info.name):
            return {"refused": "replay_other_node", "load_data_id": rid_i,
                    "run_experiment": run.get("experiment_name"), "node": node_info.name,
                    "how": f"run #{rid_i} was made by {run.get('experiment_name')}, not {node_info.name}; "
                           f"a replay re-analyses that node's own data -- name a run of {node_info.name}"}
    return None
