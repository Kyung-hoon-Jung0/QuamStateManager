"""Catalog fallbacks agree with the optional quam environment defaults."""
from dataclasses import fields

import pytest

from quam_state_manager.core.pulse_catalog import PULSE_CATALOG

DEFAULTS = [("Pulse", "digital_marker", None),
            ("FlatTopCosinePulse", "flat_length", 0),
            ("FlatTopTanhPulse", "flat_length", 0)]


@pytest.mark.parametrize("name,field,expected", DEFAULTS)
def test_catalog_fallback_default(name, field, expected):
    assert PULSE_CATALOG[name].param(field).default == expected


@pytest.mark.parametrize("name,field,expected", DEFAULTS)
def test_catalog_default_matches_environment_dataclass(name, field, expected):
    pulses = pytest.importorskip("quam.components.pulses")
    env_field = next(f for f in fields(getattr(pulses, name)) if f.name == field)
    assert env_field.default == expected
    assert PULSE_CATALOG[name].param(field).default == env_field.default
