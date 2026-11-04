"""The QOP version: carried by a re-generate, its caveats on the wizard's
Review step only (never in the build result)."""
from pathlib import Path

from quam_state_manager.core.regen_spec import reconstruct_spec
from tests.test_port_identity import state, wiring

_ROOT = Path(__file__).resolve().parent.parent


def test_a_chip_that_records_its_qop_version_carries_it_into_the_spec():
    s = state()
    s.setdefault("extras", {})["qop_version"] = "3.4.2"
    assert reconstruct_spec(s, wiring()).spec["qop_version"] == "3.4.2"


def test_an_unversioned_chip_reconstructs_with_no_qop_key():
    s = state()
    s.pop("extras", None)
    assert "qop_version" not in reconstruct_spec(s, wiring()).spec


def test_the_build_result_carries_no_port_sharing_caveat():
    src = (_ROOT / "quam_state_manager" / "generator" / "run_build.py").read_text(encoding="utf-8")
    body = src[src.index("        machine.save()\n"):]
    body = body[:body.index("# 4. Copy ONLY the two artefacts")]
    assert "_PORT_SHARING_NOTE" not in body and "pair(s) in use" not in body


def test_the_wizard_offers_only_versions_with_a_qm_release_page():
    # hidden_pages/QOP33x.md .. QOP37x.md are the release pages the rules cite
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "generate.js").read_text(encoding="utf-8")
    line = next(l for l in js.splitlines() if l.strip().startswith("var QOP_VERSIONS"))
    offered = set(line.split("[", 1)[1].split("]", 1)[0].replace('"', "").replace(" ", "").split(","))
    assert offered == {"3.3", "3.4", "3.5", "3.6", "3.7"}
