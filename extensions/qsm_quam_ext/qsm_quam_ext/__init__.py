"""QUAM extensions for fixed-frequency transmons with tunable couplers."""
from dataclasses import field
from typing import Optional, Union

from qm.qua import align, wait
from quam.core import quam_dataclass
from quam.components.macro import QubitPairMacro
from quam.components.pulses import Pulse
from quam_builder.architecture.superconducting.qubit_pair.fixed_frequency_transmon_pair import FixedFrequencyTransmonPair
from quam_builder.architecture.superconducting.components.tunable_coupler import TunableCoupler

__all__ = ["FixedFrequencyTunableCouplerPair", "CouplerCZGate"]


@quam_dataclass
class FixedFrequencyTunableCouplerPair(FixedFrequencyTransmonPair):
    """Synchronize both qubits and the optional coupler."""

    coupler: Optional[TunableCoupler] = None

    def _channel_names(self):
        names = {ch.name for q in (self.qubit_control, self.qubit_target)
                 for ch in q.channels.values()}
        if self.coupler is not None:
            names.add(self.coupler.name)
        return sorted(names)

    def align(self):
        align(*self._channel_names())

    def wait(self, duration):
        """Wait on every participating channel; duration is in clock cycles."""
        wait(duration, *self._channel_names())


@quam_dataclass
class CouplerCZGate(QubitPairMacro):
    """Play coupler flux and apply virtual Z corrections in full turns."""

    coupler_flux_pulse: Union[Pulse, str]
    phase_shift_control: float = 0.0
    phase_shift_target: float = 0.0
    duration: int = None
    extras: dict = field(default_factory=dict)
    fidelity: dict = field(default_factory=dict)

    @property
    def coupler_flux_pulse_label(self):
        pulse = self.coupler_flux_pulse
        if isinstance(pulse, str):
            pulse = self.qubit_pair.coupler.get_pulse(pulse)
        if pulse.id is not None:
            return pulse.id
        return pulse.parent.get_attr_name(pulse)

    def apply(self, *, amplitude_scale_coupler=None, duration=None,
              phase_shift_control=None, phase_shift_target=None, **kwargs):
        """Accept official phase overrides, including QUA scalar expressions.

        Auxiliary kwargs follow the official CZGate compatibility convention.
        Duration overrides and the stored duration are in clock cycles.
        """
        pair = self.qubit_pair
        pair.align()
        pair.coupler.play(
            self.coupler_flux_pulse_label, validate=False,
            amplitude_scale=amplitude_scale_coupler,
            duration=self.duration if duration is None else duration,
        )
        pair.align()
        if phase_shift_control is not None:
            pair.qubit_control.xy.frame_rotation_2pi(phase_shift_control)
        elif abs(self.phase_shift_control) > 1e-6:
            pair.qubit_control.xy.frame_rotation_2pi(self.phase_shift_control)
        if phase_shift_target is not None:
            pair.qubit_target.xy.frame_rotation_2pi(phase_shift_target)
        elif abs(self.phase_shift_target) > 1e-6:
            pair.qubit_target.xy.frame_rotation_2pi(self.phase_shift_target)
        pair.align()
