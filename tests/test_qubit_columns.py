"""Tests for ``core.qubit_columns.derive_qubit_columns`` — the dynamic, opt-in
column derivation behind Table View full coverage (r6 item 4).

Corpus-shaped synthetic fixture: a qubit with xy/z/resonator channels, an
``operations`` subtree carrying per-neighbor suffixed ops + an alias pointer,
a ``confusion_matrix`` list, ``extras``, and a z channel whose ``opx_output``
points through wiring to a port with an ``exponential_filter`` list + scalar
leaves + a multi-DUC ``upconverters`` dict. Pins:

* derivation dedupes vs the curated ``_BULK_COLUMNS_SPEC`` templates;
* per-neighbor suffixed ops templatize into ONE column (anchored strip);
* ``listedit`` kind for matrices + ``exponential_filter``;
* port leaves present (through the pointer chain), ``upconverters`` skipped;
* everything ``default_on=False``; all-null columns dropped;
* the cache invalidates on a ``mutation_seq`` bump (and not before).
"""

from __future__ import annotations

import threading

from quam_state_manager.core.param_specs import _BULK_COLUMNS_SPEC
from quam_state_manager.core.qubit_columns import derive_qubit_columns


class _FakeStore:
    """Minimal store: derive_qubit_columns only touches _lock / merged /
    qubit_names / qubit_pair_names / mutation_seq."""

    def __init__(self, state: dict):
        self._lock = threading.RLock()
        self.merged = state
        self.mutation_seq = 0

    @property
    def qubit_names(self) -> list[str]:
        return list((self.merged.get("qubits") or {}).keys())

    @property
    def qubit_pair_names(self) -> list[str]:
        return list((self.merged.get("qubit_pairs") or {}).keys())


def _qubit(qid: str, neighbor: str) -> dict:
    return {
        "id": qid, "__class__": "Transmon",
        "f_01": 6.25e9,                       # curated → deduped
        "custom_scalar": 0.5,                 # NOT curated → derived
        "always_null": None,                  # null on every qubit → dropped
        "xy": {
            "RF_frequency": 6.25e9,           # curated → deduped
            "operations": {
                "x180": "#./x180_DragCosine",             # alias pointer
                "x180_DragCosine": {"amplitude": 0.11, "alpha": -1.0,
                                    "digital_marker": "ON"},
                # per-neighbor CR drive: a DIFFERENT pair suffix per qubit
                "cr_cosine_%s-%s" % (qid, neighbor): {"amplitude": 0.2},
            },
        },
        "z": {
            "joint_offset": 0.05,             # curated → deduped
            # channel port as a wiring POINTER → expanded into Z Port+ leaves
            "opx_output": "#/wiring/qubits/%s/z/opx_output" % qid,
            "operations": {
                # per-neighbor flux pulse: suffixed with the OTHER qubit's id
                "cz_flattop_pulse_%s" % neighbor: {"amplitude": 0.1, "length": 48},
            },
        },
        "resonator": {
            "f_01": 7.6e9,                    # curated → deduped
            "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]],   # list → listedit
            "operations": {"readout": {"amplitude": 0.04}},
        },
        "extras": {"custom_gain": 1.25},
    }


def _state() -> dict:
    return {
        "qubits": {"qA1": _qubit("qA1", "qA2"), "qA2": _qubit("qA2", "qA1")},
        "qubit_pairs": {"qA1-qA2": {"id": "qA1-qA2"}, "qA2-qA1": {"id": "qA2-qA1"}},
        "ports": {"analog_outputs": {"con1": {"2": {
            "1": {"offset": 0.0,                       # curated (z_offset) → deduped
                  "delay": 24,                          # curated (z_delay) → deduped
                  "exponential_filter": [[0.8, 120.0]],  # list → listedit
                  "feedback_gain": 0.02,                 # scalar → derived
                  "upconverters": {"1": {"frequency": 4.3e9}}},  # nested dict → skipped
        }}}},
        "wiring": {"qubits": {
            "qA1": {"z": {"opx_output": "#/ports/analog_outputs/con1/2/1"}},
            "qA2": {"z": {"opx_output": "#/ports/analog_outputs/con1/2/1"}},
        }},
    }


def _derive(state: dict):
    return derive_qubit_columns(_FakeStore(state))


def _tmpls(cols) -> set[str]:
    return {c["tmpl"] for c in cols if c.get("kind") != "note"}


def _by_tmpl(cols) -> dict:
    return {c["tmpl"]: c for c in cols if c.get("kind") != "note"}


class TestDedupe:
    def test_curated_templates_never_derived(self):
        cols, curated = _derive(_state())
        assert curated == {c["tmpl"] for c in _BULK_COLUMNS_SPEC}
        assert not (_tmpls(cols) & curated), "derived model must not twin a curated column"

    def test_curated_port_leaves_deduped_but_new_ones_kept(self):
        cols, _ = _derive(_state())
        tmpls = _tmpls(cols)
        # delay + offset are curated z-port columns → deduped
        assert "qubits.{name}.z.opx_output.delay" not in tmpls
        assert "qubits.{name}.z.opx_output.offset" not in tmpls
        # the non-curated port scalar IS derived
        assert "qubits.{name}.z.opx_output.feedback_gain" in tmpls

    def test_non_curated_qubit_scalar_derived(self):
        cols, _ = _derive(_state())
        assert "qubits.{name}.custom_scalar" in _tmpls(cols)


