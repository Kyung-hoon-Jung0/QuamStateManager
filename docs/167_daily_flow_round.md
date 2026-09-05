# docs/167 — seven features a user asked for, one of which the measurement killed

2026-09-04. A round that began by asking what a *user* of this app would want
next, and ended with a physics check whose first design was wrong, a
notification feature that was mostly a deletion, and a value lock that was
deferred on purpose.

Every feature was planned, then adversarially red-teamed against the code
before anything was written. The red team killed one feature outright, reduced
four, and found a badge-inflation bug in the one that had already been
approved. Its findings are recorded here next to the things it got wrong, and
next to the things **my own measurement** contradicted afterwards.

---

## 0. The method, and why it mattered here

Eight plans, each required to cite `path:line` for every factual claim. Then
four adversarial reviewers, each holding two related plans, whose brief was:

> Extract every load-bearing factual claim and re-verify it by reading the
> code. Report it CONFIRMED, WRONG or UNVERIFIABLE. A plan built on a wrong
> claim is a wrong plan no matter how good the design reads. Pay special
> attention to claims of ABSENCE — "there is no X" is the easiest thing to get
> wrong and the most damaging, because it leads to building a duplicate.

Of 119 audited claims, 6 came back WRONG or UNVERIFIABLE. Three CRITICALs and
about a dozen MAJORs followed, and the verdicts moved: one `defer`, four
`ship_reduced`.

**And the review was not the last word.** The physics plan could not measure
the real chip (no `state.json` in the worktree — it said so, in its own
open-questions list), so it carried the brief's design forward. When I measured,
that design turned out to fire fourteen times on a healthy chip. §1 is what
happened next.

---

## 1. A Physics domain in Diagnostics — and the criterion that had to change

**The finding.** On the customer's 20-qubit chip, Diagnostics said:

> ✓ No structural issues found

while `state.json` held `q5: T1 = 19.99 µs, T2ramsey = 41.83 µs`, which is
above the `T2 ≤ 2·T1` bound that coherence obeys by definition.

**Why nothing caught it.** Every physics check in the app is a predicate on ONE
value. `chip_health.physicality(key, value) -> bool` is literally that
signature; `diagnostics._unphysical_findings` reads one leaf at a time. Neither
can express a relation between two numbers. All 35 catalogued checks are
wiring, schema and hardware legality — "will this run on an OPX", never "does
this number make sense".

### 1a. Tier A — the bound, reported as a margin

`T2 <= 2*T1` has nothing to configure. But the one real violation on the chip
is **4.6% over**, which is inside what T1 and Ramsey fits routinely return. So
the finding is the MARGIN, and the severity follows it (`info` under 10%,
`warning` above). The 10% is SM's own judgement about fit convergence and the
constant says so in its own comment.

**Report, never drop** — the deliberate opposite of docs/162's confusion-matrix
rule. A row that does not sum to one is provably bad on its own. When T1 and T2
disagree, neither value is convicted: SM cannot know which fit broke, so it
names the pair and leaves both in every average exactly as the lab wrote them.

### 1b. Tier B — the criterion the measurement rewrote

The agreed design was: compare qubits that are ADJACENT IN FREQUENCY, and judge
them by the dimensionless `Δ·T`, where `T` is that qubit's own x180 length. The
scale comes from the chip (40 ns ⇒ ~25 MHz, 200 ns ⇒ ~5 MHz), so no constant is
invented.

The scale argument survives. The *pairing* did not:

| what is compared | how often it fires on the healthy 20Q chip |
|---|---|
| frequency-adjacent qubits (19 pairs) | **14** |
| declared `qubit_pairs` couplings (30 pairs) | **0** (tightest Δ·T = 2.38) |
| qubits sharing one xy output port | **0** (20 qubits, 20 ports) |
| readout resonators per feedline (3 groups) | 0 (min gap 76 MHz) |

A check that fires on three quarters of a healthy chip is not a check. And the
reason is physical, not statistical: **reusing a frequency between two qubits at
opposite corners of a chip is deliberate, correct design.** The lab put its
COUPLED neighbours far apart in frequency and reused frequencies where nothing
can interact. Flagging that would have called good engineering a defect.

