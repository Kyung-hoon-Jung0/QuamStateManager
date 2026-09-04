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
read-as-you-go `prev` records the intermediate; audit F13). 53 asserts, 16/16.

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

## 9. Five things this round is worth remembering for

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
