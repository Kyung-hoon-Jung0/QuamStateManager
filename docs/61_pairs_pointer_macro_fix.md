# 61 — Pairs page crash on pointer-form CZ macros

**Report:** on a customer chip with double-digit qubit numbering, pressing
**Pairs** raised an error and the menu never opened; every other page worked.

**Root cause — not the numbering.** The chip's CZ gate macros store
`flux_pulse_qubit` as a JSON REFERENCE to the op on the moving qubit's z line
(`"#/qubits/q1/z/operations/cz_unipolar_flux_pulse_q1_q2"`) — exactly the
form `run_build._seed_cz_variant` and the shared pair_gates recipe write (the
op is the single calibration home; the macro points at it). `query.get_pair`
assumed an inline dict and called `.get()` on the raw pointer string →
`AttributeError` → the whole `/pairs` route 500'd. `coupler_flux_pulse` had
the identical latent bug for tunable-coupler chips. (The numbering itself was
the separate lexicographic-sort issue fixed in docs/59.)

**Fix:** `query._deref_pulse_ref` — inline dicts pass through unchanged
(byte-identical for every existing chip); a pointer resolves to its target
dict through the store's per-instance pointer cache, with the path re-anchored
to the TARGET's frame so inner relative pointers keep resolving correctly; a
dangling/self-ref/non-dict target degrades to blank fields (one broken pair
must never 500 the page). Applied to both `flux_pulse_qubit` and
`coupler_flux_pulse` in `get_pair`.

**Verified** on the real customer chip: `/pairs`, `/pair/<id>`, `/qubits`,
`/bulk`, `/table` all 200; the pair table shows the real calibrated values
read through the pointer (amplitude 0.2, length 40 ns). Regression tests:
`test_query.py::TestGetPair::test_pointer_form_flux_pulse_macro` +
`test_dangling_pointer_macro_degrades_blank`.