So Tier B compares only pairs with a mechanism — a coupling the chip itself
declares, or one physical drive port two qubits share. On this chip it is
silent, which is what a diagnostic should be when things are fine. It fires when
a frequency is mistyped, which is the case it exists for (and which §3 makes
easier to cause).

**The red team was right about the labelling and wrong about the fix.** It
correctly said "no invented constant" was false — the cut at `Δ·T = 1` IS ours —
and the catalogue now says so in those words. It also proposed going to
all-pairs to avoid dropping true positives; the measurement says all-pairs is
27 findings out of 190. The mechanism gate answers the objection better: nothing
is dropped, because a pair with no mechanism was never a positive.

**And one CRITICAL of its own was correct and taken**: emitting Tier B as
`severity="info"` would have inflated the app-wide issues badge, since
`summarize()` folds info into `issues`. With the mechanism gate the check is
quiet enough that `warning` is right and counted — but the badge arithmetic was
worth knowing.

### 1c. Two rules that were NOT written

* **`amp > 1` and anharmonicity sign.** `_unphysical_findings`' own docstring
  says these "are not checked, and never will be from here". They were in the
  first proposal; they came straight back out.
* **`T2echo >= T2ramsey`.** It sounds like it must be true. Measured: it fails
  on **10 of 20 qubits** of a healthy chip. A rule that half a real chip
  violates is not a rule.

### 1d. What the pointer chain taught

`operations.x180` on a real chip is `"#./x180_DragCosine"` — a self-ref, which
`pointer_resolver` deliberately never follows, and which the store's own
`resolve_value` raises on:

```
KeyError: Cannot traverse into str at 'length' in 'qubits.q1.xy.operations.x180.length'
```

The read goes through `pointer_path.resolve_field_target`, which crosses a
pointer mid-path. **The fixture carries the alias form for that reason**: an
inline fixture cannot fail when the pointer walk breaks, and the check would be
dead on every real chip while passing its own tests. (docs/166 §2 learned this
the same way, one week earlier.)

Whole-chip result: **one finding**, the real one.

`tests/test_physics_diagnostics.py` — 21 asserts, 16/16 mutations red.

---

## 2. "runs →" — from a qubit to the experiments that measured it

The Datasets side already had everything: the grammar, the Qubits AND-filter,
the pickers. `_qubit_detail.html` already linked OUT to `/pulses?q=`. Only the
link back was missing.

**The token is the BARE name, and that is the whole design.** A bare token is
matched exactly against the run's own qubit list; `qubit:` and `pair:` are
SUBSTRING scopes, so a link sending `qubit:q1` would quietly answer with q1 and
q10 through q19. The bare form is exact for pairs too, so one expression serves
both inspectors.

Two ways a link like this lies, both closed:

* **meaning MORE than it says** — the substring scope above.
* **meaning LESS** — the picker/facet/experiment ticks persist across swaps by
  design, so arriving with a preset while a stale tick is live ANDs them and
  shows zero rows. A preset ARRIVAL clears them. Not a *changed* preset: the
  same link pressed twice must clear twice, because a tick made between the two
  presses is the same silent lie one press later. A swap carrying no preset — a
  date tab, Rescan, nav-back — is byte-identical to before.

The date tabs carry the search with them, so a deep-linked filter does not
evaporate on the first date click.

13 asserts, 13/13 mutations. One pin had to be **scoped to the actions block**:
the same Jinja test appears elsewhere in the template, so a whole-file `in`
assert stayed green while the link's own branch was narrowed to qubits.

---

## 3. Scaling a selection — `*1.1` over twenty cells

"Raise readout amplitude 10% on all 20 qubits" was twenty calculations and
twenty typed numbers. The grid already had multi-select, Ctrl+D and multi-line
paste (docs/111); only ARITHMETIC over a selection was missing.

**Computed on the client, and the server learns no relative grammar.** Three
reasons in order of weight:

1. `parse_value` is shared by `/field/edit`, `/field/edit-batch`, the CLI, the
   type-fix offer and the pull REPLAY. Teaching it `*1.1` hands the grammar to
   all of them, and a replay that re-multiplies compounds.
2. **`+5e6` is already a valid absolute literal in a cell.** Any in-cell
   relative grammar would silently change what that input means today.
3. Nothing commits before the user saw the offer — the FSP compensation
   contract's rule. Computing here makes the offer free.