class TestPerNeighborOps:
    def test_pair_suffixed_ops_fold_into_one_column(self):
        # qA1 carries cr_cosine_qA1-qA2, qA2 carries cr_cosine_qA2-qA1 — the
        # anchored entity strip folds both into ONE template.
        cols, _ = _derive(_state())
        cr = [t for t in _tmpls(cols) if "cr_cosine" in t]
        assert cr == ["qubits.{name}.xy.operations.cr_cosine.amplitude"], cr

    def test_qubit_suffixed_ops_fold_into_one_column(self):
        cols, _ = _derive(_state())
        cz = sorted(t for t in _tmpls(cols) if "cz_flattop_pulse" in t)
        assert cz == ["qubits.{name}.z.operations.cz_flattop_pulse.amplitude",
                      "qubits.{name}.z.operations.cz_flattop_pulse.length"], cz

    def test_mid_string_entity_never_corrupted(self):
        state = _state()
        # contains "qA2" mid-id, no trailing _<entity> — must survive verbatim
        state["qubits"]["qA1"]["xy"]["operations"]["pulse_qA2_extra"] = {"amplitude": 0.3}
        cols, _ = _derive(state)
        assert "qubits.{name}.xy.operations.pulse_qA2_extra.amplitude" in _tmpls(cols)

    def test_alias_pointer_is_a_runtime_column(self):
        # operations.x180 = "#./x180_DragCosine" — a #./ leaf, read-only like the
        # pair grid (editing would overwrite the pointer with a literal).
        cols, _ = _derive(_state())
        col = _by_tmpl(cols).get("qubits.{name}.xy.operations.x180")
        assert col is not None and col["kind"] == "runtime"

    def test_digital_marker_is_included(self):
        cols, _ = _derive(_state())
        assert "qubits.{name}.xy.operations.x180_DragCosine.digital_marker" in _tmpls(cols)


class TestKinds:
    def test_confusion_matrix_is_listedit(self):
        cols, _ = _derive(_state())
        col = _by_tmpl(cols)["qubits.{name}.resonator.confusion_matrix"]
        assert col["kind"] == "listedit"

    def test_exponential_filter_is_listedit(self):
        cols, _ = _derive(_state())
        col = _by_tmpl(cols)["qubits.{name}.z.opx_output.exponential_filter"]
        assert col["kind"] == "listedit"
        assert "Port" in col["section"]

    def test_scalars_are_edit(self):
        cols, _ = _derive(_state())
        assert _by_tmpl(cols)["qubits.{name}.extras.custom_gain"]["kind"] == "edit"


class TestPortLeaves:
    def test_port_leaves_resolve_through_the_pointer_chain(self):
        cols, _ = _derive(_state())
        col = _by_tmpl(cols)["qubits.{name}.z.opx_output.feedback_gain"]
        assert col["section"] == "Z Port+"

    def test_upconverters_dict_never_becomes_columns(self):
        cols, _ = _derive(_state())
        assert not any(".upconverters" in t for t in _tmpls(cols)), \
            "nested port dicts (multi-DUC) must not become columns"

    def test_raw_wiring_pointer_is_not_a_column(self):
        cols, _ = _derive(_state())
        assert "qubits.{name}.z.opx_output" not in _tmpls(cols)

    def test_broken_port_pointer_yields_no_columns(self):
        state = _state()
        for q in state["qubits"].values():
            q["z"]["opx_output"] = "#/wiring/qubits/missing/z/opx_output"
        state["wiring"] = {"qubits": {}}
        cols, _ = _derive(state)   # must not crash; no Z Port+ columns
        assert not any(c["section"] == "Z Port+" for c in cols)


class TestModelSanity:
    def test_all_columns_default_off(self):
        cols, _ = _derive(_state())
        assert cols and all(c["default_on"] is False for c in cols)

    def test_all_null_columns_dropped(self):
        cols, _ = _derive(_state())
        assert "qubits.{name}.always_null" not in _tmpls(cols)

    def test_column_shape(self):
        cols, _ = _derive(_state())
        for c in cols:
            assert {"key", "label", "section", "unit", "tmpl", "kind", "default_on"} <= set(c)
            assert c["key"].startswith("dyn__") or c["kind"] == "note"
            assert c["kind"] in ("edit", "listedit", "runtime", "note")

    def test_sections_channels_before_extras(self):
        cols, _ = _derive(_state())
        secs = [c["section"] for c in cols]
        assert secs.index("XY+") < secs.index("Qubit+") < secs.index("Extras")
        # a channel's Port+ group sits right with its channel block
        assert secs.index("Z+") < secs.index("Z Port+") < secs.index("Resonator+")

    def test_empty_when_no_qubits(self):
        cols, curated = _derive({"qubits": {}, "qubit_pairs": {}})
        assert cols == [] and curated == {c["tmpl"] for c in _BULK_COLUMNS_SPEC}


class TestCache:
    def test_cache_hit_until_mutation_seq_bumps(self):
        store = _FakeStore(_state())
        cols1, _ = derive_qubit_columns(store)
        # mutate the merged tree WITHOUT bumping mutation_seq → stale cache hit
        store.merged["qubits"]["qA1"]["brand_new_leaf"] = 3.14
        cols2, _ = derive_qubit_columns(store)
        assert _tmpls(cols2) == _tmpls(cols1), "same seq → cached model"
        # bump the seq → fresh derivation picks the new leaf up
        store.mutation_seq += 1
        cols3, _ = derive_qubit_columns(store)
        assert "qubits.{name}.brand_new_leaf" in _tmpls(cols3)

    def test_cached_result_returns_copies(self):
        store = _FakeStore(_state())
        cols1, _ = derive_qubit_columns(store)
        cols1[0]["group_start"] = True          # route-style stamping
        cols1[0]["label"] = "clobbered"
        cols2, _ = derive_qubit_columns(store)
        assert "group_start" not in cols2[0]
        assert cols2[0]["label"] != "clobbered"
