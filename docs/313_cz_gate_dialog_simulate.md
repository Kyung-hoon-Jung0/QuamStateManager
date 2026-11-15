# New CZ gate dialog: Simulate (client side)

The Waveform panel of the New CZ gate dialog carries a **Simulate** button (`[data-czg-sim]`), a status line
(`[data-czg-sim-line]`), an elapsed-seconds span and a notes block under the figure (`[data-czg-sim-notes]`).
Code: `quam_state_manager/web/static/cz-gate.js` (section "simulate"); server: `web/cz_gate_sim_routes.py`.

- Enabled once the current check answered for the shown pair with a plan (`ok` and a timeline) and the form has not
  changed since. It POSTs the exact body of that check plus `pair` (the pair on screen) to
  `/api/pulse/cz-gate/simulate/start`; the window id header comes from the app's fetch wrapper.
- Every answer's `line` is shown verbatim. While `starting` / `running` the dialog polls `status?id=` once a second,
  shows `elapsed_s`, and the button reads **Stop** (POST `stop` with the id; after a 409 `busy`, with no id).
- `done`: a port is drawn on the lane whose `port` text equals the port's controller / fem / port AND whose key is
  one of the port's `elements`; the lane gets `sim = {t_ns, v}` with `t_ns[i] = t0_ns + i*dt_ns`, so the chart's
  "Show simulated output" switch appears. A `time_step_unknown` port is never drawn. `time_step_note`, `cut_note`,
  `note` and `cannot_draw` are shown verbatim; an unmatched port and a lane without a port each get one line.
- Any form edit, pair change, new check or close drops the run: polling stops, a running run is stopped, traces,
  line and notes are cleared. A failed fetch or a non-JSON answer shows `Not simulated: the app did not answer.`
- Pins: `tests/cz_gate_simulate_client_selfcheck.cjs` (run on the rendered dialog by `tests/test_cz_gate_routes.py`).
