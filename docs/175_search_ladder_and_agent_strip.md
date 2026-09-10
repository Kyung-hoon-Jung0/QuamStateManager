# docs/175 — a typo finds the key, a range beats a list, and the Agent says how it is wired

Three customer items from one on-site message (2026-09-11), plus the stress
round they were required to survive. Everything below was measured on the
customer's own 1,826-run archive and driven in real headless Chrome; the
numbers are from those runs, not from reasoning.

---

## (A) `multiplzed` finds `multiplexed`

> "특히 파라미터를 입력하면 사실 많은 사람들이 multiplzed...뭐 이런식으로 오타
> 나잖아? 이렇게 오타로 해도 vscode나 유투브는 알아서 비슷한거 유사한거
> 리스팅을 해주던데?"

**The reported word settles the algorithm, and it rules out the two obvious
choices.** `multiplzed` is *not* a subsequence of `multiplexed` — there is no
`z` in the target — so a subsequence matcher returns **zero rows on the very
word that was reported**. Damerau-Levenshtein is **2**, so a distance-1 cap
misses it too. Real edit distance at k=2 is the requirement, not a preference.

**Distance to a PREFIX of the candidate**, never to the whole name: typing is a
forward process and the stem is a prefix in progress. That also makes the cost
`O(stem × band)` — independent of how long the candidate is — where a
whole-string similarity pays for the name and scores `multz` against
`multiplexed` as barely related.

**OSA transposition** is one line in the inner loop and turns `mutliplexed` and
`wiat_time` into distance 1. Without it a single swap spends the entire k=2
budget and ranks level with a genuine two-edit neighbour.

**Two prefilters, both valid LOWER BOUNDS** on the real distance, so neither can
reject a true match: a candidate shorter than `len(stem) - k`, and a
character-presence mask where `popcount(stemMask & ~nameMask) > k`. Mask bucket
collisions (`'0'` and `'p'` both land on bit 16) only weaken the filter.

### Guessing is gated, and the gate is the design

    FUZZ_MIN_STEM = 4    below four characters a miss IS a miss
    FUZZ_TRIGGER  = 3    guess only while the honest hits are this few
    _maxEdits(n)         0 under 4, 1 under 7, 2 above

A guess is for when the box would otherwise be empty. It never competes with a
real match, and it never *looks* like one: `rank` returns a third bin, `compose`
puts it under its own separator (`nothing matched — closest:`), the row is
italic with a leading `≈` in the warning colour, and accepting it inserts the
REAL key. All four boxes compose through the one function, so no box can present
a guess as a match by accident.

**Deliberately not built**: no Python twin and no JS↔PY parity pin. A suggestion
may be approximate; a query must not be, or a filtered run list silently
contains rows nobody asked for. `_param_hit` and `search_query` stay exact.

### Two per-keystroke defects fixed in the same commit

Because (A) puts weight on this path and the pre-existing waste must not take
the blame:

* `BulkTypeahead.vocab()` had **no cache** — a `querySelectorAll` plus a
  `querySelector`, an attribute read and a regex split **per header, on every
  keystroke**, at 335 headers on the customer's 20Q chip and up to
  `MAX_DYNAMIC_COLUMNS = 1200`.
* `TreeTypeahead.vocab()` keyed its cache on the **first model's identity
  alone** (the tag computed beside it was never used), so a wiring container
  mounting after the state one served a state-only vocabulary for ever.

---

## (B) the value ladder

> "multiplexed를 선택했다고 하자, 그러면 거기엔 True, False가 있어. 이걸
> 사용자가 입력하게 두는건 잔인해. bad UX야. … 그런데 amp같은 경우는 value도
> 많고 범위도 많기 때문에 까다로워... 한번 생각해봐."

**Measured first, on the real archive, because the numbers decided the design.**
Of 213 keys: **121 carry exactly one value (58%)**, ~78 carry 2–12, and **11
carry more than twelve** — `frequency_span_in_mhz` 31, `num_shots` 30,
`max_wait_time_in_ns` 21, `load_data_id` 20, `min_amp_factor` /
`max_amp_factor` 19 each. Those eleven are exactly the keys the report named,
and they are the ones a list cannot serve.

So three rungs, chosen by the key's own shape:

| the key has | what happens |
|---|---|
| one value | the row shows it (`reset_type   = thermal · 1,204 runs`); **Enter finishes the token**, Tab still leaves `key=` |
| a few | today's value list, unchanged |
| many | a comparison operator, with a preview |

    num_shots>=1000      3 of 4 values · 40 runs
    amp<0.5
    wait_time=100..1000

