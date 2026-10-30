"""The name rules (core/add_names.py) and the gate/pulse type tables
(core/add_catalog.py) live in one place each, and every caller reads through
them. Behaviour is pinned by the add-path goldens (test_add_paths_golden.py);
this file pins the ONE-PLACE part: no second copy can come back unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path

from quam_state_manager.core import add_catalog, add_names, additions
from quam_state_manager.web import routes

PKG = Path(additions.__file__).resolve().parents[1]
_A_LITERAL = 'r"^[A-Za-z0-9_\\-]+$"'
_OP_LITERAL = "[a-zA-Z][a-zA-Z0-9_]{0,63}"


def test_the_two_name_rules_are_named_constants():
    assert add_names.ADDITION_NAME_RE.pattern == r"^[A-Za-z0-9_\-]+$"
    assert add_names.OPERATION_NAME_RE.pattern == "^" + add_names.OPERATION_NAME_PATTERN + "$"
    assert add_names.OPERATION_NAME_PATTERN == _OP_LITERAL
    # the wizard accepts what the Pulses page refuses -- kept apart on purpose
    assert add_names.ADDITION_NAME_RE.match("1x") and not add_names.OPERATION_NAME_RE.match("1x")


def test_every_python_caller_reads_the_name_rules_through_add_names():
    assert additions._NAME_RE is add_names.ADDITION_NAME_RE
    assert routes._PULSE_NAME_RE is add_names.OPERATION_NAME_RE
    assert routes._GATE_NAME_RE is add_names.OPERATION_NAME_RE
    for f in (PKG / "core" / "additions.py", PKG / "web" / "routes.py"):
        src = f.read_text(encoding="utf-8")
        assert _A_LITERAL not in src, f
        assert _OP_LITERAL not in src, f


def test_the_name_inputs_render_the_one_pattern():
    for t in ("_pair_add_gate.html", "_pulse_copy.html", "_pulse_create.html", "_pulse_detail.html"):
        src = (PKG / "web" / "templates" / t).read_text(encoding="utf-8")
        assert _OP_LITERAL not in src, t
        assert "{{ op_name_pattern }}" in src, t


def test_the_wizard_checks_the_same_addition_rule():
    """gen-additions.js cannot import Python: its literal must equal
    ADDITION_NAME_RE's pattern wherever it checks a name."""
    js = (PKG / "web" / "static" / "gen-additions.js").read_text(encoding="utf-8")
    found = re.findall(r"!/(\^\[A-Za-z0-9_[^/]*\$)/\.test\(", js)
    assert found and set(found) == {add_names.ADDITION_NAME_RE.pattern}, found


def test_the_gate_tables_live_in_the_catalogue():
    assert routes._GATE_TYPES is add_catalog.GATE_TYPES
    assert routes._ENV_GATE_TYPES is add_catalog.ENV_GATE_TYPES
    assert routes._ENV_GATE_LEAVES is add_catalog.ENV_GATE_LEAVES
    assert routes._SLOT_LEAVES is add_catalog.SLOT_LEAVES
    assert additions.DEFAULT_GATE_NAME is add_catalog.DEFAULT_GATE_NAME
    assert additions.NODE_DEFAULT_OPERATION is add_catalog.NODE_DEFAULT_OPERATION
    assert additions._CZ_VARIANTS is add_catalog.CZ_VARIANTS
    src = (PKG / "web" / "routes.py").read_text(encoding="utf-8")
    for table in ("_GATE_TYPES", "_ENV_GATE_TYPES", "_ENV_GATE_LEAVES", "_SLOT_LEAVES"):
        assert not re.search(rf"^{table}\s*(:[^=]*)?=\s*{{", src, re.M), table
    assert '"label": "CZ Unipolar (square pulse)"' not in src


def test_the_catalogue_readers():
    assert list(add_catalog.all_gate_types()) == list(add_catalog.GATE_TYPES) + list(add_catalog.ENV_GATE_TYPES)
    assert add_catalog.env_gate_types(None) == {} and add_catalog.env_gate_types({}) == {}
    assert list(add_catalog.env_gate_types({"SNZPulse": {}})) == ["cz_snz"]
    # every slot must be verified: half of the bipolar gate's classes is not enough
    assert add_catalog.env_gate_types({"CosineBipolarPulse": {}}) == {}
    both = {"CosineBipolarPulse": {}, "_FlatTopGaussianPulse": {}, "ErfSquarePulse": {}, "SNZPulse": {}}
    assert list(add_catalog.env_gate_types(both)) == ["cz_bipolar", "cz_snz", "cz_flattop_erf"]
    assert add_catalog.slot_leaves("cz_snz")["coupler"] is None
    assert add_catalog.slot_leaves("cr_gate") == {}


def test_the_seeder_copy_of_the_variants_matches():
    from quam_state_manager.core.script_emitter import _run_build
    assert tuple(_run_build()._CZ_VARIANTS) == add_catalog.CZ_VARIANTS
