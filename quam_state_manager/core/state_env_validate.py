"""State ↔ env-schema validation: THE resolver, THE judge, and the analyzer.

Single-source rules (the adversarial critique's P0 fixes — do not fork these):

* ``expected_type_for`` is the ONE path→TypeSpec resolver — a walk-DOWN of the
  annotation graph that re-anchors on every ``__class__``-bearing dict it
  meets (the actual serialized class wins over the annotation, so subclassed
  pulses under ``operations`` resolve exactly), descends dict-typed fields by
  any-key (int-string port keys ``ports.mw_outputs.con1.1.2.band`` and
  multi-DUC ``upconverters.2.frequency`` both work), consumes numeric
  segments into list ``item`` specs, and recurses through component-typed
  fields. It returns ``None`` — "the env layer abstains" — for wiring paths
  (no ``__class__``), unimportable classes, unknown fields, and ``any``
  dead-ends; the type-policy layer then falls to user assignment → value
  inference.
* ``judge`` is the ONE value-vs-TypeSpec judgment. It returns a verdict CODE;
  the edit gate and the retrospective validator map the same codes onto their
  own severity tiers (``EDIT_BLOCKING`` / ``VALIDATION_SEVERITY``) — one
  semantics, two enforcement times, zero drift.
* ``analyze_state`` is the retrospective validator: one walk producing
  aggregated findings (two-tier: load-breaking errors vs experiment-risk
  warnings) plus the bulk ``types`` map, memoizable per
  ``(mutation_seq, manifest-key)``.

Invariants (corpus-grounded): pointer strings (incl. ``#./inferred_*``)
always pass; null always passes at the edit gate; int passes where float is
expected (widening) and an integral float passes where int is expected —
VERBATIM, never rewritten (the int/float Hz-field instability across chip
generations would otherwise churn diffs forever); enum misses are warnings
in v1; ``extras.*``/``operations.*``/entity-named dict children are never
field-checked (only ``__class__``-bearing nodes are).
"""

from __future__ import annotations

import logging
import math
import weakref
from typing import Any

logger = logging.getLogger(__name__)

_POINTER_PREFIXES = ("#/", "#./", "#../")

# judge() verdict codes → tiers. ONE code table, two tier maps.
EDIT_BLOCKING = frozenset({
    "type_mismatch", "non_integral_int", "bool_in_numeric", "non_finite",
    "list_shape", "element_mismatch",
})
VALIDATION_SEVERITY = {
    "type_mismatch": "warning",
    "non_integral_int": "warning",
    "bool_in_numeric": "warning",
    "non_finite": "warning",
    "list_shape": "warning",
    "element_mismatch": "warning",
    "enum_miss": "warning",          # enum churns across fork generations — v1 never blocks
}


def is_pointer_str(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_POINTER_PREFIXES)


# ---------------------------------------------------------------------------
# manifest lookups
# ---------------------------------------------------------------------------

def _class_entry(manifest: dict, class_str: str) -> dict | None:
    """Exact class-path lookup, then unique-leaf fallback (class-move
    portability: quam-0.6.0 relocated pulse classes; the stored path may be a
    generation older than the env's defining home)."""
    classes = manifest.get("classes") or {}
    entry = classes.get(class_str)
    if isinstance(entry, dict):
        return entry
    leaf = class_str.rsplit(".", 1)[-1]
    homes = (manifest.get("by_leaf") or {}).get(leaf) or []
    if len(homes) == 1:
        entry = classes.get(homes[0])
        if isinstance(entry, dict):
            return entry
        # by_leaf carries CANONICAL paths; find the requested entry whose
        # canonical matches
        for e in classes.values():
            if isinstance(e, dict) and e.get("canonical") == homes[0]:
                return e
    return None


def _fields_for(manifest: dict, class_str: str) -> dict | None:
    """The field-schema dict for a class, or None (= abstain: unimportable /
    unknown / non-dataclass)."""
    entry = _class_entry(manifest, class_str)
    if not entry or not entry.get("importable"):
        return None
    fields = entry.get("fields")
    return fields if isinstance(fields, dict) else None


# ---------------------------------------------------------------------------
# THE resolver — walk-down with actual-class re-anchoring
# ---------------------------------------------------------------------------

# context = ("fields", <fields dict>) | ("spec", <TypeSpec>) | None

def _anchor(manifest: dict, node: Any):
    """Class context from an actual ``__class__``-bearing dict, if any."""
    if isinstance(node, dict):
        c = node.get("__class__")
        if isinstance(c, str) and c:
            fields = _fields_for(manifest, c)
            if fields is not None:
                return ("fields", fields)
            return None                      # unimportable/unknown class → abstain
    return _NO_ANCHOR


_NO_ANCHOR = object()