**The preview is unconditional.** A threshold would be a constant SM invented,
and one extra click on a two-cell change is cheaper than one wrong twenty-cell
fill. It lists every new value, every skipped cell WITH ITS REASON, and every
cell that already holds the result.

**Exact decimal, not float.** `0.215 * 1.1` is `0.2365`; the float answer is
`0.23650000000000002`, and that would land in `state.json`, in every Δ chip and
in the leaf index. Output is plain comma-grouped decimal, never exponential —
the JS mirror of `type_policy._PLAIN_GROUPED_NUMBER`, which is exactly the shape
whose commas `parse_value` strips, so the round trip through the unchanged
server holds by construction. A non-terminating division or a `calcEval`
operand falls back to float64 and **the row says so**.

`%` is always a fraction of the cell's OWN value, stated in the bar's title and
the preview header. SM must not decide that one field is "a percentage" and
another is not; for percentage points the plain additive form is right. A bare
number is refused — it would be a second, worse fill-down.

**The fixture was the finding, again.** The first mutation sweep scored 14/16,
and both survivors were guards the harness could not reach: it had no read-only
cell and no LINKED pair. Adding them turned both red — the read-only skip, and
the snapshot-`prev`-first rule (writing a linked cell mirrors its sibling, so a
read-as-you-go `prev` records the intermediate; audit F13). 53 asserts, 16/16
at that point; the file is at 100 now (§8b, §8c).

---

## 4. The sync pill carries the notifications, as a state

The user's directive, translated: *"it must not happen too often — e.g. it must
not pop up every time an experiment run finishes. Rather… the Sync button turns
blue/rounded? Anyway a small visible mark on the sync button is enough."*

**The first half of delivering that is a deletion.** `#new-run-popup` was
already showing a card once per detected run, on every page, with a 7-second
auto-dismiss. A hundred finished runs made a hundred cards. That was the
complaint. The card survives as what the chip OPENS — the same information,
pulled instead of pushed.

**The count accumulates because two baselines are now two variables.** This was
the review's CRITICAL and it was correct: `_lastSeenStamp` must advance on every
detection (that is what makes "strictly newer" work), so a single variable meant
the server's "how many since" reset every poll and the chip could only ever read
`1 new`. `_ackStamp` moves only when somebody clicks. `/datasets/poll` gained
`since_date`/`since_time` and counts in the walk it was already doing.

**Two announcements were designed and cut**, and the reasons are about honesty:

* `live changed` — `/state/drift`'s refresh returns early on a dirty context, so
  the 5-second poll cannot keep the flag true there. A chip that is right only
  sometimes is worse than none, and the pill's own server-rendered
  `state-status-drifted` already covers the clean case.
* autofit `needs_human` — the engine flag is level-triggered with **no clearing
  path**, so the chip could be shown but never legitimately dismissed. A
  per-tab suppression latch would be a lie about a robot still waiting.

Both are pinned as absences **at runtime**, not by source search: the reasons
are written in the module's own comments, where a grep finds the words and
proves nothing.

`run done` enumerates the terminal set from `runner_status`'s own vocabulary
rather than negating `"running"` — `paused` is a first-class status, and a user
pausing their own queue must not be told it finished.

30 + 19 + 17 asserts; 18/19 mutations. The survivor is a genuine no-op:
registering an unknown KIND changes nothing observable because `render()` walks
`ORDER`, and the mutation that adds to BOTH is caught.

---

## 5. Notes that outlive whoever wrote them

"q12's flux line contact is suspect, do not trust these values" had nowhere to
live. `DatasetStore.set_note` exists but only for a RUN.

**A sidecar, and the reasons are structural rather than preferential.** It
writes zero bytes of `state.json`, so it cannot race an experiment's
`os.replace` and needs no Apply. A LEAF note (`qubits.q12.T1`) has no home in
`extras` without a dotted key, and a dotted key is ambiguous under SM's own
dot-path grammar — so leaf notes are sidecar-only BY CONSTRUCTION.

**Which chip key, decided once**, because two plans in this round picked
opposite answers and a reviewer caught the contradiction:
`working_copy.key_for`, the folder-shaped key every other user-preference
sidecar already uses, and NOT the identity ladder. The ladder is designed to
re-key and heal — it adopts a directory by `extras.chip_name` and can return a
not-yet-existing dir. Right for a snapshot store, wrong for a note, whose text
must be exactly as stable as the folder the user is looking at.

