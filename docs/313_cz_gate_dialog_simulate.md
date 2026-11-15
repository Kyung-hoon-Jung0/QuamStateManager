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
  "Show simulated output" switch appears. One common start latency is estimated from the half-height leading
  edges of the config and simulated output, relative to their pre-pulse baselines (zero for config pulse
  samples, the first voltage for simulated output). The sharpest edge supplies the estimate; all driven lanes
  must agree within one sample. The same shift is applied to every port and stated in the note, preserving port-delay differences.
  Missing output, unknown time steps, cut or decimated output, disagreeing edges, or a negative latency keep the raw
  simulator time axis, with simulator start at 0 ns stated in the note. The simulation window margin is unchanged. A `time_step_unknown` port is never drawn. `time_step_note`, `cut_note`,
  `note` and `cannot_draw` are shown verbatim; an unmatched port and a lane without a port each get one line.
- Any form edit, pair change, new check or close drops the run: polling stops, a running run is stopped, traces,
  line and notes are cleared. A failed fetch or a non-JSON answer shows `Not simulated: the app did not answer.`
- A start answer that arrives after its run was dropped (form edit, pair change, new check, close) and names a
  `starting` / `running` run is stopped by its id; that stop's answer is not shown. A dropped busy or error answer
  sends nothing. Stop stays disabled while `starting` has no id yet, so a second press sends nothing.
- Every request carries a client sequence number; an answer older than the latest start / stop or the latest
  applied answer is ignored, so the line is always the latest action's answer. Stop server side with an id that is
  no longer current is a no-op (`core/port_test_runs.py` `stop`, the id check).
- Pins: `tests/cz_gate_simulate_client_selfcheck.cjs` (run on the rendered dialog by `tests/test_cz_gate_routes.py`).
