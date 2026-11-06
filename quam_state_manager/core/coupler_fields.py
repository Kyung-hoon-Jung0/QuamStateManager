"""Shared presentation of the stored coupler model; validation lives in cz_gate_plan."""

COUPLER_MODEL_FIELDS = (
    dict(key="fc_max_hz", label="Maximum coupler frequency", unit="GHz", scale=1e9, min=0, exclusive_min=True, help="Coupler frequency at its upper sweet spot."),
    dict(key="ec_c_hz", label="Coupler charging energy", unit="MHz", scale=1e6, min=0, exclusive_min=True, help="Positive charging energy expressed as a frequency."),
    dict(key="d_c", label="Junction asymmetry", unit="", scale=1, min=0, max=1, help="Junction asymmetry, at least 0 and less than 1."),
    dict(key="phi0_v", label="Flux period", unit="V", scale=1, min=0, exclusive_min=True, help="Positive voltage change corresponding to one flux quantum."),
    dict(key="phi_idle", label="Idle flux", unit="Phi0", scale=1, help="Idle coupler flux in units of the flux quantum."),
    dict(key="g_c_hz", label="Control coupling", unit="MHz", scale=1e6, min=0, help="Non-negative control qubit to coupler coupling."),
    dict(key="g_t_hz", label="Target coupling", unit="MHz", scale=1e6, min=0, help="Non-negative target qubit to coupler coupling."),
    dict(key="g_ct_hz", label="Direct qubit coupling", unit="MHz", scale=1e6, min=0, help="Non-negative direct coupling between control and target qubits."),
)