**Orphans are reported, never tidied away.** A regenerated chip leaves notes
pointing at nothing; each keeps its last-known subject and gets Re-address
(checked against the loaded chip, so a typo cannot make a second orphan) and
Delete. With no readable chip NOTHING is stamped orphan — the `physical_units`
rule: annotate only when the input resolves.

**Concurrency, claimed exactly and no further.** The write re-reads inside the
lock and applies only its own subject, so two windows noting *different* qubits
both survive. A per-note `rev` is a compare-and-swap token; a stale one is
refused with a 409 carrying THEIR text. Residual: two windows writing the SAME
subject inside one read-to-replace window can still lose a write.

**Three pins were rebuilt because a review showed they could not fail.** The
shape repeats and is worth naming:

* a chip name staged into the WORKING COPY never changes what the LIVE files
  say, so a test that stages one and expects the key to move tests nothing;
* `/load` serves a provably-clean working copy unchanged (`sync_if_clean=False`,
  docs/87), so editing a live file behind the app and re-loading proves nothing;
* `created_at` is stamped to the second, so two writes in one test share it
  whatever the code does.

41 asserts, 22/22 mutations.

---

## 6. The spec bands say whose they are

The symptom, on the real chip:

    QUBITS IN SPEC   0/20   (16 warn · 4 fail)

which is not a statement about the device. It is a statement about a comparison
nobody had configured, and the page did not say so.

**The labelling is the fix**, and the reviewer was right to reduce the feature
to it plus one shared layer. Where a qubit is called out of spec the tile now
adds "against SM's default bands" or "partly SM's default bands", and is silent
once the lab has set its own — at that point the number means what it says.

The spec moved from `localStorage` to `instance/spec_thresholds.json`: five
people had five definitions of "in spec" and clearing a cache erased one. Only
the bands that DIFFER from SM's seeds are stored, so a later correction to a
seed still reaches a lab that once pressed Apply — a full copy would freeze
today's defaults into the file.

**A per-chip layer was cut**, and that absence is pinned: it adds a second place
to look when the numbers surprise somebody, for a symptom caused by labelling
alone.

25 asserts, 20/21 mutations. The survivor is a no-op — widening `_BOUNDS` leaks
nothing because the numeric filter beside it is the real gate. That filter is
now pinned against a **hand-edited sidecar**, a case a sweep showed nothing
covered because every junk value in the tests went through `save` first.

---

## 7. A hand-tuned mark, and the lock that was not built

The ask was value LOCKING. A real lock was deferred, and not for effort.

"Every write path must respect it" is understated. Thirteen write paths; five
replace the tree wholesale and cannot honour a per-path rule without
reintroducing the docs/65 mixed-content hazard; undo and discard write through
`_revert_entry` and never see a `set_value` gate. **And the actor that actually
overwrites a hand-tuned flux point is the lab's own qualibrate node — a process
SM spawns but does not mediate.** A padlock that walks through is worse than no
padlock, because people stop checking the gates SM *can* enforce.

So what ships is the advisory the deferral implies, at near-zero cost because
§5's notes already carry a per-subject record: one flag, and one clause in the
`overwrite-live` preflight, which already names what disappears. **The mark
blocks nothing and says so in its own tooltip**, and that is pinned.

`touches()` is deliberately generous — it decides what a confirmation MENTIONS,
so naming one path too many costs a sentence while missing one costs the whole
point. The prefix test is on a dotted boundary, so `q12` never matches `q120`.

12/12 mutations, and three needed a jsdom harness rather than a source pin: an
EDIT that silently drops the mark is exactly the failure the feature prevents,
and no source search can see it.

---

## 8. What was DEFERRED, and why

**Value freshness from SM's own snapshot history.** The reviewer moved this from
`ship_reduced` to `defer`, and its reasoning was better than the plan's own:

> "last changed" is NOT "last measured". A value re-measured and returning the
> same number produces no transition, so the leaf index reports it stale when it
> is fresh.

The plan had raised this itself and then designed around it; the reviewer
showed the workaround did not hold. It also found something the plan called
absent that is not: `core/autofit/families.py`'s `UpdateSpec` already maps a
node family to the state paths it writes, which is the right foundation for a
real "last measured" — and a reason to build this properly rather than
approximately. Nothing shipped.

