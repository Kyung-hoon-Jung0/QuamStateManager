# 202 — A rebuild reported what it lost, never what replaced it

Date: 2026-09-18. Found by executing the one action the stress round had never
pressed on the customer chip: a **real Re-generate build** of
`260907_KRS_5Q`, in the environment that lab actually runs (`KRISS_CZ`), writing
to a scratch folder. The chip's own hash was `0c9a78e48405` before and after
every build in this round.

---

## 1. What the build did

```
reconstruct : qubits=5 pairs=4 lines=16 quam_class=quam_config.my_quam.Quam
build       : 17.4 s   ok: True
merge       : carried 737 · grafted 1207 · kept_new_pointer 260
              dangling_grafts 0 · schema_dropped 50 · residual_lost 10
```

docs/176's root-class fix is working — the reconstruction carries the lab's own
`quam_config.my_quam.Quam` rather than a stock root. docs/118's case is clean:
**all 1,954 pair leaves kept identically**, all 11 headline calibrated values
kept, 0 leaves added.

`schema_dropped: 50` is the number worth pulling on. The report NAMES those
paths, and they are readout weights:

```
qubits.q1.resonator.operations.readout.ringdown_length
qubits.q1.resonator.operations.readout.weights_imag
qubits.q1.resonator.operations.readout.weights_real
```

## 2. The cause was reported nowhere

The source chip's readout pulse is `quam_config.complex_weights_pulse.`
`ComplexWeightsReadoutPulse` — a class the **lab** wrote, declaring
`weights_real` / `weights_imag` / `ringdown_length`. The rebuild produces
`quam.components.pulses.SquareReadoutPulse`, stock quam. The lab's fields are
not fields of the stock class, so the merge's cross-generation schema gate
(docs/51) drops them — correctly, because grafting them would poison
`Quam.load()`.

So the **consequence** was reported (50 dropped paths, each named) and the
**cause** was not. A reader saw a list of `weights_*` keys and had to infer
"my readout class was replaced" from it. That inference is the only part they
can act on.

The gap is structural, not an oversight in one function: **the build spec has
no slot for a per-pulse class**. It carries the chip's root class and nothing
below it, so `reconstruct_spec` *cannot* carry `ComplexWeightsReadoutPulse` and
the builder writes whatever its own templates write.

### The fix

`regen_merge` records the substitution where it happens — the one place that
already decides `__class__` comes from the NEW build:

```python
if (k == "__class__" and isinstance(nv, str)
        and isinstance(old.get(k), str) and old[k] != nv):
    stats.class_changed.append((path or "(root)", old[k], nv))
```

`__class__` and `__package_versions__` share that branch and the comment there
called both "serialization artifacts". That is true of the version stamp and
false of `__class__`, which says what the object IS; the comment now says so.

`regenerate.py` ships `class_changed` / `class_changed_paths` /
`class_changed_total` beside the existing `schema_dropped` block, and
`generate.js` renders an amber `10 class substitutions` chip plus one line per
**substitution** — grouped, because one class swap normally appears at every
qubit and a per-path list buries the single fact that matters.

## 3. What the panel says now, on the real chip

```
[chip] 737 carried · 1207 grafted · 10 not carried · 50 cross-gen dropped
       10 class substitutions
rebuilt as quam.components.pulses.SquareReadoutPulse
   (was quam_config.complex_weights_pulse.ComplexWeightsReadoutPulse)
   — 5 places: qubits.q1.resonator.operations.readout, …q2…, …q3…, …
rebuilt as quam.components.pulses.SquareReadoutPulse
   (was quam_config.gef_weights_pulse.GefWeightsReadoutPulse)
   — 5 places: qubits.q1.resonator.operations.readout_GEF, …
```

**Two** lab classes, not one — my first reading of the chip found only
`ComplexWeightsReadoutPulse`, and the report found `GefWeightsReadoutPulse`
beside it. Re-running the merge with the env's real class schemas reproduces
the build's own `schema_dropped = 50` exactly, and **50 of 50** dropped paths
sit on one of the 10 re-typed objects: the class lines explain the whole drop.

### The practical consequence for this lab

