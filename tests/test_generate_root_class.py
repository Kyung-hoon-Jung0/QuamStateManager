"""docs/176 — the review can SEE the root, and the wizard can NAME it.

Found trying to generate the KRISS 5Q chip in the environment that lab actually
runs. Four defects, three of them in the browser (pinned by
``tests/generate_root_selfcheck.cjs``, driven below) and one here:

``/generate/capabilities`` built its manifest from ``capabilities`` +
``versions`` only. ``qpu_root_check`` reads ``qpu_roots``, so with the key
absent it saw no roots at all and could never refuse — the review answered
"✓ This environment can build everything this chip needs." for a spec that
``/generate/build`` then rejected outright with a 400. The two doors were
reading two different manifests of the same environment.

The route now also reports every importable root, which is what lets the wizard
offer a root class at all: every lab that drives this wizard has its own Quam
subclass, the probe lists it first, and until now the build always wrote the
stock one.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


# A chip whose qubits are biased by a QDAC-II — the shape whose root class the
# stock quam-builder roots cannot hold (docs/136).
_QDAC_SPEC = {
    "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
    "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"},
                                                        {"slot": 5, "fem": "lf"}]}]},
    "qubits": ["q1"], "qubit_pairs": [], "twpas": [], "pair_gate": "",
    "qdac": {"communication_type": "Ethernet", "ip_address": "10.0.0.1", "port": 5025,
             "qubits": {"q1": {"channel": 1, "trigger_port": "ext1"}}},
    "lines": [{"element": "q1", "line": "resonator"},
              {"element": "q1", "line": "drive"}],
}

_LAB_ROOT = "quam_config.my_quam.Quam"
_STOCK_ROOT = ("quam_builder.architecture.superconducting.qpu"
               ".flux_tunable_quam.FluxTunableQuam")


def _manifest(roots):
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    caps = {cid: {"available": True, "detail": ""} for cid in CATALOG_IDS}
    out = {"ok": True, "cached": False, "error": None,
           "capabilities": caps, "versions": {"quam_builder": "0.4.0"}}
    if roots is not None:
        out["qpu_roots"] = roots
    return out


def _patch(monkeypatch, roots):
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda *a, **k: _manifest(roots))


def _root_only(holds_qdac):
    return [{"path": _STOCK_ROOT, "importable": True, "holds_qdac": holds_qdac,
             "bias_tee": None, "qubits_type": "typing.Dict[str, FluxTunableTransmon]"}]


class TestTheReviewSeesTheRoot:
    """The review door and the build door must read the SAME manifest."""

    def test_the_review_refuses_what_the_build_would_refuse(self, client, monkeypatch):
        _patch(monkeypatch, _root_only(holds_qdac=False))
        r = client.post("/generate/capabilities", json={"spec": _QDAC_SPEC})
        assert r.status_code == 200
        body = r.get_json()
        # `assess` alone still finds nothing wrong — that is the point. The
        # contradiction was the page reading only this half.
        assert body["report"]["buildable"] is True
        assert body["root"]["needed"] is True
        assert body["root"]["blocker"], "the review promised a build the door refuses"
        assert "QPU root class" in body["root"]["blocker"]

    def test_a_root_that_can_hold_the_chip_is_chosen_not_refused(self, client, monkeypatch):
        _patch(monkeypatch, [
            {"path": _LAB_ROOT, "importable": True, "holds_qdac": True,
             "bias_tee": None, "qubits_type": "typing.Dict[str, AnyTransmon]"},
        ])
        body = client.post("/generate/capabilities", json={"spec": _QDAC_SPEC}).get_json()
        assert body["root"]["blocker"] is None
        assert body["root"]["chosen"] == _LAB_ROOT

    def test_an_unprobed_env_is_never_a_refusal(self, client, monkeypatch):
        """Unknown is not a negative — the rule `assess` already follows.

        A manifest with no `qpu_roots` at all (an older cache entry, a probe
        that could not run) must not manufacture a blocker out of its own
        ignorance.
        """
        _patch(monkeypatch, None)
        body = client.post("/generate/capabilities", json={"spec": _QDAC_SPEC}).get_json()
        assert body["root"]["blocker"] is None
        assert body["roots"] == []

    def test_a_chip_with_no_qdac_never_asks_the_question(self, client, monkeypatch):
        _patch(monkeypatch, _root_only(holds_qdac=False))
        spec = {k: v for k, v in _QDAC_SPEC.items() if k != "qdac"}
        body = client.post("/generate/capabilities", json={"spec": spec}).get_json()
        assert body["root"]["needed"] is False
        assert body["root"]["blocker"] is None


class TestTheRootsOffered:
    """What the wizard's picker is built from."""

    def test_only_importable_roots_are_offered(self, client, monkeypatch):
        _patch(monkeypatch, [
            {"path": _LAB_ROOT, "importable": True, "holds_qdac": True,
             "bias_tee": None, "qubits_type": "typing.Dict[str, AnyTransmon]"},
            {"path": "quam_config.broken.Quam", "importable": False,
             "error": "ModuleNotFoundError: no module named 'quam_config.broken'"},
        ])
        body = client.post("/generate/capabilities", json={"spec": _QDAC_SPEC}).get_json()
        # A root that does not import cannot root anything; offering it by name
        # would be offering a build that fails after the subprocess starts.
        assert [r["path"] for r in body["roots"]] == [_LAB_ROOT]

    def test_the_probe_order_is_kept(self, client, monkeypatch):
        """The lab's own root is listed first BY THE PROBE; the route must not
        re-sort it behind the stock ones."""
        _patch(monkeypatch, [
            {"path": _LAB_ROOT, "importable": True, "holds_qdac": True,
             "bias_tee": None, "qubits_type": "typing.Dict[str, AnyTransmon]"},
            {"path": _STOCK_ROOT, "importable": True, "holds_qdac": False,
             "bias_tee": None, "qubits_type": "typing.Dict[str, FluxTunableTransmon]"},
        ])
        body = client.post("/generate/capabilities", json={"spec": _QDAC_SPEC}).get_json()
        assert [r["path"] for r in body["roots"]] == [_LAB_ROOT, _STOCK_ROOT]

    def test_each_offered_root_says_what_it_holds(self, client, monkeypatch):
        _patch(monkeypatch, _root_only(holds_qdac=True))
        body = client.post("/generate/capabilities", json={"spec": _QDAC_SPEC}).get_json()
        assert body["roots"][0]["qubits_type"]

    def test_a_declared_root_the_env_cannot_hold_is_refused_by_name(self, client, monkeypatch):
        """A person can name a root. Naming one the probe KNOWS cannot hold the
        chip is refused, not warned — the file would be written and would not
        open."""
        _patch(monkeypatch, _root_only(holds_qdac=False))
        spec = dict(_QDAC_SPEC, quam_class=_STOCK_ROOT)
        body = client.post("/generate/capabilities", json={"spec": spec}).get_json()
        assert body["root"]["chosen"] == _STOCK_ROOT
        assert body["root"]["blocker"] and _STOCK_ROOT in body["root"]["blocker"]


# ── the three browser-side halves ────────────────────────────────────────────

_SELFCHECK = _ROOT / "tests" / "generate_root_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_root_selfcheck_passes():
    """Drives the shipped renderers under node + jsdom.

    Pins: a STRING blocker rendering as the sentence it is (the object template
    printed "• undefined — needs ? · (missing). Fix:" and threw away the only
    text that said what was wrong); the review's verdict never claiming
    "everything" directly above a list of what will be skipped; a root refusal
    shown as a blocker in the review rather than as a surprise 400 later; and
    the root-class picker, whose "Automatic" default puts nothing on the spec
    so today's behaviour is byte-for-byte unchanged until a person picks.
    """
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "generate_root_selfcheck ok" in r.stdout, (r.stdout + r.stderr)
