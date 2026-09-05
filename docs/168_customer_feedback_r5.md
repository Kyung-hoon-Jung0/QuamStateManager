# docs/168 — five customer reports, and the two that were worse than reported

2026-09-05. A feedback round from the lab running SM daily. Five items, each
reproduced on their own data before anything was written, and two of them
turned out to be a bigger defect than the report described.

Method, same as docs/167: five investigators with a hard evidence bar
(`file:line` or a command with its real output), then an independent adversarial
auditor per plan whose brief was to re-verify every load-bearing claim. The
audits changed three of the five designs, and one of them caught a defect in a
design I was about to build.

---

## 1. A type SM itself flags cannot be fixed where SM sends you

**The report.** *"SM says a leaf that should be int is a str. I go to Json Tree
View, change `"3124"` to `3124`, and it is not applied. 반드시 고칠 것."*

**The server was never the problem.** `POST /field/edit` with `value=3124`
already answers correctly:

```
409  {"type_fix": {"path": "…x180_DragCosine.length",
                   "current_display": "\"3124\"", "proposed": "int"},
      "error": "… is stored as TEXT (\"3124\"), not a int — convert the type to
                store the number, or keep text (wrap the value in quotes …)"}
```

and `app.js:7638` already handles that 409 with a confirm and a re-post. **The
tree just never asked.** `commit()` compared the typed text against
`valEl.dataset.editVal`, which is the RAW stored value, while docs/145 fills the
editor with `JSON.stringify(value)` — the quoted form. So for a leaf holding
`"3124"`:

```
editVal  = 3124      (raw, 4 chars)
shownVal = "3124"    (6 chars — what the editor displays)
typed    = 3124      (the user deleted the quotes)
guard #1 (newVal === shownVal)   passes
guard #2 (newVal === editVal)    FIRES  ->  cancel()
```

The one gesture that unambiguously means "make this a number" was read as
"nothing changed", and the request that answers it was never sent. Measured in
real Chrome on the customer's chip: **zero** `/field/edit` requests, no error
chip, no toast, no tray row. Silent.

