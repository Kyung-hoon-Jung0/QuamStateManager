# CZ gate plan -- frozen route answers (the client contract)

Real answers of `/cz-plan/map`, `/cz-plan/loss` and `/cz-plan/data` produced by the Flask test client
on three chip copies, frozen for the map / detail clients and asserted against the live routes by
`tests/test_cz_loss.py::test_the_routes_answer_in_the_frozen_contract_shape`.

| file | chip | data | what it shows |
|---|---|---|---|
| `*_rig5q.json` | 5 qubits, a chain | 15 population-loss runs | the customer layout: the chip declares an EMPTY data folder; the runs sit in the qualibrate project storage (two roots, both searched); a curve split in two pieces (q4); content-matched marker runs; an ambiguous `[]` |
| `*_rig21.json` | 21 qubits, a grid | 30 population-loss runs | a full grid (32 pairs), ledger staleness lines, a card with staleness warnings |
| `*_rig13.json` | 13 qubits | none | stored defects only: every curve `null` with its reason |

`data_*` is one operation: rig5q `qubit_pairs.q3-4.macros.cz_SNZ`, rig21
`qubit_pairs.qA2-qA1.macros.cz_SNZ`, rig13 `qubit_pairs.q1-2.macros.cz_SNZ`. The ledger was built inline
before the answers were taken, so `staleness` / `calibration` are in `ledger` mode. Paths of the rig
are written `<rig>/...`; chip and project names are written `device`. Nothing is trimmed (largest file
~150 KB).

Units everywhere: frequencies in Hz, times `t` as ISO-8601 UTC (`...Z`) and `t_us` as microseconds since
the epoch (UTC), losses as fractions (0.25 = 25 %), durations in ns. `null` always means "not known /
not applicable" and comes with a reason field where one exists -- never a zero.

## `/cz-plan/map` (unchanged fields omitted: `qubits[].f01_hz/defects/safe/loss_data/tier/direction`, `segments`, `planned`, `refused`, `missing`, `op`, `operations`, `unavailable`)

* `qubits[].grid_location` -- string|null: the qubit's stored `grid_location` ("col,row" as the chip
  spells it, pointers resolved); `null` when the chip has none. Parsing is the client's
  (`TopoGraph.layoutFor`).
* `pairs` -- object `{pair id: {...}}`, EVERY pair of the chip, with or without the drawn operation or
  any CZ macro:
  * `control_path`, `target_path` -- string|null: the members' holder paths (`qubits.q3`); `null` when
    the role does not resolve;
  * `control`, `target` -- string|null: the members' short names.

## `/cz-plan/loss`

File-derived; never waits for the ledger. Fetch once per page.

* `ok` -- true. `reason` -- string|null: `"no data folder is declared for this chip"` when there is no
  root at all.
* `roots` -- `[{path, source}]`: every data folder searched, strongest first (`source`: `declared`,
  `project_storage`, `project_roots`, `decided_same`).
* `runs_listed` -- int: population-loss run folders found under the roots; `runs_read` -- int: how many
  of the newest were read (cap `cap` = 60).
* `skipped` -- `[{run_id, name, reason}]`: runs read but not used (unfinished, still being written,
  unreadable / non-HDF5 `ds_fit.h5`, another chip's run, a renamed chip's run whose names cannot be told).
* `curves` -- `{qubit (today's name): entry}`, one entry per qubit of the chip:
  * `scan` -- curve|null: the NEWEST run with a usable curve for the qubit (draw it filled);
  * `marker` -- where the stored defect list (`extras.population_loss_defects_json`) came from:
    * `status` -- `match` (the newest run that measured the qubit -- in `ds_fit.h5` and in
      `fit_results` -- whose defect frequencies equal the stored list, 1 Hz, order-free), `ambiguous`
      (the stored list is `[]` and several runs match: the newest is named), `no_match` (no run in the
      data folders has that list), `none` (no stored list), `unreadable` (the stored list is not JSON);
    * `run_id` -- int|null, `run_name` -- string (only on `match` / `ambiguous`), `matches` -- int (how
      many read runs match), `same_as_scan` -- bool (only on `match` / `ambiguous`);
    * `label` -- string, ready to show: `"stored defects match run #1390"`, `"ambiguous (empty list
      matches 3 runs); newest #1905"`, `"source of the stored defects not in the data folder"`,
      `"no stored defects"`.
  * `marker_curve` -- curve|null: the marker run's curve when it is not the scan (draw it as a thin
    outline);
  * `reason` -- string|null: why `scan` is null (`"run #N has no frequency axis for q"`, `"no
    population-loss run of q among the N newest read"`, `"no population-loss run in the chip's data
    folders"`, the no-roots sentence).
