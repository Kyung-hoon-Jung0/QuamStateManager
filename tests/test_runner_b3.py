"""Runner+agent §17 B3 (docs/78): every verdict records the context that made it.

D-13.3 is binding — *"a verification context is (env, source root, run
generation); every verdict records all three."* Three paths produce verdicts and
before this they carried three different answers: `fit_audit.audit_run` put env
and root in its cache KEY and handed the payload back unlabelled, `figure_gen`
carried all four, the engine's gate verdicts carried none.

The stamp is not bookkeeping. Sixteen shipped gate bands were overturned by
measurement in a single session; a verdict that cannot name the revision that
produced it silently mixes with verdicts that mean something else.
"""
from __future__ import annotations

import json

from quam_state_manager.core.autofit import consistency, gates, verification


def _ctx(**kw):
    base = dict(analysis=verification.SM_GATES, analysis_rev="a" * 64,
                run_generation="g" * 64)
    base.update(kw)
    return verification.VerificationContext(**base)


class TestTheShape:
    def test_a_gate_verdict_carries_its_context_through_as_dict(self):
        """The ledger writes `**v.as_dict()` — a field the dict drops is a
        field the record never had."""
        v = gates.GateVerdict(target="qA1", verdict="pass",
                              context=_ctx().as_dict())
        d = v.as_dict()
        assert d["context"]["analysis"] == "sm_gates"
        assert d["context"]["analysis_rev"] == "a" * 64
        # and it must survive the ledger's own json round-trip
        assert json.loads(json.dumps(d, default=str))["context"]

    def test_sm_gate_analysis_rev_is_the_bands_plus_the_pipeline(self):
        """`families.py` holds every band and `gates.py` reads them; a change
        to either changes what "pass" means."""
        rev = verification.sm_analysis_rev()
        assert isinstance(rev, str) and len(rev) == 64
        assert verification.sm_analysis_rev() == rev      # stable per process

    def test_an_sm_gate_verdict_never_invents_an_env(self):
        """No interpreter is spawned for deterministic gates, so naming one
        would be a fiction — and `missing()` must not demand one."""
        c = verification.for_sm_gates(None, generation="g")
        assert c.env is None and c.analysis == "sm_gates"
        assert c.missing() == []

    def test_an_unresolvable_generation_reads_as_unknown_not_as_a_default(self):
        c = verification.for_sm_gates(None, generation=None)
        assert c.run_generation is None
        assert "run generation" in c.missing()
        assert "unrecorded" in c.describe()


class TestComparability:
    def test_two_verdicts_from_different_gate_revisions_are_not_comparable(self):
        ok, why = verification.comparable(_ctx(analysis_rev="a" * 64),
                                          _ctx(analysis_rev="b" * 64))
        assert ok is False and "gate revisions" in why

    def test_two_verdicts_from_different_state_generations_are_not_comparable(self):
        ok, _ = verification.comparable(_ctx(run_generation="g1"),
                                        _ctx(run_generation="g2"))
        assert ok is False

    def test_the_same_context_compares(self):
        ok, why = verification.comparable(_ctx(), _ctx())
        assert ok is True and "same" in why

    def test_an_lab_replay_verdict_never_compares_to_a_gate_verdict(self):
        """They are different analyses; their verdicts do not mean the same
        thing, and a shared key would let one vouch for the other."""
        lab = verification.for_lab_replay(
            env="py.exe", source_root=None, lib_versions={"quam": "0.5"},
            gate_hash="h", generation="g")
        ok, why = verification.comparable(lab, _ctx(run_generation="g"))
        assert ok is False and "different analyses" in why

    def test_an_unknown_context_is_not_a_matching_one(self):
        """An unverifiable premise is not a satisfied one — the same rule as
        the class-B preconditions."""
        assert verification.comparable(_ctx(), None)[0] is False
        assert verification.comparable(_ctx(), {})[0] is False

    def test_figure_source_does_not_split_a_comparison(self):
        """What the judge LOOKED at is evidence, not analysis. It is reported
        but it must not make two gate verdicts incomparable."""
        a = _ctx(figure_source="archived")
        b = _ctx(figure_source="none")
        assert verification.comparable(a, b)[0] is True

    def test_a_dirty_live_tree_is_reported_not_disqualifying(self):
        c = verification.for_lab_replay(
            env="py.exe", source_root=None, root_kind="live", root_rev="abc",
            root_dirty=True, gate_hash="h", generation="g")
        assert "uncommitted" in c.describe()
        assert c.missing() == []


