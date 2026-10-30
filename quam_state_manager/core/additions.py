"""Generate-Config additions (lab additions, phase 2: pulses).

A spec may carry ``spec.additions`` -- objects the official builder does not
write, added to the built chip afterwards::

    {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "x180_flat",
     "class": "<module>.<Class>", "fields": {...}}

The build itself stays the official one. After it saved ``state.json`` into
the output folder, :func:`apply_additions` adds each addition as a JSON pass
here in SM (SM never imports quam), and :func:`apply_and_verify` then asks the
selected env -- a fresh ``Quam.load`` + ``generate_config`` and, for a class
SM does not draw itself, the class's own ``calculate_waveform()``. An addition
that fails is removed ALONE and reported with its reason; the rest of the chip
is never blocked by it (degrade-only).

Every rule below is [derived]: it is a reading of how quam serialises a
dataclass (every key must be a declared field of the class ``__class__``
names, required fields have no default, a ``#`` string is a reference that
must land), checked by the env load, never cited.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from quam_state_manager.core.field_kinds import _norm
from quam_state_manager.core.pointer_path import _walk, pointer_to_abs

logger = logging.getLogger(__name__)

KINDS = ("pulse",)
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_TARGET_RE = re.compile(r"^[A-Za-z_][\w\-]*(\.[\w\-]+)+\.operations$")
_CLASS_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
_OFFICIAL = ("quam", "quam_builder", "qualang_tools", "qm")

# wizard line type -> the channel attribute the official builder writes
_QUBIT_CHANNEL = {"xy": "drive", "z": "flux", "resonator": "resonator"}
_PAIR_CHANNEL = {"coupler": "coupler"}
_TWPA_CHANNEL = {"pump": "twpa_pump", "pump_": "twpa_pump", "isolation": "twpa_isolation",
                 "isolation_": "twpa_isolation"}


def is_pointer(v: Any) -> bool:
    return isinstance(v, str) and v.startswith("#")


def _pair_ids(spec: dict) -> set[str]:
    out = set()
    for p in spec.get("qubit_pairs") or []:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            a, b = str(p[0]), str(p[1])
            out.add(f"{a}-{b}")
            m = re.match(r"^([A-Za-z_]+)(\d+)$", b)
            if m and a.startswith(m.group(1)):
                out.add(f"{a}-{m.group(2)}")      # the builder's short form q1-2
        elif isinstance(p, str):
            out.add(p)
    return out


# ---------------------------------------------------------------------------
# structural check (validate_spec)
# ---------------------------------------------------------------------------

def structural_errors(spec: dict) -> list[str]:
    """Shape errors that make ``spec.additions`` unusable as a whole (400)."""
    adds = spec.get("additions")
    if adds is None:
        return []
    if not isinstance(adds, list):
        return ["additions: must be a list"]
    errors = []
    seen = set()
    for i, a in enumerate(adds):
        where = f"additions[{i}]"
        if not isinstance(a, dict):
            errors.append(f"{where}: must be an object")
            continue
        if a.get("kind") not in KINDS:
            errors.append(f"{where}: unknown kind {a.get('kind')!r} (expected 'pulse')")
        t, n, c = a.get("target"), a.get("name"), a.get("class")
        if not isinstance(t, str) or not _TARGET_RE.match(t):
            errors.append(f"{where}: target must be the dot path of a channel's "
                          f"operations dict (got {t!r})")
        if not isinstance(n, str) or not _NAME_RE.match(n):
            errors.append(f"{where}: name must be letters, digits, '_' or '-' (got {n!r})")
        if not isinstance(c, str) or not _CLASS_RE.match(c):
            errors.append(f"{where}: class must be a dotted module.Class path (got {c!r})")
        if not isinstance(a.get("fields", {}), dict):
            errors.append(f"{where}: fields must be an object")
        key = (t, n)
        if isinstance(t, str) and isinstance(n, str):
            if key in seen:
                errors.append(f"{where}: {t}.{n} is added twice")
            seen.add(key)
    return errors


# ---------------------------------------------------------------------------
# per-addition checks
# ---------------------------------------------------------------------------

def spec_channel_reason(spec: dict, target: str) -> str | None:
    """Why the spec cannot have *target*'s channel, or None. Only shapes the
    wizard knows are judged; anything else is left to the built chip."""
    segs = target.split(".")
    lines = [ln for ln in (spec.get("lines") or []) if isinstance(ln, dict)]
    if segs[0] == "qubits" and len(segs) == 4:
        q, ch = segs[1], segs[2]
        if q not in {str(x) for x in spec.get("qubits") or []}:
            return f"qubit {q} is not in this chip"
        need = _QUBIT_CHANNEL.get(ch)
        if need and not any(ln.get("line") == need and str(ln.get("element")) == q
                            for ln in lines):
            return f"qubit {q} has no {need} line, so it has no {ch} channel"
    elif segs[0] == "qubit_pairs" and len(segs) == 4:
        p, ch = segs[1], segs[2]
        ids = _pair_ids(spec)
        if p not in ids:
            return f"pair {p} is not in this chip"
        need = _PAIR_CHANNEL.get(ch)
        if need and not any(ln.get("line") == need and str(ln.get("element")) in ids
                            and _same_pair(str(ln.get("element")), p) for ln in lines):
            return f"pair {p} has no {need} line"
    elif segs[0] == "twpas" and len(segs) == 4:
        t = segs[1]
        if t not in {str(x) for x in spec.get("twpas") or []}:
            return f"TWPA {t} is not in this chip"
    return None


def _same_pair(a: str, b: str) -> bool:
    def nums(s):
        return re.findall(r"\d+", s)
    return nums(a) == nums(b)


def _class_entry(manifest: dict | None, qclass: str) -> dict | None:
    if not manifest:
        return None
    c = (manifest.get("classes") or {}).get(qclass)
    return c if isinstance(c, dict) else None


def origin_of(qclass: str, manifest: dict | None) -> str:
    c = _class_entry(manifest, qclass)
    if c and c.get("origin"):
        return c["origin"]
    return "official" if qclass.split(".", 1)[0] in _OFFICIAL else "lab"


def _type_ok(raw: str, kind: str, value: Any) -> bool:
    """A literal value against the field's declared kind. A pointer and an
    unknown kind are always accepted (the env load decides)."""
    if is_pointer(value) or kind == "unknown":
        return True
    num = isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "scalar_or_pointer":
        return num or isinstance(value, str)
    if kind in ("pointer_pulse", "pointer_channel", "pointer_or_value", "inline_object"):
        return isinstance(value, dict) and "__class__" in value or isinstance(value, str)
    if kind in ("dict_of_pointers", "dict_of_objects"):
        return isinstance(value, dict)
    t = _norm(raw or "")
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "float":
        return num
    if t == "bool":
        return isinstance(value, bool)
    if t == "str":
        return isinstance(value, str)
    if t.split("[")[0] in ("list", "List", "tuple", "Tuple", "Sequence"):
        return isinstance(value, list)
    return True


def manifest_reason(add: dict, manifest: dict | None) -> str | None:
    """Why the env's role manifest refuses *add*, or None. No manifest: None
    (an unknown is never a blocker -- the env build decides)."""
    if not manifest:
        return None
    qclass = add["class"]
    c = _class_entry(manifest, qclass)
    if c is None:
        return (f"class {qclass} is not in this environment (not importable, or not "
                "in a module the environment was asked to look in)")
    if c.get("role") != "pulse":
        return f"class {qclass} is a {c.get('role')!r}, not a pulse"
    if c.get("abstract"):
        return f"class {qclass} is abstract and cannot be created"
    fields = c.get("fields") or {}
    if not fields:
        return None
    given = add.get("fields") or {}
    unknown = sorted(k for k in given if k not in fields)
    if unknown:
        return (f"{', '.join(unknown)} {'is not a field' if len(unknown) == 1 else 'are not fields'}"
                f" of {qclass.rsplit('.', 1)[-1]} (Quam.load would refuse it)")
    missing = sorted(n for n, f in fields.items()
                     if not f.get("has_default") and n not in given)
    if missing:
        return f"required field{'s' if len(missing) > 1 else ''} missing: {', '.join(missing)}"
    for n, v in given.items():
        f = fields[n]
        if v is None:
            if not (f.get("optional") or f.get("has_default")):
                return f"field {n} cannot be empty"
            continue
        if not _type_ok(f.get("raw") or "", f.get("kind") or "plain", v):
            return (f"field {n}: {json.dumps(v)[:60]} does not fit its type "
                    f"{_norm(f.get('raw') or '?')}")
    return None


def schema_hash(manifest: dict | None, qclass: str) -> str | None:
    """sha256 over the class's field schema (name, raw type, default, kind)."""
    c = _class_entry(manifest, qclass)
    if c is None:
        return None
    f = c.get("fields") or {}
    canon = json.dumps({n: [v.get("raw"), v.get("has_default"), v.get("default_repr"),
                            v.get("kind")] for n, v in sorted(f.items())},
                       sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def body_for(add: dict) -> dict:
    body = {"__class__": add["class"]}
    for k, v in (add.get("fields") or {}).items():
        body[k] = copy.deepcopy(v)
    return body


def _dangling(state: dict, segs: list[str], body: dict) -> str | None:
    for k, v in body.items():
        if not is_pointer(v):
            continue
        dest = pointer_to_abs(v, segs + [k])
        ok, _ = _walk(state, dest) if dest is not None else (False, None)
        if not ok:
            return f"field {k}: {v} points at nothing in the built chip"
    return None


# ---------------------------------------------------------------------------
# the JSON pass
# ---------------------------------------------------------------------------

def _entry(i: int, add: dict, manifest: dict | None, reason: str | None = None) -> dict:
    e = {"index": i, "kind": add.get("kind"), "target": add.get("target"),
         "name": add.get("name"), "class": add.get("class"),
         "origin": origin_of(str(add.get("class") or ""), manifest)}
    if reason:
        e["reason"] = reason
    return e


def apply_additions(state: dict, spec: dict, manifest: dict | None) -> dict:
    """Add every acceptable addition to *state* IN PLACE.

    Returns ``{"added": [...], "refused": [...], "notes": [...]}``; each entry
    names ``index/target/name/class/origin`` (+ ``reason`` when refused)."""
    adds = [a for a in (spec.get("additions") or []) if isinstance(a, dict)]
    report: dict = {"added": [], "refused": [], "notes": []}
    if adds and not manifest:
        report["notes"].append(
            "The environment's class manifest was not available, so classes and "
            "fields were checked only by the environment's own load.")
    placed: list[tuple[int, dict, list[str]]] = []
    for i, a in enumerate(adds):
        reason = (spec_channel_reason(spec, a["target"]) or manifest_reason(a, manifest))
        segs = a["target"].split(".")
        if reason is None:
            ok, ops = _walk(state, segs)
            if not ok or not isinstance(ops, dict):
                reason = f"{a['target']} does not exist in the built chip"
            elif a["name"] in ops:
                reason = f"{a['target']} already has an operation named {a['name']}"
        if reason:
            report["refused"].append(_entry(i, a, manifest, reason))
            continue
        ops[a["name"]] = body_for(a)
        placed.append((i, a, segs + [a["name"]]))
    # pointers may name another addition, so they are judged on the final
    # state; a refusal can strand another's pointer, hence the fixpoint
    changed = True
    while changed:
        changed = False
        for item in list(placed):
            i, a, path = item
            why = _dangling(state, path, _walk(state, path)[1])
            if why:
                _walk(state, path[:-1])[1].pop(path[-1], None)
                placed.remove(item)
                report["refused"].append(_entry(i, a, manifest, why))
                changed = True
    report["added"] = [dict(_entry(i, a, manifest), path=".".join(p)) for i, a, p in placed]
    report["refused"].sort(key=lambda e: e["index"])
    return report


def remove(state: dict, path: str) -> None:
    segs = path.split(".")
    ok, parent = _walk(state, segs[:-1])
    if ok and isinstance(parent, dict):
        parent.pop(segs[-1], None)


# ---------------------------------------------------------------------------
# env verification
# ---------------------------------------------------------------------------

def _needs_own_code(qclass: str, manifest: dict | None) -> bool:
    from quam_state_manager.core import lab_watch
    return origin_of(qclass, manifest) == "lab" or lab_watch.is_lab_class(qclass)


def _write_folder(root: Path, name: str, state: dict, wiring_src: Path) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    shutil.copyfile(wiring_src, d / "wiring.json")
    return d


def apply_and_verify(python_path: str | None, out_dir, spec: dict,
                     manifest: dict | None, *, verify=None, timeout: int = 600) -> dict:
    """The JSON pass + env verification for a build that already wrote
    ``out_dir/state.json``. Rewrites that file with the additions that
    passed. *verify* (tests) replaces :func:`config_generator.run_additions_verify`.
    Never raises: a failure of the check itself is reported, and then no
    addition is kept (an unverified addition is never written)."""
    from quam_state_manager.core import config_generator, safe_io
    out_dir = Path(out_dir)
    verify = verify or config_generator.run_additions_verify
    if not spec.get("additions"):
        return {"added": [], "refused": [], "notes": []}
    try:
        base = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"added": [], "refused": [], "notes": [f"could not read the built state: {exc}"]}
    state = copy.deepcopy(base)
    report = apply_additions(state, spec, manifest)
    if not report["added"]:
        return report
    if not python_path:
        for e in report["added"]:
            report["refused"].append({**e, "reason": "no environment to verify it in"})
        report["added"] = []
        return report
    work = Path(tempfile.mkdtemp(prefix="quamadd_work_"))
    try:
        wiring = out_dir / "wiring.json"

        def _checks(entries):
            return [e["path"] for e in entries if _needs_own_code(e["class"], manifest)]

        def _run(folders: dict[str, tuple[Path, list[str]]]) -> dict:
            res = verify(python_path, {k: {"folder": str(f), "own_code": c}
                                       for k, (f, c) in folders.items()}, timeout=timeout)
            return res

        res = _run({"all": (_write_folder(work, "all", state, wiring), _checks(report["added"]))})
        verdict = (res.get("folders") or {}).get("all") if res.get("ok") else None
        if verdict is None:
            err = res.get("error") or "the environment check did not run"
            for e in report["added"]:
                report["refused"].append({**e, "reason": f"not verified: {err}"})
            report["added"] = []
            return report
        if not verdict.get("ok"):
            # one at a time: base + that addition alone
            singles = {}
            for e in report["added"]:
                st = copy.deepcopy(base)
                _walk(st, e["path"].split(".")[:-1])[1][e["name"]] = copy.deepcopy(
                    _walk(state, e["path"].split("."))[1])
                singles[str(e["index"])] = (_write_folder(work, f"one{e['index']}", st, wiring),
                                            _checks([e]))
            res = _run(singles)
            per = (res.get("folders") or {}) if res.get("ok") else {}
            keep = []
            for e in report["added"]:
                v = per.get(str(e["index"]))
                if v and v.get("ok"):
                    keep.append(e)
                else:
                    why = (v or {}).get("error") or res.get("error") or "the check did not run"
                    report["refused"].append({**e, "reason": f"the environment refused it: {why}"})
                    remove(state, e["path"])
            report["added"] = keep
            if keep:
                res = _run({"final": (_write_folder(work, "final", state, wiring), _checks(keep))})
                v = (res.get("folders") or {}).get("final") if res.get("ok") else None
                if not (v and v.get("ok")):
                    why = (v or {}).get("error") or res.get("error") or "the check did not run"
                    for e in keep:
                        report["refused"].append(
                            {**e, "reason": f"refused together with the others: {why}"})
                        remove(state, e["path"])
                    report["added"] = []
            report["refused"].sort(key=lambda e: e["index"])
        if report["added"]:
            safe_io.atomic_write_json(out_dir / "state.json", state)
            report["verified"] = {"load": True, "generate_config": True,
                                  "own_code": [e["path"] for e in report["added"]
                                               if _needs_own_code(e["class"], manifest)]}
        return report
    except Exception as exc:  # noqa: BLE001 -- degrade-only, never fail the build
        logger.exception("additions pass failed")
        for e in report["added"]:
            report["refused"].append({**e, "reason": f"not applied: {type(exc).__name__}: {exc}"})
        report["added"] = []
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def stamp(spec: dict, manifest: dict | None, python_path: str | None) -> list[dict]:
    """``spec.additions`` with the env identity + a per-addition schema hash,
    for the sidecar (so a later re-generate in another env can tell)."""
    versions = (manifest or {}).get("versions") or {}
    out = []
    for a in spec.get("additions") or []:
        if not isinstance(a, dict):
            continue
        b = copy.deepcopy(a)
        b["env"] = {"python": python_path, "versions": versions}
        b["schema_hash"] = schema_hash(manifest, str(a.get("class") or ""))
        out.append(b)
    return out