def _spec_context(manifest: dict, ts: dict | None):
    """The descent context a child TypeSpec provides."""
    if not isinstance(ts, dict):
        return None
    base = ts.get("base")
    if base in ("dict", "list"):
        return ("spec", ts)
    if base == "component":
        cls = ts.get("class")
        if cls:
            fields = _fields_for(manifest, cls)
            if fields is not None:
                return ("fields", fields)
    return None


def _step(manifest: dict, ctx, seg: str, child_node: Any):
    """One descent step: (child TypeSpec | None, child context)."""
    ts: dict | None = None
    if ctx is not None:
        kind, payload = ctx
        if kind == "fields":
            f = payload.get(seg)
            if isinstance(f, dict):
                ts = f.get("type")
        else:  # ("spec", TypeSpec)
            base = payload.get("base")
            if base == "dict":
                ts = payload.get("item")               # any-key descent
            elif base == "list" and seg.isdigit():
                ts = payload.get("item")

    # actual serialized class re-anchors the DESCENT context (the child's own
    # __class__ beats the annotation — subclasses, macro dicts, pulse dicts)
    anchored = _anchor(manifest, child_node)
    if anchored is not _NO_ANCHOR:
        child_ctx = anchored
    else:
        child_ctx = _spec_context(manifest, ts)
    return ts, child_ctx


def expected_type_for(path: str, state: dict, manifest: dict | None) -> dict | None:
    """THE path→TypeSpec resolver (see module doc). None = env layer abstains."""
    if not manifest or not isinstance(state, dict) or not path:
        return None
    segs = path.split(".")
    node: Any = state
    ctx = _anchor(manifest, state)
    if ctx is _NO_ANCHOR:
        ctx = None
    ts: dict | None = None
    for seg in segs:
        child = None
        if isinstance(node, dict):
            child = node.get(seg)
        elif isinstance(node, list) and seg.isdigit():
            i = int(seg)
            child = node[i] if i < len(node) else None
        ts, ctx = _step(manifest, ctx, seg, child)
        node = child
    if not isinstance(ts, dict) or ts.get("base") == "any":
        return None
    return ts


# ---------------------------------------------------------------------------
# THE judge
# ---------------------------------------------------------------------------

def judge(value: Any, ts: dict) -> tuple[bool, str, str]:
    """Judge *value* against TypeSpec *ts* → ``(ok, code, message)``.

    ``code`` ∈ EDIT_BLOCKING ∪ VALIDATION_SEVERITY ∪ {""}; callers map it to
    their tier. Pointer strings and None ALWAYS pass here (null-vs-required is
    the validator's separate, non-judge concern).
    """
    if is_pointer_str(value) or value is None:
        return True, "", ""
    base = ts.get("base") or "any"

    if base == "any":
        return True, "", ""

    if base == "union":
        arms = ts.get("union") or []
        for arm in arms:
            ok, _, _ = judge(value, arm)
            if ok:
                return True, "", ""
        return False, "type_mismatch", (
            f"expected one of {[a.get('base') for a in arms]}, got {_typename(value)}")

    if base == "bool":
        if isinstance(value, bool):
            return True, "", ""
        return False, "type_mismatch", f"expected bool, got {_typename(value)} {value!r}"

    if base in ("int", "float"):
        if isinstance(value, bool):
            return False, "bool_in_numeric", (
                f"expected {base}, got bool {value!r} (booleans are not numbers here)")
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                return False, "non_finite", (
                    f"non-finite {value!r} cannot be stored in state.json")
            if base == "int" and isinstance(value, float) and not value.is_integer():
                return False, "non_integral_int", (
                    f"expected int, got non-integral {value!r}")
            # widening (int where float) and integral floats (where int) pass
            # VERBATIM — never rewritten (idempotence / anti-churn)
            return True, "", ""
        return False, "type_mismatch", f"expected {base}, got {_typename(value)} {value!r}"

    if base == "str":
        if isinstance(value, str):
            enum = ts.get("enum")
            if enum and value not in enum:
                return False, "enum_miss", (
                    f"{value!r} not in allowed values {enum}")
            return True, "", ""
        return False, "type_mismatch", f"expected str, got {_typename(value)} {value!r}"

    if base == "list":
        if not isinstance(value, list):
            return False, "type_mismatch", f"expected list, got {_typename(value)} {value!r}"
        item = ts.get("item")
        if isinstance(item, dict) and item.get("base") != "any":
            for i, el in enumerate(value[:4096]):
                ok, code, msg = judge(el, item)
                if not ok:
                    if item.get("base") == "list" and not isinstance(el, list):
                        return False, "list_shape", (
                            f"expected a matrix (list of lists); element [{i}] is "
                            f"{_typename(el)}")
                    return False, "element_mismatch", f"element [{i}]: {msg}"
        return True, "", ""

    if base == "dict":
        if isinstance(value, dict):
            return True, "", ""                 # one level; children judged on their own edits
        return False, "type_mismatch", f"expected dict, got {_typename(value)} {value!r}"

    if base == "component":
        # a classless dict is quam's implicit-instantiation pattern; a scalar
        # non-pointer is the crash class
        if isinstance(value, dict):
            return True, "", ""
        return False, "type_mismatch", (
            f"expected a {ts.get('class') or 'component'} dict, got "
            f"{_typename(value)} {value!r}")

    return True, "", ""


