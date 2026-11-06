"""Phase-applying excerpt of the installed gate class, for portable unit proof.

CZGate.apply in quam_builder's flux_tunable_transmon_pair/two_qubit_gates.py
passes the stored values unchanged to the API whose angle unit is 2*pi radians.
The installed source is also checked when available.
"""


class CZGate:
    def apply(self):
        self.qubit_pair.qubit_control.xy.frame_rotation_2pi(self.phase_shift_control)
        self.qubit_pair.qubit_target.xy.frame_rotation_2pi(self.phase_shift_target)