**The fix is a question, not a special case.** "Is this still a string?" — and
JSON is the judge: `3124` parses to a number, `true` to a boolean, `null` to
null, while `abc` does not parse at all. So retyping a NON-numeric string
unquoted still cancels, which matters: `/field/edit` does **not** no-op an
identical value (measured — the tray's change count goes 9 → 10), and that is
what the guard exists for. My first cut dropped the guard entirely and would
have put a no-op in the tray on every such gesture; the plan's narrower version
caught it and the measurement settled it.

### 1a. The escape hatch the 409 advertises could not be taken

The offer's own rule is explicit (`routes.py:6756`):

> the typed input is an UNQUOTED number (explicit quotes mean "I want text" — no
> gate)

and its message tells the user so: *"wrap the value in quotes to keep text
without asking"*. But docs/145's tree editor **unwraps a valid JSON literal
before posting**, which `tree_edit_literal_selfcheck.cjs` pins — so those quotes
never arrived, and the hatch was unreachable from the one surface that shows
them. The value still goes raw (the pin holds); the FACT travels beside it as
`value_quoted=1`.

### 1b. And "keep text" was not keeping the text

Found by the same investigation, on a chip carrying real label-shaped values:

```
slot   '007'    -> answer KEEP -> stored '7'
branch '02'     -> answer KEEP -> stored '3'
grouped '1_000' -> answer KEEP -> stored '1000'
'007' + '"008"' (quotes kept)  -> stored '008'   <- the ONLY surviving path
```

The user was asked *"convert to a number, or keep text"*, answered KEEP, and the
zeros were dropped anyway — because `keep` only skipped the 409 and the value
still went through `parse_value`. Both answers now preserve what they promise.
All five paths measured after the fix: `007`, `03`, `1_000`, `008`, `009`.

**Real Chrome, whole gesture, customer's chip:** `"3124"` → SM asks → `3124`
(int). **25 selfcheck assertions + 9 pytest, 9/9 and 4/4 mutations red.**

---

## 2. A node found by search could not be opened

**The report.** *"Search `port`, press the arrow on `ports.analog_outputs.con1
→ 7 → 1`, and NOTHING happens. 아주 bad UX."*

Reproduced in real Chrome on the 20-qubit chip:

```
childrenInDom: 16    computedDisplayOfFirstChild: "none"
arrow ▼ -> click ▶ -> click ▼      childRowsPainted: 0, 0, 0
```

The search hides non-matching rows with a per-node class and renders every KEPT
node **expanded**; `_toggleNode` only flips the wrapper and never clears that
class. So the arrow read open over sixteen children that were all
`display: none`, and the click that looked like "expand" was really a collapse
of something already invisible.

**The rule now:** while a search is filtering, the arrow on a kept row means
*"show me this row's values"* — match or not. **One level per click**, so a
press can never flatten a subtree; a revealed container keeps its own hidden set
and its own arrow. A revealed row is muted, because it did not match and must
not read as a hit.

Measured on the customer's node: one click shows all 16, a second collapses, a
third opens again, and the 150 search highlights survive.

**Pinned by an EXECUTING harness**, not a grep — the fixture drives the real
`app.js` through the app's own `__treeSearchMaterializeMax` override, because
the real chip only reaches this state through the 150-match cap. **22
assertions, 6/6 mutations red.**

Two of those six were green on the first try and both pins were vacuous: the
wrapper was already open (so "the reveal opened it" could not be distinguished
from "it was open all along" — the fixture collapses it first now), and the
nested container was `{}` (so "one level per click" could not fail — there is a
second world with the grandchildren materialised now).

---

## 3. The run list: a little smaller, noticeably tighter

**The report.** The recent enlargement (docs/165) is much liked — *"아주 조금만
더 작게"*. And separately: the spacing BETWEEN runs is too wide. And: could the
type be more modern?

**There is no gap between rows at all.** Measured in real Chrome on the 2,655-run
archive, 350 rendered rows: the gap between every consecutive pair is `0px`. What
the customer reads as spacing is the row's own height, and **66% of rows wrap**.

The line box was 30.97px rather than the 26.84px `.entry-name`'s own
`line-height: 1.3` implies, because the STRUT of its block parent inherits the
page's 1.5 and wins. Naming the dense value on the row is what moves it.

| | HEAD | now |
|---|---|---|
| average row height | 64.26 px | **47.51 px** (−26%) |
| rows in one sidebar viewport | 14.2 | **19.2** |
| font | 20.64 px | **19.71 px** (−4.5%) |
| rows fitting on one line | 120 | **159** |
| rows needing three | 54 | **37** |

The size step is one 4.5% notch (1.32 → 1.26em, both tokens together — a date
header must never be smaller than its own rows). The compaction is the rest, and
it is where the "too far apart" complaint actually lived.

**Modern, grounded in what the app already does:**

* The open run was `background: var(--pico-primary-background)` with inverse
  text — on a three-line row, a solid block — and it **masked** the app's other,
  subtler active treatment two hundred lines above (a 16%/36% tint plus a bold
  name). The codebase carried two competing selected-row styles and one was
  dead. Now: tint + the house accent bar + bold, three cues instead of one slab.
  The bar is an inset shadow, so selecting a row costs **zero layout** (measured:
  identical row height and name offset with and without).
* The hover underline on a whole list row is gone — the row already tints and
  the cursor is already a pointer. Keyboard focus keeps its own outline.
* The date headers use tabular figures, which `.run-id` already did, so
  `2026-08-19 (467)` lines up across the seven groups.

---

## 4. A date header that scrolls away takes the only way to collapse it

**The report.** A long folder fills the screen and getting back to the top means
scrolling all the way up; the parent should stay visible.

Measured on the real archive: one capped date group is **3,630 px — four sidebar
screens**, and **48.6** after "Show all". The header is plain static flow
(`.tree-date-label` sets font, weight, colour, spacing, and no positioning).

The level that holds ROWS is marked `.tree-leafdir` by the template and pins its
own summary. Three things the measurement decided:

* **`top` cancels the sidebar's own padding rather than being 0.** With `top: 0`
  a run row paints in the 8.4 px strip above the pinned header, because
  `overflow: auto` does not clip at the padding-box edge.
* **The background is not decoration.** `.tree-dir.tree-leafdir > summary` is
  (0,2,1) and beats `details.tree-branch-active > summary` at (0,1,2), so
  without publishing the active tint as a token the active branch would have
  lost its colour the moment this rule shipped. The auditor measured that and
  called the mitigation mandatory, not a precaution.
* **A bounded gap, named rather than claimed.** The plan asserted "one sticky
  level per branch, by construction". The auditor built the counter-example: a
  container holding BOTH sub-containers and its own rows takes a different
  template branch and gets ZERO sticky levels. No archive on this machine has
  that shape, so it is recorded as a gap.

Verified in real Chrome: pinned exactly at the sidebar top (89 === 89), a
hit-test at the header's own coordinates returns the header (nothing paints over
it), and **clicking it while pinned still collapses the group** — which was the
whole ask.

---

## 5. "This is correct" — telling SM a finding is expected

**The report.** *"SM says the type is wrong. It cannot know that I introduced
this key on purpose — but I must be able to tell it, and after that the check
should pass it as healthy."*

**The machinery existed and could not be reached, and could not express this
anyway.** Three defects, all measured:

1. The only control that can CREATE a type verdict renders under
   `transition.changed` — i.e. only after a library upgrade has recorded two
   different env baselines. **On a normal single-env install there is no create
   control anywhere in the UI.**
2. The button literally labelled "This is right now" (`decision="accept"`)
   silences nothing: `enforced` is set only for `override`, and both
   `overlay_manifest` and `verdict_signature` filter on it.
3. The customer's own case is refused by design. On their chip, with a real
   `cqt` probe: four error findings over 24 places — three `unimportable_class`
   (no field at all) and one `unknown_field` (409 by design) — a red banner on
   every page load, and **every action offered on every one of them was "Go to
   field"**.

**The audit killed the first design.** It rested on the claim that
`analyze_state` aggregates on the same key a verdict uses. It does not: the
finding key is the 4-tuple `(kind, class, field, code)`, the verdict key is
`class.field`, **and they collide in practice**. One acknowledgement would have
silenced a different finding on the same field.

So this is its own store (`core/env_ack.py`), keyed by the finding's own
identity, and it changes no type expectation anywhere — `type_policy` never
reads it, pinned as an absence.

**What an acknowledgement does and does not do.** It does not make the finding
false and does not lower its severity: `Quam.load()` in that environment would
still fail exactly as before, and the row keeps its error tier. What changes is
that SM stops *raising* it — the acknowledged bucket leaves the `issues` count
that drives the red banner and the badge, exactly as `advisory` already does.
The row stays on the page, muted, with the date, and revocable.

Two things it deliberately will not outlive:

* **The environment.** An acknowledgement says "this env does not declare that,
  and I know". Point SM at a different env and it resolves to nothing.
* **Its own subject.** The sentence the user was reading is stored, and if the
  finding later says something different the acknowledgement lapses and the
  finding comes back. Silence that outlives what it was about is the failure
  this guards against.

**Rejected, and worth recording:** letting an `override` verdict silence
`unknown_field` is what the report literally asks for and is a lie — verified
against real quam 0.6.0 with the customer's own class, `Quam.load()` raises
`AttributeError: Unexpected attribute`. SM must not tell a user a problem is
gone when the run will still fail.

**22 pytest assertions, 12/12 mutations red.**

---

## 6. What this round is worth remembering for

1. **The server was right and the client never asked** (§1). Two of the five
   items were a UI guard swallowing a gesture, not a missing capability — and
   both looked from the outside like "the feature does not work".
2. **An audit that re-verifies claims changes designs, not just wording.** The
   key-collision CRITICAL in §5 would have shipped a control that silenced the
   wrong finding. It was found by one agent reading the two key functions and
   running them.
3. **A measurement can make a small fix bigger and a big fix smaller.** §1's
   report was one leaf; the investigation found data loss on the "keep text"
   answer. §3's report was "too far apart"; the measurement found the gap is
   zero and the leading was the whole story.
4. **A vacuous pin passes.** Four of the pins written this round went green
   against a mutation that broke exactly what they claimed to hold — a wrapper
   that was already open, an empty nested container, a fixture with one code
   value, a guard whose branch is unreachable. The mutation sweep is the only
   thing that noticed, every time.
5. **Two guards measured unreachable were dropped, not pinned** — a leading-quote
   check redundant with the parse that follows it, and an empty-env-key guard no
   record can ever reach. A pin on a difference that cannot happen is a pin that
   cannot fail.