A re-generate today loses the optimized integration weights for all five
qubits — 300 / 500 / 400 / 1200 / 1250 elements — and readout optimization
would have to be re-run. The source chip keeps them; only the rebuilt folder
is affected, and the panel now says why.

## 4. The pre-build warning that the chip refuted

The same fact is knowable before a 17 s build, and docs/176 ③ is the precedent
(a root refusal belongs in the review, not in the build's 400 half a minute
later). So a reconstruct-time note was written: enumerate the chip's pulses via
`pulse_index.list_pulses` (reusing the enumeration SM already owns rather than
writing a second one), and warn for every class outside `quam` / `quam_builder`.

Measured on the real chip, it fires on **four** classes — and it is **wrong for
three of them**:

| class | places | what the real rebuild did |
|---|---|---|
| `ComplexWeightsReadoutPulse` | 5 | re-typed to stock, fields dropped |
| `GefWeightsReadoutPulse` | 5 | re-typed to stock, fields dropped |
| `SNZTwoFluxPulse` | 16 | **byte-identical** — class and all 11 keys kept |
| `GaussianNZTwoFluxPulse` | 4 | **byte-identical** — class and all 13 keys kept |

The builder writes `resonator.operations.readout`, so it overwrites that object.
It writes no `cz_SNZ_flux_pulse_*` operation at all, so tier-2 grafts those
whole and the lab's class survives. A source-side warning cannot tell the two
apart without building, so it told this lab it would lose 20 pulses it will
not lose.

**Reverted.** The post-build report is a MEASUREMENT of the finished rebuild and
is therefore always right; the pre-build note was a prediction and was mostly
wrong. Recorded rather than dropped, because "tell the user earlier" is a good
instinct that was refuted here by the only thing that could refute it — the
real chip — and the distinction is now a pin
(`test_a_pulse_the_rebuild_never_wrote_is_not_a_substitution`).

## 5. Still open

The report names the substitution; it does not prevent it. The real fix is the
docs/176 pattern one level down — the spec carrying a per-pulse class the way
it already carries the root class — and it is a change to the build spec's
vocabulary and to `run_build`'s construction path, needing a real build per
class and env-importability handling (`cqt` cannot import
`quam_config.two_flux_gate`; `KRISS_CZ` can). Not attempted here.

Note what the fix would NOT be: keeping the OLD class on the merged object.
docs/136 established that poison — grafting an old `QdacBiasLine` onto a
rebuilt qubit produces a state whose field is typed for something the build
never wrote, and `Quam.load()` dies on the first one with the build reporting
success.

---

## Pins

- `tests/test_regen_merge.py` — 6 tests: the substitution recorded; every
  dropped field sitting on a named object; an unchanged class silent; the
  version stamp not a class (both spellings — see below); the root named
  `(root)`; a grafted subtree never called a substitution
- `tests/test_regenerate.py` — the report block's shape, and an ordinary
  rebuild reporting none
- `tests/generate_classchange_selfcheck.cjs` — 13 assertions EXECUTING the
  shipped renderer: the chip, the grouping, both class names, the place count,
  the capped-list total, and silence on a result predating the field

**Mutation sweeps: 8/8 server-side, 6/6 client-side** — after a first sweep of
7 caught only 6.

## 6. The vacuous pin the sweep caught

`test_a_version_stamp_move_is_not_a_class_substitution` used the real shape,
`__package_versions__` as a dict, and passed **with the `k == "__class__"`
guard deleted**: `isinstance(nv, str)` already excludes a dict, so the fixture
could not reach the state the guard protects (docs/141 §4af). A scan of all 46
chips on this machine confirms that key is a dict at the root every time.

Both spellings are pinned now — the dict for what real data does, a
deliberately hostile string for what the guard is for.

## Measurement errors this round (running tally: 11)

- **the heredoc backslash trap, a fourth time** — `\\n` inside a quoted heredoc
  produced a literal newline and a `SyntaxError`. The memory note
  `bash-heredoc-backslash-trap` says to use the Write tool for backslash
  content; the Edit tool fixed it in one pass.
- **a mutation aimed at the wrong branch** — the first "a graft is also called a
  substitution" mutation edited the NEW-only loop, while a graft comes from the
  `old.items()` loop, so it went GREEN against a fixture that was fine. The pin
  was sound and the mutation was not; re-aimed, it reds.
