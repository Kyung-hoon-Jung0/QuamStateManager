"""MERGE-BLOCKING corpus harness: the type layer must be invisible on real data.

Real-data locations come from environment variables (never hardcoded — the
public repo carries no lab paths); unset ⇒ the whole module skips:

* ``QSM_CORPUS_ROOT``  — a folder of chip folders (each with state.json)
* ``QSM_CHIP_MODERN``  — a chip folder WRITTEN by the modern stack
  (quam 0.6.0 / quam_builder 0.4.0 — carries ``__package_versions__``)
* ``QSM_CHIP_FORK``    — a chip folder written by the fork-pin generation

against BOTH captured env-generation manifests
(tests/golden/state_schema_*.json — regenerate via
generator/probe_state_schema.py when the envs change):

1. **FP budget (fast, every chip)** — for EVERY scalar leaf, resolving the
   env expected type and judging the leaf's OWN CURRENT value must never
   produce an edit-BLOCKING code. A block here means a user could not
   re-commit a value already in their state — alarm-fatigue poison and the
   exact failure class the critique flagged (int/float Hz instability,
   pointer/literal variance, bool fields, enum churn).
2. **Byte idempotence (real machinery, biggest chips)** — re-writing every
   scalar leaf with its own value through the REAL ``Modifier.set_value``
   with the policy attached leaves state.json's canonical JSON byte-identical
   (catches silent rewrites, not just blocks).
3. **Generation ground truths** — own-generation validation is (near-)silent;
   cross-generation validation detects the documented class-field deltas as
   a handful of AGGREGATED findings, never a per-node flood.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_validate as sev
from quam_state_manager.core import type_policy as tp
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier

_GOLDEN = Path(__file__).resolve().parent / "golden"
_CORPUS = Path(os.environ.get("QSM_CORPUS_ROOT", "/nonexistent"))
_CHIP_MODERN = Path(os.environ.get("QSM_CHIP_MODERN", "/nonexistent"))
_CHIP_FORK = Path(os.environ.get("QSM_CHIP_FORK", "/nonexistent"))

_MANIFESTS = {}
for name in ("state_schema_modern", "state_schema_fork"):
    p = _GOLDEN / f"{name}.json"
    if p.exists():
        m = json.loads(p.read_text(encoding="utf-8"))
        # SM-side derivations (the probe fixture stores the raw document)
        from quam_state_manager.core.state_env_schema import _decorate
        _MANIFESTS[name] = _decorate(m)

pytestmark = pytest.mark.skipif(
    not _CORPUS.exists() or not _MANIFESTS,
    reason="QSM_CORPUS_ROOT not set / golden manifests not present")


def _chips():
    out = []
    if _CORPUS.exists():
        for d in sorted(_CORPUS.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")) or "backup" in d.name:
                continue
            if (d / "state.json").exists():
                out.append(d)
            else:
                for sub in sorted(d.iterdir()) if d.is_dir() else []:
                    if sub.is_dir() and (sub / "state.json").exists():
                        out.append(sub)
    for extra in (_CHIP_MODERN, _CHIP_FORK):
        if (extra / "state.json").exists() and extra not in out:
            out.append(extra)
    return out


def _scalar_leaves(node, path=""):
    """(path, value) for every non-dict leaf, recursing lists (dot-form)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _scalar_leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _scalar_leaves(v, f"{path}.{i}")
    else:
        yield path, node