def _typename(value: Any) -> str:
    return type(value).__name__


# ---------------------------------------------------------------------------
# analyzer — findings + bulk types map (one walk)
# ---------------------------------------------------------------------------

_FINDINGS_CAP = 300
_EXAMPLES_CAP = 5


def analyze_state(state: dict, manifest: dict | None) -> dict:
    """One walk → ``{"findings": [...], "types": {path: TypeSpec},
    "summary": {...}}``.

    Findings are AGGREGATED by ``(kind, class, field, code)`` with ``count`` +
    ``example_paths`` (a wrong-generation env otherwise emits 83× "CZGate has
    no field duration_qubit"). Two tiers: load-breaking ``error``
    (unimportable_class / unknown_class / unknown_field / missing_required
    with the field ABSENT) vs experiment-risk ``warning`` (judge mismatches,
    null-in-required-present, version skew).
    """
    findings: dict[tuple, dict] = {}
    types: dict[str, dict] = {}
    truncated = [False]

    def add(kind: str, severity: str, cls: str | None, field: str | None,
            path: str, detail: str, fix_hint: str = "", code: str = "") -> None:
        key = (kind, cls, field, code)
        rec = findings.get(key)
        if rec is None:
            if len(findings) >= _FINDINGS_CAP:
                truncated[0] = True
                return
            rec = findings[key] = {
                "kind": kind, "severity": severity, "class": cls, "field": field,
                "code": code or None, "count": 0, "example_paths": [],
                "detail": detail, "fix_hint": fix_hint,
            }
        rec["count"] += 1
        if len(rec["example_paths"]) < _EXAMPLES_CAP:
            rec["example_paths"].append(path)

    if not manifest or not isinstance(state, dict):
        return {"findings": [], "types": {}, "summary": {
            "errors": 0, "warnings": 0, "checked_nodes": 0, "truncated": False}}

    versions = manifest.get("versions") or {}
    checked_nodes = [0]

    def pip_hint(class_str: str) -> str:
        top = class_str.split(".", 1)[0]
        return (f"pip install {top} into the selected env, or select the env "
                f"this chip was written by")

    def walk(node: Any, path: str, ctx) -> None:
        if isinstance(node, dict):
            cls_str = node.get("__class__")
            if isinstance(cls_str, str) and cls_str:
                entry = _class_entry(manifest, cls_str)
                if entry is None:
                    add("unknown_class", "error", cls_str, None, path,
                        f"{cls_str} was not probed in the selected env (harvest drift)",
                        "re-probe the environment")
                elif not entry.get("importable"):
                    add("unimportable_class", "error", cls_str, None, path,
                        f"{cls_str} cannot be imported in the selected env: "
                        f"{(entry.get('error') or '')[:120]}",
                        pip_hint(cls_str))
                else:
                    fields = entry.get("fields")
                    if isinstance(fields, dict):
                        checked_nodes[0] += 1
                        _check_class_node(node, path, cls_str, fields)
            for k, v in node.items():
                if k.startswith("__"):
                    continue
                child_path = f"{path}.{k}" if path else str(k)
                ts, child_ctx = _step(manifest, ctx, str(k), v)
                if isinstance(ts, dict) and ts.get("base") != "any" and not isinstance(v, (dict, list)):
                    types[child_path] = ts
                walk(v, child_path, child_ctx)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                child_path = f"{path}.{i}"
                ts, child_ctx = _step(manifest, ctx, str(i), v)
                if isinstance(ts, dict) and ts.get("base") != "any" and not isinstance(v, (dict, list)):
                    types[child_path] = ts
                walk(v, child_path, child_ctx)

    def _check_class_node(node: dict, path: str, cls_str: str, fields: dict) -> None:
        leaf_cls = cls_str.rsplit(".", 1)[-1]
        for k, v in node.items():
            if k.startswith("__"):
                # serialization artifacts (__class__, __package_versions__, …)
                # are quam's own markers, never class fields
                continue
            f = fields.get(k)
            kpath = f"{path}.{k}" if path else k
            if f is None:
                add("unknown_field", "error", cls_str, k, kpath,
                    f"'{k}' is not a field of the selected env's {leaf_cls} — "
                    f"Quam.load() fails with AttributeError('Unexpected attribute')",
                    "this chip was written by a different stack generation; "
                    "select that env or migrate the state", code="unknown_field")
                continue
            ts = f.get("type") or {}
            if v is None:
                # null passes iff Optional OR the field's own default is None
                if not (f.get("optional") or (f.get("has_default")
                                              and f.get("default") is None
                                              and not f.get("default_repr"))):
                    add("null_required", "warning", cls_str, k, kpath,
                        f"{leaf_cls}.{k} is null but the env schema has a "
                        f"non-null default ({f.get('default')!r})",
                        "set a value or leave as-is (quam instantiates fine; "
                        "downstream consumers may not)", code="null_required")
                continue
            if isinstance(v, dict) or is_pointer_str(v):
                continue                       # containers recurse; pointers pass
            ok, code, msg = judge(v, ts)
            if not ok:
                sev = VALIDATION_SEVERITY.get(code, "warning")
                add("type_mismatch", sev, cls_str, k, kpath,
                    f"{leaf_cls}.{k}: {msg}",
                    "fix the value, or assign an overriding type to this key",
                    code=code)
        # required fields ABSENT from the node entirely
        for fname, f in fields.items():
            if fname in node:
                continue
            if not f.get("has_default") and not f.get("optional"):
                add("missing_required", "error", cls_str, fname,
                    f"{path}.{fname}" if path else fname,
                    f"required field {leaf_cls}.{fname} is absent",
                    "the state predates this field — migrate or select the "
                    "writing env")

    root_ctx = _anchor(manifest, state)
    if root_ctx is _NO_ANCHOR:
        root_ctx = None
    walk(state, "", root_ctx)

    # __package_versions__ stamp vs the probed env
    stamp = state.get("__package_versions__")
    if isinstance(stamp, dict):
        for pkg, want in stamp.items():
            have = versions.get(pkg)
            if have and want and str(have) != str(want):
                add("version_skew", "warning", None, pkg, "__package_versions__",
                    f"state written by {pkg} {want}; selected env has {have}",
                    "differences may be benign — run the deep validation "
                    "(Quam.load) to be sure")

    out = sorted(findings.values(),
                 key=lambda r: (0 if r["severity"] == "error" else 1, -r["count"]))
    summary = {
        "errors": sum(1 for r in out if r["severity"] == "error"),
        "warnings": sum(1 for r in out if r["severity"] != "error"),
        "checked_nodes": checked_nodes[0],
        "truncated": truncated[0],
    }
    return {"findings": out, "types": types, "summary": summary}