**The one argument against the operator did not survive reading the file.**
The claim was that the grammar is shared by sixteen search boxes, so an operator
means changing `tokens`/`caret_span` in both languages and moving a parity
harness. `search_query.tokens()` is `q.lower().strip().split()` and **never
inspects `=`**; `caret_span` scans quotes and commas only. An operator inside a
param token's *body* is invisible to every other box. The real change set is
`_SIDEBAR_PARAM_OP`, `_param_cond`, `_param_hit`, one negation character class,
`Typeahead.classify`, and the transcription into `dataset-virtual.matchScope`.

The panel also stops hiding things: it said nothing about 22 of a key's 30
values, and now says `8 of 30 values · 16 … 2000` — which is also the one place
the operator is taught, because nobody types a syntax they do not know exists.

### Three real defects found on the way, each measured before it was fixed

1. **A bare `key=value` on the DATASETS box filtered nothing at all.** It was
   routed into `scoped`, and `applyFilters` evaluates `groups`, built from
   `items` — so the token contributed no condition and every row passed.
   Measured against `7a96c29` to confirm it predates this round:
   `num_shots=1000` reported "Showing 2 of 2" over rows holding 100 and 1000.
   Only `param:num_shots=1000` ever worked, because the scope branch pushes both.
2. **`frequency_span_in_mhz=500` matched NOTHING on a run storing 500.0** —
   `param_norm` spells values through Python's `str()`. The numeric branch is
   tried only after the exact string match, so every value that matched
   yesterday still does.
3. **One `None` cost a key its numeric nature**, demoting
   `readout_amplitude_in_dBm` (12 values) and `load_data_id` (20) — the first
   being the "amp" key the report named. A run that recorded nothing is not
   evidence either way, and the same tolerance now applies on both pages.

And one I introduced, caught by the new pin: chaining the null carve-out onto
the `allNum` initialiser meant a key's **first** value never reached min/max, so
the Datasets range hint read "1000 … 1000" for values 100 and 1000.

**Deliberately not done**: the value order is **not** re-sorted by magnitude —
`num_shots=8000` is 538 of 1,782 runs and would land at row 27 of 30, outside an
eight-row panel. "Recognise my own run" is the dominant intent; the answer to
breadth is the operator.

### The load-bearing pin

`TestThePreviewCannotPrintACountItCannotDeliver` drives the **real panel** under
jsdom against the **real server matcher** over eight range tokens. The panel
counts in the browser from the vocabulary; the search counts on the server by
matching every run. A preview that printed a number the filter then did not
deliver would be worse than no preview.

---

## (C) the Agent wiring strip

> "지금은, agent를 눌러도 어떻게 이게 MCP처럼 작동하지? 하는 의문이 생겨.
> 직관적이지 않거든. codex/claude login 이라는 게 agent누르면 바로 상단에
> compact하게 깔끔하게 SM스타일로 뜨게 하자."

    CONNECTED  claude v2.1.267 · MCP ✓ · hooks ✓ · answered SM in 2.1 s · 14:32
               │ codex v0.153.4 — not registered as an MCP server  Connect →
                                                    ?   2 setup steps →

**SM cannot know whether anyone is logged in, and the strip must never say so.**
`agent_backend.detect` is `subprocess.run([exe, "--version"])` with
`found = returncode == 0`, which succeeds on a logged-out CLI. A repo-wide sweep
for `oauth`, `auth.json`, `CLAUDE_CODE_OAUTH`, `apiKeyHelper`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `credential` returns **one hit and it is
prose**. So the vocabulary is three words, in this order:

| word | what it means |
|---|---|
| installed | the binary answered `--version` |
| registered | SM's entry is in that CLI's own config file |
| **answered** | a real read-only call from SM to it succeeded — **past tense** |

There is deliberately no fourth. The `?` popover says the same in a sentence:
*"The CLI runs on your machine under your own login — SM never sees your
credentials, so it cannot show a login state."* That clause is simultaneously
the mechanism answer and the honest reason there is no green login dot.

**On failure it does not classify.** SM cannot distinguish an auth failure from
a network one, so the CLI's own words are shown verbatim in the title. A
confidently wrong diagnosis is exactly the complaint being answered.

### Split by what each half costs

* **Registration renders server-side at first paint** — that is what makes the
  MCP answer instant. `_agent_wiring` calls only the four file readers and
  **never** `_detect()`, which spawns the CLIs.
* **Measured before shipping**, because `GET /` is the most-hit route and its own
  docstring records that it deliberately pays no config I/O: median **1.1 ms**,
  p95 **3.0 ms** for the four reads, with `~/.claude.json` at **95 KB** here and
  growing with the CLI's own history. Memoized on those files' mtimes; an edit
  is picked up at once.
* **Versions and the last test ride the request `mount()` already makes**
  (`/chat/backends`), plus one `/api/agent/setup`, reused across mounts.

The strip is a **sibling** of `#agent-home`: `mount()` does
`root.innerHTML = skeleton(...)`, so anything inside it is destroyed on the
first feed. It renders on both doors — the home and the sidebar float — because
"agent를 누르면" means both; the float gets the compact form.