def _load_state(p: Path) -> dict:
    return json.loads((p / "state.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("mname", sorted(_MANIFESTS))
def test_fp_budget_every_chip_every_leaf(mname):
    manifest = _MANIFESTS[mname]
    chips = _chips()
    assert chips, "no corpus chips found"
    offenders = []
    for chip in chips:
        try:
            state = _load_state(chip)
        except (OSError, ValueError):
            continue
        policy = tp.TypePolicy(manifest, {})
        for path, value in _scalar_leaves(state):
            if value is None or sev.is_pointer_str(value):
                continue
            exp = policy.expected_for(state, path, infer=False)
            if exp is None or not exp.enforced:
                continue
            ok, code, msg = sev.judge(value, exp.spec)
            if not ok and code in sev.EDIT_BLOCKING:
                offenders.append(f"{chip.name}:{path} [{code}] {msg}")
                if len(offenders) > 20:
                    break
    assert not offenders, (
        f"{len(offenders)}+ real values would be BLOCKED from re-committing "
        f"under {mname}:\n" + "\n".join(offenders[:20]))


@pytest.mark.parametrize("mname", sorted(_MANIFESTS))
def test_byte_idempotence_biggest_chips(mname):
    """The 3 largest corpus chips + the two generation reference chips."""
    manifest = _MANIFESTS[mname]
    chips = _chips()
    by_size = sorted(chips, key=lambda c: (c / "state.json").stat().st_size,
                     reverse=True)
    targets = list(dict.fromkeys(
        by_size[:3] + [c for c in (_CHIP_MODERN, _CHIP_FORK) if c in chips]))
    ran = 0
    for chip in targets:
        before = json.dumps(_load_state(chip), sort_keys=True)
        store = QuamStore.from_dicts(json.loads(before), {"wiring": {}})
        store.type_policy = tp.TypePolicy(manifest, {})
        mod = Modifier(store)
        for path, value in _scalar_leaves(store.state):
            if isinstance(value, list):
                continue
            try:
                mod.set_value(path, value, _defer_hooks=True)
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                pytest.fail(f"{chip.name}:{path} rejected its own value: {exc}")
        after = json.dumps(store.state, sort_keys=True)
        assert after == before, f"{chip.name}: state changed under self-rewrite ({mname})"
        ran += 1
    assert ran, "no idempotence targets found"


# ---------------------------------------------------------------------------
# analyzer generation ground truths
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not (_CHIP_MODERN / "state.json").exists(),
                    reason="QSM_CHIP_MODERN not set")
def test_analyzer_modern_chip_only_the_twpa_sha_delta():
    """The modern-written reference chip vs the modern manifest.

    Ground truth (a TRUE positive the validator exists to catch): the chip's
    writing quam_builder calls itself 0.4.0 but is a DIFFERENT SHA than the
    installed one — its root class carries the fork-lineage TWPA registry
    (``active_twpa_names``), the installed build's does not, and the version
    string never moved. ``Quam.load()`` of this state in that env would
    really fail. The pin: that documented SHA delta is the ONLY error-tier
    finding family.
    """
    if "state_schema_modern" not in _MANIFESTS:
        pytest.skip("modern manifest missing")
    res = sev.analyze_state(_load_state(_CHIP_MODERN),
                            _MANIFESTS["state_schema_modern"])
    errors = [f for f in res["findings"] if f["severity"] == "error"]
    assert all(f["kind"] == "unknown_field" and "twpa" in (f["field"] or "")
               for f in errors), json.dumps(errors[:6], indent=1)
    assert len(errors) <= 2


@pytest.mark.skipif(not (_CHIP_FORK / "state.json").exists(),
                    reason="QSM_CHIP_FORK not set")
def test_analyzer_zero_errors_own_generation_fork():
    """A fork-pin-written chip vs the fork manifest: zero load-breaking."""
    if "state_schema_fork" not in _MANIFESTS:
        pytest.skip("fork manifest missing")
    res = sev.analyze_state(_load_state(_CHIP_FORK), _MANIFESTS["state_schema_fork"])
    errors = [f for f in res["findings"] if f["severity"] == "error"]
    assert not errors, json.dumps(errors[:6], indent=1)


@pytest.mark.skipif(not (_CHIP_MODERN / "state.json").exists(),
                    reason="QSM_CHIP_MODERN not set")
def test_analyzer_cross_generation_detects_and_aggregates():
    """The modern-written chip vs the FORK manifest must detect the
    generation delta (unknown_field ``duration_qubit`` — the 0.4.0 CZGate
    field the fork lacks) as a HANDFUL of aggregated findings, never a
    per-node flood."""
    if "state_schema_fork" not in _MANIFESTS:
        pytest.skip("fork manifest missing")
    res = sev.analyze_state(_load_state(_CHIP_MODERN), _MANIFESTS["state_schema_fork"])
    unknown = [f for f in res["findings"]
               if f["kind"] == "unknown_field" and f["field"] == "duration_qubit"]
    assert unknown, "the generation delta was not detected"
    assert unknown[0]["count"] >= 50            # dozens of CZ macros aggregate...
    assert len(unknown) == 1                    # ...into ONE finding
    assert len(res["findings"]) < 60, "cross-generation noise flood"