# ---------------------------------------------------------------------------
# per-store memo + diagnostics bridge
# ---------------------------------------------------------------------------

_analysis_memo: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _manifest_key(manifest: dict | None) -> tuple:
    if not manifest:
        return ()
    v = manifest.get("versions") or {}
    return tuple(sorted((str(k), str(x)) for k, x in v.items()))


def analysis_for_store(store, manifest: dict | None) -> dict:
    """Memoized ``analyze_state`` keyed ``(mutation_seq, manifest versions)`` —
    one O(leaves) walk per mutation, the diagnostics badge/banner/page all read
    the same result."""
    key = (getattr(store, "mutation_seq", 0), _manifest_key(manifest))
    hit = _analysis_memo.get(store)
    if hit is not None and hit[0] == key:
        return hit[1]
    lock = getattr(store, "_lock", None)
    if lock is not None:
        with lock:
            res = analyze_state(store.state, manifest)
    else:
        res = analyze_state(store.state, manifest)
    _analysis_memo[store] = (key, res)
    return res


def to_diag_findings(analysis: dict, env_label: str = "") -> list:
    """Bridge the analyzer's aggregated findings into diagnostics ``Finding``
    objects (category ``env_*`` → the "Environment match" domain) so the
    existing badge / banner / list / Explorer-marks machinery renders them
    with zero new plumbing."""
    from quam_state_manager.core.diagnostics import Finding
    out: list = []
    for rec in analysis.get("findings") or []:
        cls = (rec.get("class") or "").rsplit(".", 1)[-1]
        fld = rec.get("field") or ""
        loc = f"{cls}.{fld}" if cls and fld else (cls or fld or "state")
        count = rec.get("count") or 0
        suffix = f" ({count}×)" if count > 1 else ""
        examples = rec.get("example_paths") or []
        detail = rec.get("detail") or ""
        if rec.get("fix_hint"):
            detail = f"{detail} — {rec['fix_hint']}"
        if env_label:
            detail = f"{detail} [env: {env_label}]"
        out.append(Finding(
            severity="error" if rec.get("severity") == "error" else "warning",
            category=f"env_{rec.get('kind') or 'finding'}",
            location=loc + suffix,
            message=rec.get("detail") or rec.get("kind") or "env mismatch",
            detail=detail,
            jump_path=examples[0] if examples else "",
        ))
    return out
