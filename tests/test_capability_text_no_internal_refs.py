"""Messages a build refusal shows the user never point at an internal design note.

The Generate wizard prints these strings verbatim in the Review step and the build
result. An installed copy ships no ``docs/`` folder, so "see docs/NNN" sends the
reader nowhere.
"""
import re

from quam_state_manager.core import capabilities

_DOC_REF = re.compile(r"docs/\d+")


def _all_user_strings():
    for table in (capabilities.REGISTRY, capabilities.SYNTHETIC_REGISTRY):
        for cid, meta in table.items():
            for key in ("label", "symbol", "produces", "fix", "package"):
                v = meta.get(key)
                if isinstance(v, str):
                    yield f"{cid}.{key}", v


def test_registry_texts_name_no_internal_doc():
    bad = [(k, v) for k, v in _all_user_strings() if _DOC_REF.search(v)]
    assert not bad, bad


def test_qdac_root_blocker_names_no_internal_doc():
    spec = {"qubits": ["q1"], "qubit_pairs": [], "lines": [],
            "qdac": {"qubits": {"q1": {"channel": 1}}}}
    manifest = {"qpu_roots": [{"path": "pkg.Root", "importable": True, "holds_qdac": False,
                               "holds": ["FixedFrequencyTransmon"]}]}
    out = capabilities.qpu_root_check(spec, manifest)
    msg = out.get("blocker") or ""
    assert msg, out
    assert "qdac" in msg.lower()
    assert not _DOC_REF.search(msg), msg
