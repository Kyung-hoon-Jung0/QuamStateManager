"""Autofit LLM auditor — contract tests (docs/56 §6.4). Fake provider only;
no network anywhere. Pins: the number-free verdict schema (numeric emissions
discarded + flagged), budget cap, abstain-on-failure, settings round-trip."""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core.autofit import auditor


def _mk(script=None, **settings):
    fake = auditor.FakeProvider(script or {})
    s = {"provider": "fake", "max_calls_per_plan": 3}
    s.update(settings)
    return auditor.Auditor(s, fake_provider=fake), fake


def _bundle(target="qA1"):
    return auditor.build_bundle(family_label="Qubit spectroscopy",
                                target=target,
                                fit_entry={"frequency": 5.1e9, "r2": 0.5,
                                           "success": True},
                                gate_reasons=["r2=0.5 violates [0.75, None]"])


class TestVerdictContract:
    def test_scripted_verdicts_round_trip(self):
        a, fake = _mk({"qA1": {"verdict": "reject", "failure_mode": "wrong_peak",
                               "reason": "peak sits left of the marker"}})
        v = a.audit(_bundle())
        assert v.verdict == "reject"
        assert v.failure_mode == "wrong_peak"
        assert "marker" in v.reason
        assert len(fake.calls) == 1
        # the numeric context reached the provider, the image slot exists
        ctx = fake.calls[0]["context"]
        assert ctx["claimed_fit"]["frequency"] == 5.1e9
        assert "deterministic_gate_concerns" in ctx

    def test_numeric_emission_is_discarded_and_flagged(self):
        a, _ = _mk({"qA1": {"verdict": "accept", "failure_mode": None,
                            "reason": "fine", "corrected_frequency": 5.2e9}})
        v = a.audit(_bundle())
        assert v.verdict == "accept"
        assert v.discarded_numeric is True
        assert "corrected_frequency" not in v.as_dict()
        # nothing numeric beyond the flag survives into the ledger dict
        assert all(not isinstance(x, float) for x in v.as_dict().values())

    def test_invalid_or_unparseable_becomes_abstain(self):
        a, fake = _mk()
        fake.script["qA1"] = {"verdict": "APPROVE!!", "reason": "?"}
        assert a.audit(_bundle()).verdict == "abstain"

        class Garbage(auditor.FakeProvider):
            def __call__(self, bundle):
                return "sure, looks good to me!"
        a2 = auditor.Auditor({"provider": "fake"}, fake_provider=Garbage())
        assert a2.audit(_bundle()).verdict == "abstain"

    def test_unknown_failure_mode_normalized_to_none(self):
        a, _ = _mk({"qA1": {"verdict": "reject",
                            "failure_mode": "gremlins", "reason": "x"}})
        assert a.audit(_bundle()).failure_mode is None


class TestBudgetAndAvailability:
    def test_budget_cap_yields_abstain(self):
        a, fake = _mk({"qA1": {"verdict": "accept", "reason": "ok"}},
                      max_calls_per_plan=2)
        assert a.audit(_bundle()).verdict == "accept"
        assert a.audit(_bundle()).verdict == "accept"
        v = a.audit(_bundle())
        assert v.verdict == "abstain" and "budget" in v.reason
        assert len(fake.calls) == 2                 # capped call never reaches it

    def test_off_and_unconfigured_providers_are_disabled(self):
        assert auditor.Auditor({"provider": "off"}).enabled is False
        assert auditor.Auditor({"provider": "anthropic"}).enabled is False
        assert auditor.Auditor({"provider": "anthropic",
                                "api_key": "k"}).enabled is True
        assert auditor.Auditor({"provider": "openai_compat"}).enabled is False
        v = auditor.Auditor({"provider": "off"}).audit(_bundle())
        assert v.verdict == "abstain"

    def test_provider_exception_becomes_abstain(self):
        class Boom(auditor.FakeProvider):
            def __call__(self, bundle):
                raise OSError("connection refused")
        a = auditor.Auditor({"provider": "fake"}, fake_provider=Boom())
        v = a.audit(_bundle())
        assert v.verdict == "abstain" and "provider error" in v.reason


class TestSettings:
    def test_round_trip_and_unknown_keys_dropped(self, tmp_path):
        out = auditor.save_settings(tmp_path, {"provider": "openai_compat",
                                               "base_url": "http://localhost:11434",
                                               "evil_extra": "x"})
        assert out["provider"] == "openai_compat"
        assert "evil_extra" not in out
        loaded = auditor.load_settings(tmp_path)
        assert loaded["base_url"] == "http://localhost:11434"
        assert loaded["max_calls_per_plan"] == 40   # defaults preserved

    def test_missing_file_gives_defaults(self, tmp_path):
        assert auditor.load_settings(tmp_path)["provider"] == "off"


class TestBundle:
    def test_figure_is_optional_and_binary_safe(self, tmp_path):
        png = tmp_path / "f.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        b = auditor.build_bundle(family_label="x", target="qA1",
                                 fit_entry={"a": 1.0},
                                 gate_reasons=[], figure_path=png)
        import base64
        assert base64.b64decode(b["image_b64"]).startswith(b"\x89PNG")
        b2 = auditor.build_bundle(family_label="x", target="qA1",
                                  fit_entry={}, gate_reasons=[],
                                  figure_path=tmp_path / "missing.png")
        assert b2["image_b64"] is None
