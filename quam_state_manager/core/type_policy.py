"""Per-key expected-type policy: layering, enforcement, parsing, storage.

Flask-free (CLI-importable, like ``edit_policy``). Wraps the ONE resolver +
ONE judge in :mod:`core.state_env_validate` and adds exactly three things:

1. **Layering** — ``expected_for`` resolves a key's expected type as
   user-assignment-with-``override_env`` → env schema → plain user assignment
   → value inference. Only ``env``/``user`` sources are ENFORCED (hard error);
   ``inferred`` is display-only — enforcement at that layer IS today's
   ``_type_coerce``, byte-identical, so a chip with no manifest and no
   assignments behaves exactly as before (the empty-policy golden pins this).
2. **Enforcement** — ``check`` judges the value, raises
   :class:`TypeMismatchError` (a ``TypeError`` subclass, so every existing
   route/CLI catch tuple handles it), and returns the value normalized ONLY
   by the old-value numeric reconciliation that ``_type_coerce`` already
   ships (old int + integral-float new → int; old float + int new → float) —
   idempotent by construction, never an env-driven rewrite (critique #10).
3. **Storage** — per-chip user assignments in
   ``instance/type_assignments/<working_copy.key_for(live)>.json`` (atomic
   ``safe_io`` write + module lock — the ``chip_decisions`` precedent).
   EXACT paths only in v1: normalization is provably ambiguous on the real
   corpus (entity-tuple ``extras`` keys, ``pump``/``pump_`` twins).

User-facing type grammar (assignment API):
``int | number | str | bool | dict | list | list<T> | matrix | matrix<T>``
with ``T ∈ {int, number, str, bool}`` — sugar over the nested TypeSpec
(``number`` = float base with the judge's int-widening; ``matrix`` =
``list<list>``).
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io
from quam_state_manager.core.state_env_validate import (
    EDIT_BLOCKING,
    expected_type_for,
    is_pointer_str,
    judge,
)

logger = logging.getLogger(__name__)

_ASSIGN_DIRNAME = "type_assignments"
_assign_lock = threading.Lock()


class TypeMismatchError(TypeError):
    """A blocked wrong-type write. Subclasses TypeError so every existing
    ``except (KeyError, TypeError, ValueError, IndexError)`` responder catches
    it unchanged; carries structured fields for JSON routes."""

    def __init__(self, message: str, *, path: str, expected: "Expected",
                 got: str) -> None:
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.got = got

    def as_json(self) -> dict:
        return {
            "error_kind": "type_mismatch",
            "expected": self.expected.as_json(),
            "got": self.got,
        }


# ---------------------------------------------------------------------------
# user type-expression grammar  ⇄  TypeSpec
# ---------------------------------------------------------------------------

# r14: floats DISPLAY as "real" (physicists' vocabulary — "number" was too
# broad a label for ℝ). Both "real" and the legacy "number" PARSE to float so
# every stored assignment / sidecar / script keeps working.
_SCALARS = {"int": "int", "number": "float", "real": "float",
            "str": "str", "bool": "bool"}
_VALID_EXPRS = ("int", "real", "str", "bool", "dict", "list", "list<T>",
                "matrix", "matrix<T>")


def parse_type(expr: str) -> dict:
    """User grammar → TypeSpec. Raises ValueError on an unknown expression."""
    e = (expr or "").strip()
    if e in _SCALARS:
        return {"base": _SCALARS[e], "optional": True, "item": None,
                "enum": None, "union": None, "class": None, "raw": e}
    if e == "dict":
        return {"base": "dict", "optional": True, "item": None, "enum": None,
                "union": None, "class": None, "raw": e}
    if e in ("list", "matrix") or (
            (e.startswith("list<") or e.startswith("matrix<")) and e.endswith(">")):
        inner = None
        if "<" in e:
            t = e[e.index("<") + 1:-1].strip()
            if t not in _SCALARS:
                raise ValueError(
                    f"unknown element type {t!r} — use one of {sorted(_SCALARS)}")
            inner = {"base": _SCALARS[t], "optional": False, "item": None,
                     "enum": None, "union": None, "class": None, "raw": t}
        if e.startswith("matrix"):
            row = {"base": "list", "optional": False, "item": inner,
                   "enum": None, "union": None, "class": None,
                   "raw": f"list<{inner['raw']}>" if inner else "list"}
            return {"base": "list", "optional": True, "item": row, "enum": None,
                    "union": None, "class": None, "raw": e}
        return {"base": "list", "optional": True, "item": inner, "enum": None,
                "union": None, "class": None, "raw": e}
    raise ValueError(
        f"unknown type {expr!r} — use one of: {', '.join(_VALID_EXPRS)}")


def format_type(ts: dict | None) -> str:
    """TypeSpec → short display string (chips, error messages)."""
    if not isinstance(ts, dict):
        return "unknown"
    base = ts.get("base") or "any"
    if base == "float":
        return "real"
    if base == "list":
        item = ts.get("item")
        if isinstance(item, dict) and item.get("base") == "list":
            inner = item.get("item")
            return (f"matrix<{format_type(inner)}>"
                    if isinstance(inner, dict) else "matrix")
        if isinstance(item, dict):
            return f"list<{format_type(item)}>"
        return "list"
    if base == "union":
        return " | ".join(format_type(a) for a in (ts.get("union") or [])) or "union"
    if base == "component":
        cls = ts.get("class") or ""
        return cls.rsplit(".", 1)[-1] or "component"
    if base == "str" and ts.get("enum"):
        return "enum(" + ", ".join(map(str, ts["enum"])) + ")"
    return base


# ---------------------------------------------------------------------------
# layered resolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Expected:
    spec: dict                       # TypeSpec
    source: str                      # "env" | "user" | "verdict" | "inferred"
    override_env: bool = False
    class_path: str | None = None    # env source: owning class (display)
    field: str | None = None
    detail: str = ""

    @property
    def enforced(self) -> bool:
        return self.source in ("env", "user", "verdict")

    def as_json(self) -> dict:
        return {
            "type": format_type(self.spec),
            "nullable": bool(self.spec.get("optional", True)),
            "enum": self.spec.get("enum"),
            "source": self.source,
            "override_env": self.override_env,
            "class_path": self.class_path,
            "field": self.field,
            "detail": self.detail or (self.spec.get("raw") or ""),
        }


def _infer_spec(value: Any) -> dict | None:
    """Layer-3 inference — DISPLAY ONLY (enforcement at this layer is today's
    ``_type_coerce``). Null and pointer values are un-inferable."""
    if value is None or is_pointer_str(value):
        return None
    if isinstance(value, bool):
        return {"base": "bool", "optional": True, "item": None, "enum": None,
                "union": None, "class": None, "raw": "bool (from value)"}
    if isinstance(value, (int, float)):
        return {"base": "float", "optional": True, "item": None, "enum": None,
                "union": None, "class": None, "raw": "real (from value)"}
    if isinstance(value, str):
        return {"base": "str", "optional": True, "item": None, "enum": None,
                "union": None, "class": None, "raw": "str (from value)"}
    if isinstance(value, list):
        return {"base": "list", "optional": True, "item": None, "enum": None,
                "union": None, "class": None, "raw": "list (from value)"}
    if isinstance(value, dict):
        return {"base": "dict", "optional": True, "item": None, "enum": None,
                "union": None, "class": None, "raw": "dict (from value)"}
    return None


class TypePolicy:
    """The per-store type policy: manifest (may be None) + user assignments."""

    def __init__(self, manifest: dict | None, assignments: dict[str, dict] | None,
                 *, sidecar_path: Path | None = None,
                 verdicts: dict | None = None,
                 env_manifest: dict | None = None) -> None:
        # ``manifest`` is what everything resolves against — the env schema with
        # the user's verdicts overlaid (docs/79). ``env_manifest`` keeps the
        # PRISTINE env truth, so the UI can show both sides of a disagreement
        # and a re-attach can never overlay an already-overlaid manifest.
        self.manifest = manifest
        self.env_manifest = env_manifest if env_manifest is not None else manifest
        self.assignments = dict(assignments or {})
        self.verdicts = dict(verdicts or {})
        self.sidecar_path = sidecar_path

    # -- resolution ------------------------------------------------------

    def _user_expected(self, a: dict) -> Expected | None:
        try:
            spec = parse_type(a.get("type") or "")
        except ValueError:
            return None
        return Expected(spec=spec, source="user",
                        override_env=bool(a.get("override_env")),
                        detail=f"assigned {a.get('set_at') or ''}".strip())

    def _env_expected(self, merged: dict, dot_path: str) -> Expected | None:
        ts = expected_type_for(dot_path, merged, self.manifest)
        if ts is None:
            return None
        # provenance for the error message / UI chip
        cls, fld = _owner_of(dot_path, merged)
        return Expected(spec=ts, source="env", class_path=cls, field=fld,
                        detail=str(ts.get("raw") or ""))

    def _verdict_expected(self, merged: dict, dot_path: str) -> Expected | None:
        """The user's env-scoped correction of SM's belief, if it covers this
        key. Dormant — and provably free — when no verdict is stored."""
        if not self.verdicts:
            return None
        from quam_state_manager.core.state_env_validate import field_ref_for
        ref = field_ref_for(dot_path, merged, self.manifest)
        if ref is None or not ref.exact:
            # v1: a verdict states the FIELD's type; elements below it keep
            # resolving through the env.
            return None
        v = self.verdicts.get(f"{ref.canonical}.{ref.field}")
        if not v or not v.get("enforced") or not v.get("spec"):
            return None
        return Expected(spec=v["spec"], source="verdict",
                        class_path=ref.canonical, field=ref.field,
                        detail=f"you taught SM ({v.get('from_label') or ''})".strip())

    def expected_for(self, merged: dict, dot_path: str,
                     current_value: Any = None,
                     *, infer: bool = True) -> Expected | None:
        """user-override → VERDICT → env → user → inferred. None = fully unknown.

        The verdict layer is the user's env-scoped correction ("in THIS env,
        CZGate.duration_qubit is real now"). It outranks the env schema because
        it is a statement ABOUT that schema; it loses to a 409-confirmed
        per-key ``override_env`` assignment, which is strictly more specific
        (one exact path on one chip).
        """
        a = self.assignments.get(dot_path)
        if a and a.get("override_env"):
            exp = self._user_expected(a)
            if exp:
                return exp
        verdict = self._verdict_expected(merged, dot_path)
        if verdict:
            return verdict
        env = self._env_expected(merged, dot_path)
        if env:
            return env
        if a:
            exp = self._user_expected(a)
            if exp:
                return exp
        if infer:
            spec = _infer_spec(current_value)
            if spec:
                return Expected(spec=spec, source="inferred")
        return None

    # -- enforcement -----------------------------------------------------

    def check(self, expected: Expected, new_value: Any, *, path: str,
              old_value: Any = None) -> Any:
        """Judge → raise TypeMismatchError on a blocking code → return the
        value after OLD-VALUE numeric reconciliation only."""
        ok, code, msg = judge(new_value, expected.spec)
        if not ok and code in EDIT_BLOCKING:
            prov = ""
            advice = ("If this change is intentional, assign a new type to this "
                      "key first.")
            if expected.source == "env" and expected.class_path:
                cls = expected.class_path.rsplit(".", 1)[-1]
                prov = f" (quam schema: {cls}.{expected.field})"
            elif expected.source == "user":
                prov = " (user-assigned type)"
            elif expected.source == "verdict":
                # The user TAUGHT SM this type for the whole class in this
                # environment (docs/79) — telling them to "assign a type" would
                # send them to the wrong place, so name what is really in force.
                cls = (expected.class_path or "").rsplit(".", 1)[-1]
                prov = (f" (you taught SM: {cls}.{expected.field}"
                        f"{', ' + expected.detail if expected.detail else ''})")
                advice = ("If that is no longer right, revoke or change it under "
                          "Diagnostics -> Types & values -> Manage taught types.")
            raise TypeMismatchError(
                f"Type mismatch at {path}: expected {format_type(expected.spec)}"
                f"{prov} — {msg}. {advice}",
                path=path, expected=expected, got=type(new_value).__name__)
        return _reconcile_numeric(old_value, new_value)

    def check_subtree(self, merged: dict, root_path: str, value: Any) -> None:
        """Env/user-check every scalar leaf of a subtree about to be CREATED.
        Embedded ``__class__`` dicts anchor exactly as in the resolver (the
        walk runs on a temporary composite so created pulses are env-checked
        immediately). Pointer/null leaves skip."""
        def leaves(v: Any, p: str):
            if isinstance(v, dict):
                for k, sub in v.items():
                    if k == "__class__":
                        continue
                    yield from leaves(sub, f"{p}.{k}")
            elif isinstance(v, list):
                for i, sub in enumerate(v):
                    yield from leaves(sub, f"{p}.{i}")
            else:
                yield p, v

        # graft the candidate value into a shallow copy of the parent chain so
        # expected_type_for sees the embedded __class__ anchors
        composite = _graft(merged, root_path, value)
        for leaf_path, leaf_value in leaves(value, root_path):
            if leaf_value is None or is_pointer_str(leaf_value):
                continue
            exp = self.expected_for(composite, leaf_path, infer=False)
            if exp is not None and exp.enforced:
                self.check(exp, leaf_value, path=leaf_path)

    # -- display ---------------------------------------------------------

    def annotate(self, merged: dict, dot_path: str, current_value: Any = None) -> dict | None:
        exp = self.expected_for(merged, dot_path, current_value)
        return exp.as_json() if exp else None


def _owner_of(dot_path: str, merged: dict) -> tuple[str | None, str | None]:
    """(nearest classed ancestor's __class__, final segment) — provenance only."""
    segs = dot_path.split(".")
    node: Any = merged
    owner = merged.get("__class__") if isinstance(merged, dict) else None
    for seg in segs[:-1]:
        if isinstance(node, dict):
            node = node.get(seg)
        elif isinstance(node, list) and seg.isdigit():
            i = int(seg)
            node = node[i] if i < len(node) else None
        else:
            node = None
        if isinstance(node, dict):
            c = node.get("__class__")
            if isinstance(c, str) and c:
                owner = c
    return (owner if isinstance(owner, str) else None), segs[-1]


def _reconcile_numeric(old_value: Any, new_value: Any) -> Any:
    """Today's shipped int/float normalization vs the OLD value — idempotent
    (re-committing a field's current value is always a no-op) and never driven
    by the env schema (critique #10)."""
    if isinstance(new_value, bool) or isinstance(old_value, bool):
        return new_value
    if (isinstance(old_value, int) and isinstance(new_value, float)
            and new_value.is_integer()):
        return int(new_value)
    if isinstance(old_value, float) and isinstance(new_value, int):
        return float(new_value)
    return new_value


def _graft(merged: dict, root_path: str, value: Any) -> dict:
    """A shallow composite of *merged* with *value* grafted at *root_path*
    (read-only use by the resolver; never mutates *merged*)."""
    segs = root_path.split(".")
    out = dict(merged) if isinstance(merged, dict) else {}
    node = out
    for seg in segs[:-1]:
        child = node.get(seg) if isinstance(node, dict) else None
        child = dict(child) if isinstance(child, dict) else {}
        node[seg] = child
        node = child
    node[segs[-1]] = value
    return out


# ---------------------------------------------------------------------------
# expectation-aware parsing (route boundary)
# ---------------------------------------------------------------------------

# cli's grouped-number gate, verbatim (NO exponent — the empty-policy golden
# pins parse_value byte-identical to the historical cli parser; the
# expectation path's _GROUPED_NUMBER_OK below deliberately also accepts
# exponents and must stay separate).
_PLAIN_GROUPED_NUMBER = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?$")


def parse_value(raw: str):
    """Parse a CLI / web-edit string value into the appropriate Python type.

    Lives HERE (not cli.py, its historical home): the web edit path calls it
    on every /field/edit, and cli.py imports typer at module level — an env
    without the CLI stack 500'd every field edit (r9 SUPER-CRITICAL). cli.py
    re-imports it for its own commands.

    Symmetric with :func:`units.group_digits`: a comma-grouped number like
    ``"5,075,187,484"`` has its grouping commas stripped before numeric
    parsing (guarded so genuine comma-bearing strings are left untouched).
    Non-finite floats (``inf``/``nan``/overflow) are rejected with
    ``ValueError`` so a hostile value can never reach the JSON store as
    ``Infinity`` (invalid strict JSON).
    """
    s = raw.strip()
    low = s.lower()
    if low in ("null", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    # Explicit JSON literal — a quoted string, an array, or an object. Lets a
    # user type a genuine string (``"02"`` → the 2-char string ``02``) or a
    # list / dict value the scalar parser can't express; a malformed literal
    # (``[1,2``) falls through to the bare-string path.
    if s[:1] in ('"', '[', '{'):
        try:
            return json.loads(s)
        except (ValueError, TypeError):
            pass
    candidate = (s.replace(",", "")
                 if ("," in s and _PLAIN_GROUPED_NUMBER.match(s)) else s)
    try:
        return int(candidate)
    except ValueError:
        pass
    try:
        f = float(candidate)
    except (ValueError, OverflowError):
        return raw
    if not math.isfinite(f):
        raise ValueError(f"{raw!r} is not a finite number")
    return f


def parse_with_expected(raw: str, expected: Expected | None) -> Any:
    """Parse user-typed text against the expected type. With no ENFORCED
    expectation, behavior is :func:`parse_value` (the historical
    ``cli._parse_value``) byte-identical."""
    if expected is None or not expected.enforced:
        return parse_value(raw)

    s = raw.strip()
    # pointers and the null/none tokens ALWAYS win (null is always writable —
    # the clear-a-field workflow; a literal string "null" needs the quoted form)
    if s.startswith(("#/", "#./", "#../")):
        return s
    if s.lower() in ("null", "none"):
        return None

    base = expected.spec.get("base") or "any"
    if base == "str":
        if s.startswith('"'):
            try:
                loaded = json.loads(s)
                if isinstance(loaded, str):
                    return loaded
            except ValueError:
                pass
        return raw                      # verbatim — kills the "02" → "2" mangle
    if base == "bool":
        low = s.lower()
        if low in ("true", "t", "yes", "y", "on", "1"):
            return True
        if low in ("false", "f", "no", "n", "off", "0"):
            return False
        raise ValueError(
            f"expected a boolean — use true/false, yes/no, on/off or 1/0, got {raw!r}")
    if base in ("int", "float"):
        cleaned = s.replace(",", "") if _GROUPED_NUMBER_OK(s) else s
        try:
            return int(cleaned)
        except ValueError:
            pass
        try:
            f = float(cleaned)
        except ValueError as exc:
            raise ValueError(f"expected a number, got {raw!r}") from exc
        if not math.isfinite(f):
            raise ValueError(f"non-finite {raw!r} cannot be stored in state.json")
        return f
    if base in ("list", "dict", "component", "union"):
        try:
            return json.loads(s)
        except ValueError as exc:
            shape = "[1, 2, 3]" if base == "list" else "{\"key\": value}"
            raise ValueError(
                f"expected JSON for a {format_type(expected.spec)} — e.g. {shape}; "
                f"could not parse {raw!r}") from exc
    return parse_value(raw)


def _GROUPED_NUMBER_OK(s: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d[\d,]*(\.\d+)?([eE][+-]?\d+)?", s))


# ---------------------------------------------------------------------------
# sidecar storage (chip_decisions precedent)
# ---------------------------------------------------------------------------

def assignments_path(instance_path, live_folder) -> Path:
    from quam_state_manager.core import working_copy
    return (Path(instance_path) / _ASSIGN_DIRNAME
            / f"{working_copy.key_for(live_folder)}.json")


def _load_assignments_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("corrupt type-assignments sidecar %s — ignoring", path)
        return {}
    a = data.get("assignments") if isinstance(data, dict) else None
    return a if isinstance(a, dict) else {}


def load_policy(instance_path, live_folder, manifest: dict | None,
                *, verdicts: dict | None = None) -> TypePolicy:
    """Never raises; a corrupt/missing sidecar yields an empty assignment set.

    THE single place the env-scoped verdict overlay is applied (docs/79), so
    every construction site gets a verdict-aware policy without knowing about
    verdicts. With no verdict file the manifest is passed through unchanged —
    ``policy.manifest is policy.env_manifest`` — and the whole mechanism is
    provably dormant.
    """
    path = assignments_path(instance_path, live_folder)
    with _assign_lock:
        assignments = _load_assignments_file(path)
    resolved: dict = {}
    overlaid = manifest
    if manifest:
        try:
            from quam_state_manager.core import type_verdicts
            resolved = (verdicts if verdicts is not None
                        else type_verdicts.resolve_for_manifest(instance_path, manifest))
            if resolved:
                overlaid = type_verdicts.overlay_manifest(manifest, resolved)
        except Exception:  # noqa: BLE001 — a verdict bug must never brick activation
            logger.warning("type-verdict overlay failed", exc_info=True)
            resolved, overlaid = {}, manifest
    return TypePolicy(overlaid, assignments, sidecar_path=path,
                      verdicts=resolved, env_manifest=manifest)


def save_assignment(instance_path, live_folder, dot_path: str, spec: dict) -> dict:
    """Persist one assignment (validates the type expression). Returns the
    stored record. Locked load-modify-write + atomic replace."""
    parse_type(spec.get("type") or "")          # ValueError on bad expr
    record = {
        "type": spec["type"],
        "override_env": bool(spec.get("override_env")),
        "scope": "exact",
        "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": str(spec.get("note") or ""),
    }
    path = assignments_path(instance_path, live_folder)
    with _assign_lock:
        assignments = _load_assignments_file(path)
        assignments[dot_path] = record
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, {
            "version": 1,
            "live_folder": str(live_folder),
            "assignments": assignments,
        })
    return record


def delete_assignment(instance_path, live_folder, dot_path: str) -> bool:
    path = assignments_path(instance_path, live_folder)
    with _assign_lock:
        assignments = _load_assignments_file(path)
        if dot_path not in assignments:
            return False
        assignments.pop(dot_path)
        safe_io.atomic_write_json(path, {
            "version": 1,
            "live_folder": str(live_folder),
            "assignments": assignments,
        })
    return True
