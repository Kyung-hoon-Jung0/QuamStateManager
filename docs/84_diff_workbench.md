# 84 — the diff workbench

*Status: shipped 2026-08-07. Branch `feat/diff-workbench`.
Amends docs/49 (Compare hub) and docs/76 (Δ everywhere).*

## The report

> 현재 state에서 compare selected 자체는 아주 working을 잘하거든? 근데….
> 많은 사용자들이 실제 사용을 안해. … 메뉴가 직관적이지 못하다고 하네?
> 사람들은 단순해서 그냥 vscode처럼 compared selected를 하면 **차이점**만
> 보여주고 **얼마나 차이나는지만** 쭉 나열하면 된다고 생각해.

The Compare hub is correct and nobody uses it. Two reasons, both structural:

* **Three front doors.** "Compare selected" existed on Datasets, Param History
  and State History, and each led somewhere different.
* **It asks before it answers.** The hub wants a declared comparison context,
  a tolerance preset, and sometimes an entity mapping — before showing a single
  row.

## What was already there

`renderJsonTree` — the Explorer's tree — has had a diff overlay all along; it
is what "Live diff" uses. It already carried: differing-node marking, an
`old → new` inline row, a delta, auto-expansion to the differences, lazy
children (a 15,000-leaf document is cheap), search and depth controls.

So the answer to "can we reuse the JSON tree?" was: it is 70 % built. Two
things it genuinely could not do:

**It could not show added or removed.** The top-level loop and the lazy child
builder both iterated the PRIMARY document's keys, and the diff mark was gated
on `refValue !== undefined`. A key only one side had rendered as nothing at
all — the single most characteristic thing an IDE diff shows.

**Its delta was its own.** `toFixed(6)` / `toExponential(3)`, so the same
change read `(+0.000123)` in the tree and `+100,000,000 (+1.96%)` in the Review
tray — a straight docs/76 violation ("ONE implementation").

## The measurements that shaped it

Real snapshot pairs, server-side:

| pair | leaves | changed | tree nodes to render | build |
|---|---|---|---|---|
| neighbours (LabA) | 15,285 | 1 | **4 (0.0 %)** | 24 ms |
| 30 apart | 12,665 | 2,758 (117 numeric) | 3,249 (26 %) | 28 ms |
| first vs last (LabB) | 1,657 | 690 (138 numeric) | 876 (53 %) | 5 ms |

Two consequences:

1. **"Differences only" is a pruned document, not a filter.** Shipping two full
   states is 1.7 MB; shipping the changed leaves plus their ancestor chains is
   0.5 KB for the neighbouring case. The client then renders those with the
   ordinary tree and inherits everything it can do.
2. **A naive "list every difference" would be a wall.** The 30-apart pair has
   2,758 differences of which only 117 are numeric — the rest are keys a
   regenerate added or removed. That is exactly how the old surface became
   unusable, so rows carry their change class and numeric moves rank first.

## The surface

```
/diff?a=<ref>&b=<ref>&tab=state|wiring|node|data&view=tree|list
```

* **Sources** are the hub's own ref tokens (`ws:` `run:` `hist:` `drop:`
  `working:`) resolved through `compare_sources.resolve_source`, so every
  source the hub understands works here unchanged.
* **A bare `/diff` needs no picking**: it opens the newest snapshot against the
  loaded chip — *what have I changed?*
* **Tree** is the default (nested, with the ancestor context); **List** is the
  same rows flat and ranked by |Δ%|, which is the "얼마나 차이나는지만 쭉 나열"
  half of the ask. The list pages at 300 rows — 2,257 ranked rows serialise to
  1.2 MB.
* **Four tabs.** `state.json` and `wiring.json` are the pooled documents;
  `node.json` is the run's own file (snapshots say so instead of showing an
  empty tab); `data` is a variable INVENTORY diff — files, variables, dims and
  shapes. A byte-level diff of HDF5 arrays is not a meaningful thing to render,
  and pretending otherwise would be the dishonest option.

Verified on two real runs of the same node type: the node tab reports
`artificial_detuning_in_mhz`, `num_shots`, `amp_max`, `amp_min` — literally
what was asked differently — and the data tab reports the variables whose
shapes differ. state/wiring/node render in 2–39 ms, the h5-inventory tab in
84 ms.

## One front door

All three "Compare selected" buttons now open the same surface, through two
tiny routes that resolve identity server-side rather than making each page
carry it in the DOM:

| from | route | lands on |
|---|---|---|
| Param History, State History | `/diff/snapshots?ts_a=&ts_b=` | state tab, oldest side on the left |
| Datasets (exactly two runs) | `/diff/runs?uids=` | node tab |

The sidebar's **Compare** opens `/diff`. The hub is one click deeper behind
**Advanced…** — it keeps the hard cases (entity mapping across different
devices, N-way baskets, tolerance presets), and every legacy URL still
redirects into it: a POST from the old form, and any GET carrying the hub's own
query grammar (`src=` and friends).

## Ripples

* `renderJsonTree` gained `options.union`. It is **opt-in**: the live-diff
  overlay is a before→after of one document and its behaviour is byte-identical
  (pinned).
* The primary document is the BEFORE side and `refData` the AFTER side in both
  modes, so every row reads `A → B` and the delta needs no per-mode orientation.
* A diff row is **read-only**: the two sides come from arbitrary sources and one
  of them usually is not the loaded chip, so a click copies the value rather
  than opening an editor.

## A bug this found

Profiling a `/diff` render showed **906 ms of a 937 ms render was
`time.sleep`** — `_ctx()` → `_type_alarm_payload` → `_load_chip_prompt_memo`
reading an optional sidecar through `safe_io.read_json`'s 4-attempt / 0.9 s
retry ladder. Until a user declines a prompt that file does not exist, so a
fresh install paid it on **every page**. Guarded: 906 ms → 2 ms.

## Pins

`tests/test_diff_workbench.py` (32) — the engine (flatten grammar, the three
change classes, house-arithmetic deltas, ranking, pruning fidelity including
list indices) and the surface (tabs, views, the bare-`/diff` default, honest
"not an experiment run", bad refs never 500ing, the three entry points, and the
hub still reachable). `tests/diff_tree_selfcheck.cjs` (23) — union rendering,
removed subtrees expanding, ValueDelta as the only delta, read-only clicks, and
the live-diff / plain-Explorer regression pins.

## Known limits

* Comparison is exact — no tolerance preset. The hub keeps those.
* `data` compares inventories, not array contents.
* Cross-device comparison still needs the hub's entity mapping; the diff shows
  such a pair honestly (everything added and removed) rather than mapping it.