- **a malformed fixture read as a finding** — `{"qubits": {q: …} for q in qs}`
  written with the comprehension around the OUTER dict left only `q3`, and the
  test failed `3 == 9` looking like a merge bug. `carried=1` in the failure
  output was what named it.

---

## Appendix — six pins this session's own pushes turned red

The full suite for this commit failed 27 tests. Re-run in isolation, 25 failed
deterministically (2 were flakes). Run at `8e483f6` — the commit before this
session's first push — **19 of those 25 fail identically**: the docs/87 OS
class, the QDAC live builds, the replay benchmarks, `test_knowledge_pack`'s
name scan, `test_runner_p7`. **Six pass there and fail at HEAD**, so they came
from docs/195 and docs/196, pushed earlier in this session without a full
suite run. That is the process failure this appendix records.

Each was measured, not inferred (docs/155 §10a):

| pin | what happened | the rule |
|---|---|---|
| auto-sync latch | a 2,600-char window; docs/195 put the per-cell `dom_path` collection between the guard and the call | holds — the pull is gated on `!_applyInFlight`, takes it, releases and drains |
| in-lock dirty re-check | a 4,600-char slice; the lock moved to offset 4,758 | holds — the re-check is the first statement inside the lock |
| reapply stash cleared | "sliced generously" at 9,000 chars; the function is 13,227 now | holds — `_clear_reapply` pairs the rebuild |
| declined pull | **the contract changed**: docs/195 cannot know "same field" without the live content the cheap drift poll never reads | measured: advertises **once**, the pull declines and records the conflict, then quiet over every later poll |
| `_iso` parity | the grammar now runs in `SnapTime.parse` (app.js); `return null` sat past a 300-char window | holds — the same anchored 8+6 regex, the same refusal |
| Trends option order | a page-wide `<option value="…_…">` scrape matched the Settings zone picker's `America/New_York` | holds — the picker has no `name` and sits in no form |

No product regression among them. One suspicion was checked and cleared on
the way: the zone picker's inline `onchange=` runs under the app's CSP, whose
`script-src` carries `'unsafe-inline'` (docs/120's dead handlers needed
`unsafe-eval`, a different thing).

