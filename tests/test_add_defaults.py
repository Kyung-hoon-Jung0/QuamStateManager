"""The catalog fallbacks agree; manifests still own class defaults."""
from quam_state_manager.core import add_catalog, pulse_catalog


def test_flux_amplitude_fallback_is_shared():
    assert add_catalog.FLUX_AMPLITUDE_DEFAULT == 0.1
    for gate in add_catalog.all_gate_types().values():
        for name, _, default, _ in gate["fields"]:
            if name == "amplitude":
                assert default == add_catalog.FLUX_AMPLITUDE_DEFAULT
    assert pulse_catalog.PULSE_CATALOG["SquarePulse"].param("amplitude").default == add_catalog.FLUX_AMPLITUDE_DEFAULT