**The autofit writer's missing journal trace.** `writer.py` saves at three sites
with no journal capture, and `Saver.save` clears the change log, so a robot's
writes are invisible to Ctrl+Z and to the applied log. That is a real defect,
found by a plan that was asked about something else. Fixing it means journalling
at the write path's RETURN points — a successful save only proves the WORKING
COPY was written, and a failed live apply returns `action="staged"` — and it
makes robot writes Ctrl+Z-walkable, which is a deliberate change to the
closed-loop calibration path rather than a side effect of an attribution
feature. Recorded, not built.

**A `note:` search token** (the bulk grid's classifier has no scope branch at
all; adding a fourth class touches docs/110's hottest path) and **a Chip Status
stone dot for notes** (a docs/92 decision about which of the two maps owns it).

---

## 8b. Verified in real Chrome, on the customer's chip

Thirty-two checks, driven over CDP against `PJ_10082026` with the CQT run
archive attached (the browser extension cannot reach this machine's localhost —
docs/141 §5's tooling). All pass.

What that run found that no unit test did:

**A scaled value was showing more digits than a double can hold.**
`0.45919729451219904 * 1.1` is *exactly* `0.505117023963418944` — eighteen
significant digits — and `state.json` stores doubles. The exact-decimal
arithmetic was right and the SPELLING was not: the server parses the long form
to the nearest double anyway, so the cell and the chip would have disagreed in
their last digits from the moment it was applied. Past seventeen significant
digits the double's own shortest round-tripping form is written now, and the
row is marked NOT exact — the same disclosure a non-terminating division
already carried. Same double, fewer lies.

> **Superseded by §8c.** "Past seventeen significant digits" was the rule as
> shipped that night, and a digit count turned out to be the wrong question in
> both directions. The rule now is the round trip itself. This paragraph is
> left as written because §8c is the record of why it changed.

That fix exposed a second one in the same file: `_floatStr` used `toFixed(20)`,
which pads a double out to twenty digits it does not have. It goes through
`ValueDelta.parse` of the NUMBER now — `String(x)`, the shortest round-tripping
form — which is both the shortest honest spelling and never exponential.

**And two "failures" that were the verification's own**, worth writing down
because both are easy to repeat:

* Diagnostics' domain sections are collapsed `<details>`, and `innerText`
  excludes collapsed content. The check read an empty string and called the
  feature broken. The finding was rendering perfectly the whole time.
* `/datasets?q=q7` showed the no-workspace branch because that server had no
  data folder attached. Not a defect — but a check that cannot tell "the
  feature is missing" from "the precondition is missing" is not a check.

---

## 8c. Four review rounds over §8b, and the fix that took all four

2026-09-05. §8b was written one night and reviewed the next day, four
times — and every round after the first found something wrong with the round
before it, including a defect the second round had already shipped into this
document as a virtue.

**Round 1** — six independent lenses over the two uncommitted files AND over
the commit §8b describes, every finding then reproduced, refuted and
impact-checked by separate agents (33 agents, 21 findings, 5 survived).
**Round 2** — a heavy red team plus four customer-role reviewers over round 1's
own fixes. It hit a session limit with 33 of 38 agents dead, so its 12 findings
came back UNVERIFIED; they were verified by hand instead, which is how the
largest defect in this round was found.

### What was wrong, and is now fixed

**A pointer to the wrong section.** §8b credited the CDP tooling to
`docs/141 §4l-review`, which is that file's five-reviewer adversarial round and
mentions CDP, headless Chrome, DevTools and the extension exactly zero times
(lines 554–702). The tooling is `docs/141 §5`.

**Two of the three call sites were pinned by nothing.** `_fitDouble` guards
three branches — `*`, `+`/`-`, and the terminating `/`. Deleting the guard
outright from either of the last two left all 57 assertions green:

    M1  * branch: delete the guard      RED
    M2  +/- branch: delete the guard    GREEN   <- nothing pinned it
    M3  / branch: delete the guard      GREEN   <- nothing pinned it

The commit message's "18/18 mutations red" was true of the mutations it ran; the
two it did not run were the two that mattered.

**The digit-count gate was wrong in BOTH directions.** `_sigDigits(text) <= 17`
is a PROXY for "is this text already the double's own spelling", and a proxy is
all it is:

* `0.45919729451219904 * 2` is exactly `0.91839458902439808` — seventeen
  significant digits, so the gate passed it — and the double's own spelling is
  `0.9183945890243981`. Measured on the customer's 20-qubit chip, one plain
  `*1.1` over the `x180_amplitude` column left **11 of 20 rows** spelled with
  digits the chip does not keep, each marked exact and carrying no glyph.
* `1000000000 * 1000000000` is 1e18, which a double holds EXACTLY, but its
  nineteen characters tripped the same gate: the row was marked as rounded when
  nothing had been rounded.

So the glyph was close to an inverted signal — the marked rows were the
trustworthy ones. `_fitDouble` now asks the question directly: is this text
already what `_floatStr(Number(text))` spells? All twenty rows are honest now,
and 19 of 20 carry the mark, because on this chip 19 of those products genuinely
are not storable as written.

**The integer carve-out was wrong too, and a third review round removed
it.** It was the reason the first draft of this section refused the fix at all:
`type_policy.parse_value` tries `int()` before `float()`, so an integral text
looked like it would be stored as an exact arbitrary-precision Python int, and a
blanket round-trip rule would rewrite `9007199254740993` to `9007199254740992`
— silent numeric corruption in the name of fixing a spelling.

That is true of `parse_value` and **false of the path this grid takes**.
Arithmetic always scales a cell that ALREADY holds a number, so
`modifier._type_coerce` has a non-None old value, and it casts through
`float()` for an int-typed leaf just as it does for a float-typed one:

    parse_value('9007199254740993')            -> 9007199254740993   (int)
    _type_coerce(7,   9007199254740993)        -> 9007199254740992   (int)
    _type_coerce(0.5, 9007199254740993)        -> 9007199254740992.0 (float)

So the value is rounded either way on that path, and the round trip is the
right question for integral text as well. Removing the carve-out also settles
the false mark, since 1e18 written out round-trips and is left alone.

**One measured exception, named rather than handled.** When an ENFORCED type is
in force — an env schema, or a docs/79 verdict — `_checked_value` calls
`policy.check` instead of `_type_coerce`, and its `_reconcile_numeric` DOES keep
an exact int on an int-typed leaf:

    _reconcile_numeric(7,   9007199254740993) -> 9007199254740993   (exact)
    _reconcile_numeric(0.5, 9007199254740993) -> 9007199254740992.0

so above 2^53 on an enforced int leaf the preview would understate what is
stored. Reaching it needs an integral leaf past 9.007e15; the largest value on a
real chip is a ~1e10 frequency. The client cannot see the expected type anyway,
so this is recorded, not handled — and the sentence that used to say "either
way" without qualification was overbroad, which is the same class of error as
the premise it was correcting.

**And removing it exposed a second bug — which the carve-out had been hiding
only HALF of.** `_decStr` comma-groups the integer part, and the comparison was
made against the comma-stripped string, so every result over 999 came back
"shortened" when nothing had been shortened. The carve-out returned early on the
INTEGRAL ones, so removing it is what made a doubled 6 GHz frequency visible —
but a fractional result over 999 never took that early return, and was being
falsely marked in the version this document had already called correct. Both
spellings have their grouping removed before the comparison now, and §1c3 pins
an integral case, a fractional one and the value that only just crosses the
grouping threshold, for that reason.

**The glyph was claiming something it could not know.** The legend read "rows
marked ≈ were computed in floating point". After c3dd655 TWO different things
set `exact: false`: a division that never terminates (floating point, true), and
an exact BigInt decimal shortened to what a double holds (not floating point at
all). It names both causes now — and that is also what answers the second
objection to the round-trip fix, which was that it would stamp a
floating-point label onto rows computed exactly.

**"Nothing is written to the chip" was false exactly when it mattered.** The
preview's closing sentence was unconditional, but inside an armed Auto-Sync push
session (docs/117) staging IS writing: the tray observer flushes it to the live
chip with no further press. The sentence is now conditional on
`AutoApply.armed()`, falling back to the tray's own `data-auto-apply` marker so
a preview opened before that module loads still tells the truth rather than the
reassuring answer — pinned on both paths, because the fallback exists for
exactly the window in which a wrong answer is most likely.

**And it calls the control what the user's control is called.** The module is
`auto-apply.js`, so the first draft of the sentence said "Auto-apply is armed" —
but docs/120 item 8 renamed the visible control, and the pill, the panel and
`_diff_workbench.html:205` all say **Auto-Sync**. A sentence naming the module
sends the reader looking for a switch that does not exist. Three of the five
final-review lenses raised this independently.

**And the sentence now agrees with its own plan.** "1 cell will change — rows
marked ≈ were computed" was the plural half of a sentence whose first half is
carefully singularised.

**The expressions the bar advertises were all refused.** Not a rounding matter
at all — found by a red-team lens looking at the legend and following
`exact: false` back to its sources. `calc.js`'s `calcEval` returns
`{ok, value}`; `_arithOperand` stored that object in `v` and gated on
`typeof v !== 'number'`, so it was ALWAYS null. Every expression the bar's own
tooltip promises — `*10^(-1/20)`, `/sqrt(2)`, `*(1+0.05)` — came back as an
error toast, while the tooltip went on promising them. Introduced by 5146650 in
this same round; it has never been on `main`. `calc.js` is now loaded in the
selfcheck's world, because stubbing it would have hidden exactly this.

**The import consolidation was half done** — `compare_sources` was still a
standalone line three below the group `spec_thresholds` had just joined.

### What this round is really about

The first draft of this section recorded a DECISION not to fix the gate, and
argued it well: the residue is a transient pre-audit display, both spellings
parse to a bit-identical double, and the apply path repaints each cell from the
server's committed value (`bulk-edit.js:1553-1557`), so it cannot outlive a
press. Every one of those statements is true and none of them was the point.

What changed the answer was measuring it on the real chip. "Four of six
operators on one contrived value" is a corner case. **Eleven of twenty rows of
one ordinary column under the most ordinary operator there is** is the feature's
normal behaviour — and the user-visible symptom is not the extra digits, it is
that eight rows carry a mark and eleven identical-looking ones do not, for
reasons invisible from the screen.

The two objections that killed the fix were both answerable. The legend fix
removed the false label; and the integer objection turned out to rest on a
premise nobody had measured on the path the grid actually takes — which took a
THIRD round to find, after the carve-out written to satisfy it had shipped into
this very document as a virtue. A refusal that rests on two objections is worth
revisiting when both are cheap to check, and "cheap to check" means running the
real path, not reading the function whose name sounds right.

### What the reviews got wrong about themselves

**Round 1's minor and nit findings were verified by a SINGLE refuting lens**,
briefed to default to "refuted" when uncertain. Three of the sixteen it killed
were true on re-check: §3's frozen assert count, the half-finished import
consolidation, and the false glyph on large exactly-representable integers —
which was the same defect as the biggest finding of round 2, seen from the other
side and dismissed. A refutation budget that scales with the reporter's own
severity guess filters confidence, not truth.

**Round 2 lost 33 of 38 agents to a session limit**, and the script filed its
unverified findings under "refuted" because its survival rule reads a majority
of zero votes as a kill. Twelve findings arrived pre-dismissed. Verifying them
by hand is what found the dead expression path. A verification pipeline needs to
distinguish *nobody checked this* from *somebody checked this and it is wrong*.

**And a green suite hid a lost patch, twice.** Two patch scripts accumulated
edits in memory and wrote once at the end; when a later step's anchor assertion
failed, the earlier successful steps went with it. The suite stayed green at 74
assertions and said nothing, because the blocks that were missing were the ones
that would have failed. The mutation sweep is what caught it — four mutations
came back GREEN, and the reason was that the pins for them did not exist. Every
patch step writes its own file now.

### The sentence that described a mark the table did not have

`anyFloat` is raised at `_arithPlan`'s line 2404, and the unchanged-cell carve-
out returns at 2412 — eight lines later. So a cell whose scaled value lands back
on its own value flips the flag on the way OUT of the plan, and the legend, which
gated on that flag, described marked rows that did not exist. The empty-table
form of this predates the round; the fourth review's verifiers found the harder
one, and it IS new here, because the round-trip `_fitDouble` is what starts
producing inexact-but-unchanged cells at all:

    +1 over [0.5, 9007199254740992, ...]
       1 cell changes, is exact, carries no glyph
       -- and the sentence said "rows marked ≈ were computed ..."

The clause is gated on `_approxCount(plan)` now — the rows the table will
actually show — not on `anyFloat`, whose meaning ("something in this selection
was inexact") is left alone for any future reader. Measured against all five
shapes the reviewers built, empty and populated: the sentence matches the table
in every one.

Two smaller ones from the same round. The armed-session sentence said
"Auto-apply is armed" — the module's name; three lenses independently pointed
out that the user's control is labelled **Auto-Sync**, so the sentence sent them
looking for a switch that does not exist. And the comment justifying the tray
fallback claimed a load-order race that cannot happen: `auto-apply.js` is a core
script and `bulk-edit.js` is in the `grid` bundle after it, so the module is
always there. The fallback stays — the answer it exists to prevent is the one
that PROMISES a user nothing was written — but its comment now says why, rather
than inventing a race.

### The third round, and what it took to see

The final pre-push review is where the carve-out fell. Nothing in the suite
could have found it: 85 assertions were green, 15 of 15 mutations were red, and
the real-browser run passed 26 checks — because every pin and every probe was
written from the same wrong premise. What found it was running
`_type_coerce` against a float-typed leaf and reading the number that came back.

The rule that survives is smaller than any of the three that preceded it: **the
text the preview shows is the text `_floatStr(Number(text))` produces, or it is
marked.** No digit counts, no type carve-outs, no exceptions.

Final state: **100 assertions, 19/19 mutations red**; `2 failed, 682 passed,
19 skipped` across `test_bulk_edit` / `test_bulk_virt_server` /
`test_grid_editing` / `test_bulk_markup` / `test_bulk_pairs_picker` /
`test_web` / `test_auto_apply`, where those two failures reproduce identically
on a pristine `HEAD` worktree — measured, not assumed (docs/155 §10a); every
`tests/*.cjs` exits as it did before this diff (the two that do not are the
parity harnesses, which require a `cases.json` argument pytest supplies); and on
the real 20-qubit chip every value the arithmetic preview shows — under five
operators over all twenty amplitudes — is one the chip stores exactly, end to
end: the preview promised `1.1910395203393354` and `state.json` holds that
number.


---

## 9. Eight things this round is worth remembering for

1. **A plan that cannot measure says so, and then you measure.** The physics
   plan listed "I could not re-measure the customer chip" in its own open
   questions. It was right to carry the brief forward; the brief was wrong.
2. **A check that fires on a healthy chip is not a check.** 14 of 19 was the
   number that killed a design, and the fix was to ask what MECHANISM makes two
   values comparable at all.
3. **Pin absences at runtime, not by grep.** Every deliberate omission in this
   round is explained in a comment in the very file a source-search pin would
   read. Three pins were vacuous for exactly that reason.
4. **The fixture is the finding.** Three separate features scored short on their
   first mutation sweep, and in every case the survivor was a guard the harness
   could not reach — a read-only cell, a linked pair, a hand-edited sidecar.
   docs/141 §4af said this; it keeps being true.
5. **Reduce, and say what you reduced.** Four features shipped smaller than
   planned. Each deferral is written down with its reason, so the next round
   argues with it rather than rediscovering it.
6. **Check the premise on the path the code actually takes, not on the
   function whose name sounds right** (§8c). A fix was refused because
   `parse_value` keeps an integral value exact. It does — and the grid's writes
   go through `_type_coerce`, which does not. The carve-out written to satisfy
   that objection shipped into this document as a virtue and survived a green
   suite, a full mutation sweep and a real-browser run, because every pin was
   written from the same wrong premise. One call to `_type_coerce(0.5, ...)`
   settled it — and the correction was then overbroad in its turn, until a
   reviewer measured the ENFORCED branch and found the one path that does keep
   the exact int.
7. **Measure the residue on the real chip before deciding it is a corner case.**
   The same defect reads as "four of six operators on one contrived value" in a
   probe and as "11 of 20 rows of an ordinary column under `*1.1`" on the
   customer's chip. Only the second number is about the feature.
8. **A patch script that writes once at the end can lose the work it already
   did.** It happened twice here, and the suite stayed green both times —
   because what went missing were the pins for the mutations that then came back
   GREEN. The mutation sweep is the only thing that noticed.