Every pin is re-anchored on structure rather than a byte count — the guard
that opens a block, a function bounded by the next `@bp.route`, a JS function
bounded by its own closing brace — and the declined-pull pin now asserts the
contract docs/195 actually ships (at most one press in six polls, 204 on the
conflict, the user's value intact).

### The sweep found one more

8 mutations, first pass **7 of 8**: deleting the FIRST in-lock re-check left
`test_the_dirty_check_is_repeated_inside_the_build_lock` green, because a
second guard after `sync_from_live` still rescues the edit (re-persists memory,
answers 204). The structural pin could not tell one re-check from two.

What only the first guard does is refuse BEFORE the I/O — no live read, no
working-folder rewrite, no "backup" snapshot for an edit nobody was going to
discard. `test_an_edit_in_the_lock_window_is_refused_before_any_live_io`
reaches that window for real: the build lock is wrapped so a user's edit lands
while it is being taken, and `sync_from_live` is spied. With the first guard
deleted it goes red. **8 of 8.**

After the re-pins, the 27 ids re-run at HEAD fail **19 — exactly the base's
19, test for test**.

---

## 7. The Compare hub — the last unpressed menu

Pressed on the customer chip in real Chrome (`h1`–`h7` in the scratchpad), every
control by hand:

| control | result |
|---|---|
| **+ Current chip** ×2, **live** | 3 sources; "Add at least one more source." at 1; the context prompt at 2+ |
| bucket ① / ③ over working ×2 + live | `✓ Identical — 30,222 leaves equal` (the chip is clean — correct) |
| bucket ② with 3 sources | refused by name: "② Same design compares exactly two sources — remove extras or switch to ① / ③." |
| Exact / Wide / Lab default | the URL carries each; the result re-renders |
| ★ on row 2 | `ref=1`, the star moves |
| × on row 1 | 3 → 2 rows, and **the ★ followed its source** (`ref` 1 → 0) — `removeAt`'s promise, verified |
| History… → KRISS_CZ | the "which state?" popover lists 14 snapshots; two clicks add two `hist:` sources |
| bucket ① over two snapshots | `8 changed · 0 within tolerance · 31,064 equal`, e.g. `q1.anharmonicity` 207 → 206 MHz |
| hostile `bucket=7/-1/abc` | falls back to the context prompt |
| `preset=zzz`, `ref=99`, `ref=-5` | defaulted / clamped |
| `src=ws:<script>…` | escaped, rendered as a ✕ unreadable row, no dialog |
| a nonexistent source | a ✕ row carrying its own error text, excluded from the comparison |

Zero console complaints across all of it.

### Two things it threw away without a word

**① A URL with more than eight sources.** The route slices `src=` to the pool
cap (`_HUB_MAX_SOURCES = 8`) — correct — but the notice that says so fired only
on `trunc=`, a parameter the retired sidebar redirect used to send. A pasted,
bookmarked or hand-edited twenty-source URL compared eight as though they were
everything. The route already holds the full list, so it counts it now
(`trunc_total = max(trunc, len(all_refs))`), and the sentence counts the rows
ON SCREEN (`basket|length`), not `sources_count` — which counts only the
readable ones and would say "the first 7" beside eight rows when one is ✕.
The client already refused a ninth ADD with a toast; this is the URL door.

**② An unreadable `map=`.** `map=zz:yy` (parses, names the wrong qubits) said
"matches neither device — showing the suggestion instead". `map=::,,` (does not
parse) said nothing, for the same outcome. `_hub_validated_map` now names it
("could not be read"), and the saved-mapping fallback's sentence — which said
"matches neither device" for BOTH — says "could not be used".

Neither produced a wrong number: the comparison over the kept eight is right,
and a dropped map falls back to the suggestion panel rather than a confident
empty result. What was wrong is that the page did not say what it had dropped,
beside neighbours that did — the docs/94 silent-cap class.

Pinned by `TestTheHubSaysWhatItDropped` (6) in `tests/test_compare_hub_routes.py`;
**mutation sweep 6/6**, including both over-warning directions (an absent map
called unreadable; the notice at eight or fewer). Real Chrome: `Showing the
first 8 of 20 selections — the basket holds at most 8 sources.` above the
results; `could not be read` on `::,,`; `matches neither device` on `zz:yy`.

### Measurement errors this round (running tally: 14)

- **the setup section collapses when a context is chosen** (by design — the
  results take the page), so my ★ and × presses landed on zero-size buttons
  and read as "the star does nothing". Re-driven with the section open, both
  work.
- **`options()` returns `[value, text, disabled]`** and I passed the whole list
  as the value, so the History select landed on nothing and the popover read
  as "empty".
- **a long query opened at Tab construction** rendered one row and no notice;
  the same URL through `navigate()` renders eight rows and the notice, stable
  over eight seconds. The server's own answer was checked first and was right.

---

## 8. A customer's name in shipped code — a red pin nobody had read

`test_knowledge_pack::test_shipped_code_carries_no_lab_name` was among the 19
failures the pre-session base shares, and "pre-existing" is where triage
usually stops. This one is a **confidentiality** pin (docs/138: shipped code
carries no lab name, because SM ships to other labs), so it was read: six
comments naming one customer's chip, `KRISS_CZ`, in `pulse_catalog.py` ×2,
`waveform_synth.py`, `routes.py` ×2 and `pulses.js` — written during the
docs/189–190 Pulses round on that chip.

A seventh sat where the pin never looked: a Jinja comment in
`_pulse_detail.html`. `{# … #}` never reaches the browser, but the template
ships in the installed package like any other source file.

All seven reworded to "one customer chip" / "a lab's own CZ pulse class" —
comments only, no behaviour. The class names (`SNZTwoFluxPulse`, …) stay: they
are technical terms for a published CZ technique, and the pin's `NAMES` list is
lab names.

The pin now scans `web/templates/*.html` and `web/static/*.css` beside `.py`
and `.js`. Both new scopes mutation-checked: a lab name put back into the
template comment reds it, and so does one appended to `style.css`.

---

## 9. Two safety properties that had never been checked on Windows — and one real race behind them

Of the 19 base failures, six guard state safety: `test_state_coherence` ×4
(a dirty chip is never evicted; the LRU is true LRU; a run archive opens
read-only and is never downgraded) and `test_web`'s
`TestPhase4QuamCacheConcurrency` + `TestDatasetSelectionFix`. None was a
product failure. Each was a PIN that could not run here, and that is worse
than it sounds:

- **The five cache lookups used `str(path)`.** `_activate_quam` keys the cache
  by `path_match.fs_key`, which case-normalizes on Windows, so a mixed-case
  `str(path)` is never a key. Four pins died at the lookup, BEFORE their
  assertion — the properties they name had never been checked on this OS. And
  `assert str(chips[1]) not in cache`, the LRU pin's "evicted instead" half,
  **could not fail at all**: a key that is never present is always absent.
  They look up `fs_key` now. Every property holds, and a sweep proves each
  pin can fail: eviction taking a dirty chip, a re-access not refreshing LRU
  order, a run archive opening as live — 3 of 3.
- **The downgrade guard was unreachable by its own pin.** The fourth mutation
  (delete "NEVER downgrade a read-only archive") stayed green: the fixture IS
  a recognisable run archive, so `_is_run_archive` re-labels the `/load` before
  the guard runs. Every production caller passes `<run>/quam_state`, so today
  the classifier always wins and the guard is defense in depth — but it states
  its own contract, so `TestTheDowngradeGuardOnItsOwn` opens a folder
  explicitly as an archive that the classifier does not recognise. Red on the
  mutation now.
- **`TestDatasetSelectionFix` was stale on every OS**, not an OS-class failure.
  It asserted the folder PATH in `data-folder`; the route sends `folder_sig`, a
  signature of the active folder set. Checked before calling it stale: the
  consumer (`dataset-virtual.js`) compares the attribute against what IT
  stored last time, never against a path, so the feature works. Re-pinned on
  that contract — the same folder gives the same value, a different folder a
  different one.
- **`TestPhase4QuamCacheConcurrency` asserted a tautology.** `sum(k == key) ==
  1` over a dict's keys cannot be 2. What a race can break is WHICH context a
  caller gets back.

### The race

Reading that last pin's target turned up a real one. `_activate_quam`'s slow
path is single-flight per folder (a build lock plus a cache re-check under
it). But the FAST path re-inserts an LRU-evicted entry *without* the build
lock — its own comment names the hazard ("two contexts on one working
folder"). If that re-insert lands between a slow-path build and its install,
the install saw the key present, refreshed LRU order, and published **its own**
context. **Reproduced by injecting that re-insert**: afterwards the registry's
active context and the cache's entry were two objects with two stores for one
working folder — an edit made through the active context vanishes on the next
open (a cache hit serves the other store), and the uncached context escapes
the dirty-pin that protects unsaved edits from eviction.

Narrow — it needs an eviction (ten chips cached) and a concurrent re-open of
the same chip — but it is exactly the state the fast path's comment exists to
prevent. The install now applies that comment's own rule: the cache's current
entry wins (`ctx = _quam_cache[key]`).

`TestOneContextPerFolder` reproduces the window deterministically. The
concurrency pin now asserts IDENTITY — all eight callers hold the cache's
object, which is also the registry's — and a start barrier plus a slowed build
makes the threads actually contend: unslowed, threads 2–8 took the fast path
and the pin stayed green with single-flight AND adoption both deleted. Sweep:
reverting the fix reds the deterministic pin; removing single-flight AND the
adoption reds both (8 builds, 8 distinct contexts). Removing only
single-flight leaves one context (8 builds, 1 distinct): the two mechanisms
now back each other up.

Measurement error, recorded: the first "remove single-flight" mutation indexed
an earlier occurrence of `build_lock = _get_quam_build_lock(key)` and disabled
the FAST path's lookup instead ("builds: 1" gave it away). Re-aimed on the slow
path's own comment.

---

## 10. The live pair read failed on a chip that was never missing

`test_safe_io::test_reader_survives_concurrent_writes` was in the base 19 and
failed deterministically in isolation. It states a promise docs/28 makes: a
writer looping `atomic_write_json` must never make `read_state_wiring` fail.
Measured on this machine, on two volumes:

| volume | reads in 1.5 s | read failures | kind |
|---|---|---|---|
| TEMP (`…\ESTsoft\CreatorTemp`, an AV vendor's dir) | 5–42 | 20–91 | `FileNotFoundError` |
| `D:\work` (where the customer's chips live) | ~1,700 | **36–50** | `FileNotFoundError` |

So not an antivirus artifact. And the numbers contradicted the retry ladder:
forty failures inside 1.5 s cannot each have slept the ladder's 0.9 s, so
they were raised WITHOUT retrying. The cause: `_pair_fingerprint` — the stat
bracket that detects a torn pair — does a bare `Path.stat()` on both files,
called outside both retry loops. `read_json_raw` beside it has always retried
a transient `FileNotFoundError` ("a read that lands in the brief window of an
external atomic replace"); the stat did not, and `ReplaceFileW` leaves the NAME
briefly absent. The docstring even said "raises OSError if a file is missing
(same as the caller's read)" — the same error, but not the same retry.

This is the pair read behind Sync, Apply-to-live and reconcile, on a bench
where qualibrate rewrites `state.json` every 30–60 s (docs/187). The window is
small and the writer in the test is a tight loop, so at the customer it is
rare — but it is a "state.json not found" on a chip that exists, and the pin
promising otherwise had been red on Windows the whole time.

`_pair_fingerprint_settled` rides it out with short sleeps (10/20/40/80 ms),
not the read ladder's 0.15 s steps: a replace window closes well inside the
first, and a folder that genuinely has no `state.json` (a wrong pick in the
browser) should not wait 0.9 s to say so — it adds at most 0.15 s there.
Re-measured: **0 failures in six runs on both volumes**, read throughput on
`D:` unchanged. Pinned: the existing concurrency test (green on Windows for
the first time), plus a deterministic stat miss that is ridden out and a
genuinely missing file that still raises within 0.6 s. Sweep 3/3: the bare
stat back; the settle on the long ladder; the settle never giving up.

### And the other filesystem flake, which was the fixture

`TestEnvDiscoveryCache::test_a_changed_inventory_invalidates_it` failed 2 runs
in 3 even in isolation. The env cache (docs/135) keys on each env's parent
directory `st_mtime_ns`. Measured: a child `mkdir` left the parent's mtime
unchanged **16 of 30** times at millisecond spacing on `D:` — NTFS coalesces a
directory's timestamp across modifications that close together — and **0 of
12** at ≥ 0.25 s spacing, on both volumes. A real conda env is created seconds
after the last scan, so the product sees it; the test created two directories
milliseconds apart. It now waits 0.3 s first: 10 of 10, and a key that ignores
the env directories still reds it.

---

## 11. Two notification events a user can enable that nothing ever sends

`test_runner_p7::test_only_four_events_exist` — a design pin (docs/78 D-9) whose
point is that "a notifier that fires on everything is one the user mutes, and
a muted notifier reads as coverage while delivering nothing" — had been red
since 2026-09-06, when docs/172 §1b deliberately added three agent events and
did not update it. Re-pinned on the seven, as an exact set, so every future
event is still a decision made there.

Checking that the three new ones actually fire found that two do not.
`agent_failure` is emitted from `agent_runs.py` and `agent_api.py`;
**`agent_apply_refused` and `agent_stalled` appear only in the vocabulary and
in `limits.DEFAULTS["notify_events"]`, where they are enabled by default** —
no composed name, no wrapper, no literal anywhere sends them. "Stalled" is a
real, specified state (docs/173 §3.1: not running AND 15 min with no event, or
the PID dead), but it is decided client-side by `agent-pill.js`, where no
notifier lives; the apply refusal never calls one.

Nobody receives them today — the webhook defaults to empty and the Limits UI
that would expose `notify_events` was deferred (docs/173 §S7) — so this is a
truthful-vocabulary gap, not an outage, and building a server-side stall
detector unasked would be scope nobody chose. What ships is the record, in the
form that cannot rot: `test_every_declared_event_has_an_emitter` is
`xfail(strict=True)`. It fails today for exactly the two names (verified with
`--runxfail`), its emitter scan finds the other five (so it is not vacuous),
and adding emitters for both turns it red — verified — so the xfail cannot
outlive the gap.
