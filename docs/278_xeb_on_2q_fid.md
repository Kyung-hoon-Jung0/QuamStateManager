# docs/278 -- An XEB-only CZ gate is visible on 2Q Fid., and a flat fidelity block is read as one measurement

2026-10-04, on-site report: "2Q fidelity does not show SNZ" (Chip Status > 2Q Fid.).

## What was wrong

The device's state stores the CZ gate `cz_SNZ`'s XEB result as FLAT sibling
keys, not a nested dict like the RB results of the other gates:

```
macros.cz_unipolar.fidelity = {"Bell_State": {...}, "StandardRB": {...}}
macros.cz_SNZ.fidelity      = {"XEB": 0.9889, "XEB_err": 0.0012, "XEB_run": 1782,
                               "XEB_runs": [1782, 1783], "XEB_updated_at": "2026-...+09:00"}
```

Three defects, measured on a read-only copy of the device's live state:

| # | Defect | Measured |
|---|---|---|
| 1 | The 2Q Fid. panel builder drew Standard RB and Interleaved RB only. A gate whose only figure is XEB had no panel, so `cz_SNZ` vanished. | 7 pairs with XEB, 0 panels |
| 2 | `_extract_pair_gate_fidelities` read the block key by key: `XEB_err` (an uncertainty) and `XEB_run` (a run number) became fidelity rows of their own. | 14 bogus rows on the device (e.g. `XEB_run = 1768` as a "fidelity") |
| 3 | The XEB row's provenance was the gate's RB run id (`StandardRB_load_id`) when the same gate had one, not its own XEB run. | `qB3-qB1` XEB row pointed at RB run #331 instead of XEB run #1772 |

And on Trends, `…fidelity.XEB_run` was classified as a 2Q measurement (a
run number charted as a series).

## The fix

- `core/query.py` `_flat_metric_attrs`: a key `<M>_<suffix>` is an ATTRIBUTE
  of the scalar metric `<M>` beside it (never a metric) when `<M>` is a numeric
  key of the same block and the suffix is one of `err/stderr/std/uncertainty/
  sigma` -> `err`, `run/run_id/load_id` -> `load_id`, `runs` -> `runs`,
  `updated_at` -> `updated_at`. Anything else keeps its old per-key reading
  (a scalar IRB's `InterleavedRB_alpha` is still its decay row; a dict block's
  `StandardRB_load_id` is still skipped and still names the RB rows' run).
- `fidelity_field_kind`: `run`, `runs`, `*_run`, `*_runs` are provenance
  (`load_id`), the same family as `*_id` (docs/138). Trends no longer offers
  a run number as a 2Q measurement.
- `chip-status.js` `build2QRBPanels`: an `XEB` panel type after Standard RB
  and Interleaved RB. Its heading says **"as stored"**: the chip records the
  number but not which XEB figure it is (per cycle or per gate, average or
  Pauli), so SM does not give it an RB kind it may not have, and does not judge
  it against the CZ-fidelity spec threshold (outliers by MAD only). A cell's
  tooltip carries the stored uncertainty and the run (`98.89% ± 0.12% as
  stored · run #1782`). The section sub-heading names what is shown
  (`2Q Gate Fidelity — RB · XEB`); the "no 2Q values" note now also waits for
  XEB.

## Verification

- Device copy (read-only copy of the live state, network made unreachable):
  7 XEB rows, each with err / own run / runs / updated_at; 0 bogus rows
  (main: 14).
- Real Chrome (headless, port 5147): 2Q Fid. shows `XEB as stored` > `SNZ`,
  7/32 pairs, tooltips with ± and run; reload keeps it; console errors 0.
- Pins: `tests/test_xeb_fidelity.py` (6) + `tests/chip_status_xeb_panel_selfcheck.cjs`
  (9 assertions). Mutation check 9/9 RED.

## Note for the device's site

The installed SM there predates this fix: it needs `pip install -U` from main
and a restart of `qsm serve` to show the panel.