* a **curve**:
  * `run_id` int, `run_name` string (the run folder's name), `folder` string (absolute path);
  * `t` string|null, `t_us` int|null: the run's instant; `time_quality` string (`offset` = the run's
    own time carries an offset; anything else = assumed); `order_proven` bool (= `time_quality ==
    "offset"`);
  * `interaction_time_ns` number|null: the scan's flux excursion duration T (`null` -> "scan duration
    unknown" is in `notes`); T differs per run (100 or 1000 ns on the real data) -- show it in every label;
  * `run_qubit` string: the qubit's name IN THE RUN (differs from the key after a rename);
  * `flux_conversion` string|null: what placed the run's axis (`model` / `quad`);
  * `n_samples` int, `dropped` int (samples with a non-finite frequency or loss, left out);
  * `range_hz` [min, max]|null, `median_step_hz` number|null (over non-zero steps);
  * `loss_min`, `loss_max` -- number|null: the TRUE extremes (losses are not clipped; real data spans
    -0.11..1.86 -- the client clips at 0 and at the scale edge with a tick);
  * `idle_at_run_hz` number|null: the frequency at detuning 0 = the f_01 the lab used; `idle_now_hz`
    today's f_01; `idle_moved_hz` = now - then; `idle_note` string|null: `"idle then 4.8450 GHz, now
    5.0000 GHz (+155.0 MHz)"` when |moved| > 1 MHz;
  * `defects` -- `[{frequency_hz, fwhm_hz, peak_loss, detuning_hz, strong, edge_truncated}]`|null: the
    run's own `fit_results[q].defects` (null when the run has no fit result for the qubit);
  * `notes` -- [string]: `"order not proven ..."`, `"scan duration unknown"`, the idle note;
  * `pieces` -- `[{n_raw, frequency_hz[], loss[], detuning_hz[]|null, stderr[]|null}]`: the curve split
    at every direction change of the frequency axis (a zero step is not one) and at every jump > 3x the
    median step, each in SAMPLE order (draw and fill each piece on its own, never join two); at most
    400 points per piece (peak-preserving decimation; `n_raw` = samples before it); frequency rounded
    to 1 kHz, loss / stderr to 1e-4; array entries may be `null`.
* `staleness` -- the ledger's word on the curves' flux model (ask again while `mode` is `building` or
  `preparing`; the curves never wait for it):
  * `mode` -- `ledger` (answered), `building` / `preparing` (ask again), `unavailable` (no ledger;
    `note` says why), `none` (no curve to check);
  * `note` -- string|null;
  * `qubits` -- `{qubit: [{which, run_id, text, fields[], changed_run_id, changed_t}]}`: one line per
    curve (`which`: `scan` or `marker_curve`) whose qubit's `lffem_flux_model` fields,
    `freq_vs_flux_01_quad_term` or park offset (`z.<point>_offset`) changed after the run; `text` is
    ready to show (`"axis of run #824 predates 1 flux-model input changed (run #3450)"`); `fields` are
    relative to the qubit (`z.joint_offset`). f_01 is not checked here (the idle check covers it).

## `/cz-plan/data` (new fields; `row`, `qubits`, `trajectory`, `pair_ops`, `amax_v` unchanged)

* `notes` [string] (warnings) and `info` [string] (how a figure was drawn) as before, plus the
  aligned `notes_figure` / `info_figure` [string]: the figure each one explains --
  `status` (the chip's unreadable entries: show on the status line), `both` (routing, pair refusal,
  unreadable amplitude, coupler shift), `time` (the frequency-in-time figure: waveform not drawn,
  nominal waveform, transcription). The client tags its own summary notes as `flux`.
* `unavailable` -- [string]: the chip's unreadable entries (the same texts as the `status` notes).
* `calibration.warning_count` -- int: the card's staleness count (`len(warnings)`);
  `calibration.warning_groups` -- `[{qubit, runs: [{run_id, t, fields[]}]}]`: the warnings per member
  (operation order) and per run, newest run first; `fields` relative to the qubit.
* `gate_loss` -- one entry per member, in `row.lines` order:
  * `role`, `qubit`, `moves` bool, `operating_hz` number|null;
  * `reason` -- string|null: why there is no loss reading (`"stays at idle"`, a refusal, no
    operating frequency); then `scan` and `sweep` are null;
  * `marker` -- the qubit's `marker` from `/cz-plan/loss` (null without a loss entry);
  * `scan` -- `{run_id, interaction_time_ns, loss, pieces: [{piece, loss, reason}], reason}`: the scan's
    loss at the operating frequency [derived]: linear between the two bracketing samples of ONE piece,
    only when they are at most 2 median steps apart; `loss` = the largest over the pieces that hold the
    frequency; `null` with `reason` `"not measured at this frequency"` (plus the gap size) otherwise;
    every piece whose span holds the frequency is listed. It is the scan's dwell loss (T ns excursion
    from idle plus every defect crossed on the way, with the scan's ramp), not a gate error;
  * `sweep` -- `{f_min_hz, f_max_hz, source, label, note}`|null: the member's frequency excursion
    during the gate [derived]: min / max of its own f(t) over the operation's waveform (SNZ lobes
    included) together with its idle f_01 (`source` `trajectory`), else SM's f(amplitude) over [0, A]
    (`source` `amplitude only`, `label` `"from the amplitude only"`); `note`: the stored defects sit
    on the run-time axis, the trajectory on today's conversion;
  * `defects_touched` -- `[{frequency_hz, fwhm_hz, peak_loss, strong, inside, distance_hz}]`: the
    STORED defects whose centre lies within FWHM/2 of `[f_min_hz, f_max_hz]` [derived]; `inside` =
    the centre itself is in the interval.