`allow ✓` is shown only when a calibrations folder is set, since the file it
reads lives inside one.

---

## The stress round

> "진짜 빡세게 해. 브라우저 열고 막 이것저것 클릭해보고 눌러보고 엔터 누르고
> 입력도 하고.. 등등 유저가 되어서 edge케이스도 막 테스하고 클릭도 여러번
> 누르고."

`tests/stress_drive.cjs` (60 checks) and `tests/stress_bigchip.cjs` (10) drive
real headless Chrome over CDP: real keydown/keyup per character; the word typed
one letter at a time with the panel read after every one; five typos; arrows
twelve past the end and twenty past the start; Enter vs Tab on the same key; a
range previewed then run; seven half-typed operators; an unbalanced quote; a
300-character stem; two tokens at once; Hangul composed a jamo at a time; Escape
and typing on; the same row clicked three times; a held-down arrow; 40 input
events back to back; the `?` clicked four times; the float opened and closed
three times.

**70/70, zero non-CSP console errors.** Measured on the 20-qubit chip: 335
headers, 82–94 ms per keystroke *including the driver's own 70 ms settle* (so
~12–24 ms of work, against docs/04's 50 ms budget), 275 distinct tree keys, and
the fuzzy pass's worst case — a sixteen-character stem matching nothing, where
every candidate reaches the DP — at 409 ms including settle.

### Three defects the round found

1. `claude v2.1.267 (Claude Code)` and `codex vcodex-cli 0.153.4` — the two CLIs
   print their version differently and "v" was prefixed onto whatever they said.
   `shortVersion` takes the number; anything with no number is shown verbatim.
2. The typeahead panel clipped its meta behind a **horizontal scrollbar**.
   Measured: panel 460 px, widest real row 473 px
   (`operation_amplitude_factor`, label 244 + meta 203), every other stem
   335–455. Panel to 26rem, `overflow-x: hidden` stated — the scrollbar appeared
   for free, because `overflow-y: auto` alone makes the other axis compute to
   `auto` — and the **key never gives way**: the meta ellipsizes, because the key
   is the identity. Re-measured: 520 px, no overflow on any stem.
3. `wirePaint` nested its own wrapper on every repaint, multiplying the backend
   lines. Found by reading the pins' output rather than their exit code.

### Reported, deliberately not fixed

35–49 identical CSP `EvalError`s per session, all from htmx compiling the event
filter in `hx-trigger="toggle[this.open] once"`
(`_sidebar_tree_macros.html`, docs/142's lazy groups) with `new Function`, which
this app's CSP forbids — the same shape docs/120 ② found for `hx-on::`.
`tests/stress_lazy.cjs` measures the consequence: **none.** A collapsed date
group still opens and still loads (38 entries, one `/workspace/tree/group`
request), because htmx logs and proceeds. It is console noise in a subsystem
this round did not touch, and the filter is redundant beside the `once` (a lazy
group always starts closed, so its first toggle is always an open) — worth its
own measured change, not a drive-by.

---

## Pins, and what the sweeps found about the pins themselves

| file | what it holds |
|---|---|
| `param_typeahead_selfcheck.cjs` | sections K (typo tolerance), L (the two caches), M (the ladder) |
| `test_param_typeahead.py` | the operator, the numeric keys, the twelve-token JS↔PY agreement, the preview equivalence, the panel geometry rule |
| `search_grammar_selfcheck.cjs` | the Datasets box end to end: the operator, the bare token, the null-tolerant facet, the `1e-05` spelling |
| `test_agent_setup.py::TestTheWiringStrip` + `agent_panel_selfcheck.cjs` §W | the strip |

**Mutation sweeps: 13/13 (A), 33/34 (B), 15/15 (C).** The one that is not is a
boolean guard that is **unreachable in JavaScript** (`Number('true')` is NaN)
and load-bearing in the Python twin (`float(True)` is 1.0) — recorded in a
comment beside it, since a pin cannot fail on a no-op.

**Six of my own pins were wrong, and the sweeps are what found them.** Recorded
because the pattern repeats:

* K5 tested the fuzzy GATE with a fixture in which every name was already an
  honest hit, so nothing could be guessed and it passed with the gate deleted.
* A source pin tripped on its own explanatory docstring (the third time in this
  project) — it strips comments now.
* One compared an escape sequence (`✓`) against the character it denotes.
* W10 counted requests in a window nine earlier harness sections had already
  cleared, **and** re-mounted an element that was already mounted, so it proved
  nothing twice over.
* The memo pin grepped the source for `st_mtime_ns`, which survives deleting the
  early return; it counts real reads now.
* A mutation I wrote was a no-op: it changed an outer guard while the
  element-wise loop inside it still did the work.