class TestLabReplayStamp:
    def test_no_source_root_is_a_named_kind_not_a_blank(self):
        """`installed` is a real answer: the analysis is whatever the env has.
        Leaving it None would read as "unrecorded", which is a different claim."""
        d = verification.describe_root(None)
        assert d["root_kind"] == "installed"
        c = verification.for_lab_replay(env="py.exe", source_root=None,
                                        gate_hash="h", generation="g")
        assert c.missing() == []

    def test_a_materialized_pinned_root_is_recognised_without_git(self, tmp_path):
        """`sourceroot.materialize` writes `.sm_pinned_ok`; the pinned cache is
        not a git tree, so the marker is the only truth available."""
        root = tmp_path / "deadbeef"
        root.mkdir()
        (root / ".sm_pinned_ok").write_text("deadbeef" * 5, encoding="utf-8")
        d = verification.describe_root(str(root))
        assert d["root_kind"] == "pinned"
        assert d["root_rev"] == "deadbeef" * 5
        assert d["root_dirty"] is False

    def test_a_plain_folder_is_unversioned_not_live(self, tmp_path):
        d = verification.describe_root(str(tmp_path))
        assert d["root_kind"] == "unversioned" and d["root_rev"] is None

    def test_describe_never_dumps_a_raw_hash(self):
        c = verification.for_lab_replay(
            env="py.exe", source_root="/x", root_kind="pinned",
            root_rev="0123456789abcdef0123", lib_versions={"quam": "0.6.0"},
            gate_hash="h", generation="g")
        text = c.describe()
        assert "0123456789ab" in text and "0123456789abcdef0123" not in text
        assert "quam 0.6.0" in text


class TestTheReviewRefusesMixedContexts:
    """The cross-experiment review is the one place that reasons ACROSS runs,
    so it is the one place D-13 can actually bite."""

    ENTRIES = {
        ("resonator_spectroscopy", "qA1"): {"frequency": 7.0e9, "fwhm": 1e6},
        ("resonator_spectroscopy_vs_flux", "qA1"): {
            "resonator_frequency": 7.9e9, "fwhm": 1e6},
    }

    def test_without_contexts_the_behaviour_is_unchanged(self):
        rep = consistency.reconcile(self.ENTRIES)
        assert len(rep.findings) == 1          # 900 MHz apart on a 1 MHz line

    def test_same_context_still_finds_the_contradiction(self):
        ctxs = {k: _ctx().as_dict() for k in self.ENTRIES}
        rep = consistency.reconcile(self.ENTRIES, contexts=ctxs)
        assert len(rep.findings) == 1

    def test_different_gate_revisions_are_not_reported_as_a_contradiction(self):
        """Two values read by two different sets of bands disagreeing is a
        category error, not physics — reporting it as physics is how a review
        loses its authority."""
        ctxs = {("resonator_spectroscopy", "qA1"): _ctx(analysis_rev="a" * 64).as_dict(),
                ("resonator_spectroscopy_vs_flux", "qA1"): _ctx(analysis_rev="b" * 64).as_dict()}
        rep = consistency.reconcile(self.ENTRIES, contexts=ctxs)
        assert rep.findings == []
        assert any("different verification context" in s for s in rep.skipped)

    def test_the_skip_is_never_silent(self):
        ctxs = {("resonator_spectroscopy", "qA1"): _ctx(analysis_rev="a" * 64).as_dict(),
                ("resonator_spectroscopy_vs_flux", "qA1"): None}
        rep = consistency.reconcile(self.ENTRIES, contexts=ctxs)
        assert rep.skipped, "a dropped comparison must say so"

    def test_the_majority_context_is_kept_not_the_first_family(self):
        """Three families under a new revision and one under the old: the
        three are worth comparing and the one is named."""
        entries = dict(self.ENTRIES)
        entries[("resonator_spectroscopy_vs_power", "qA1")] = {
            "resonator_frequency": 7.0e9, "fwhm": 1e6}
        new = _ctx(analysis_rev="n" * 64).as_dict()
        ctxs = {("resonator_spectroscopy", "qA1"): new,
                ("resonator_spectroscopy_vs_power", "qA1"): new,
                ("resonator_spectroscopy_vs_flux", "qA1"):
                    _ctx(analysis_rev="o" * 64).as_dict()}
        rep = consistency.reconcile(entries, contexts=ctxs)
        # the two same-context values agree, so nothing is reported…
        assert rep.findings == []
        # …and the odd one out is named rather than dropped quietly
        assert any("vs_flux" in s for s in rep.skipped)
