# 75 — Inline-edit commit stability (the Pulses "Leave site?" bug)

Status: **fixed**, 2026-08-04. Branch `fix/pulses-commit-stability`.
Surfaces: Pulses detail (primary), Qubit/Pair inspector inline rows (same
code path).

## The report

> 1. pulse에서 파라미터 바꾸면, length같은 것을 바꾸면, 무슨 '사이트에서
>    나갈까요?' 하고 물어보고 있음
> 2. 파라미터 수정하면 refresh가 굉장히 자주 일어나고, 그리고 enter를 눌러도
>    아무런 반응이 없을 때가 있음

Both symptoms are **one defect** plus its fallout. It is **not** an r16
regression: the same reproduction runs identically against the r13 tip
(`1b1d199`) — the faulty handler is byte-identical there and dates back to the
first commit. What changed is how visible it became.

## Reproduction (real browser, not a unit test)

Driven with Playwright + Edge against a dev server on the customer 17Q chip
copy, instrumenting every submit event, every request (with `resourceType`),
every dialog and every main-frame navigation:

```
keydown Enter          (trusted, INPUT.edit-input)
submit  #1             defaultPrevented=true      ← htmx handles it, POST /pulse/edit
requestSubmit()        from app.js focusout handler, inside htmx's swap
submit  #2             defaultPrevented=FALSE     ← nobody prevented it
htmx:afterSwap #inspector-pane
DIALOG  beforeunload   "Leave site?"
```

## Root cause

`app.js`'s inline-edit **focusout → commit** handler (Tab / click-away
commits, so a typed value is never silently lost) carried this assumption:

> `focusout` does NOT fire on Enter (Enter never blurs the input), so there's
> no double-submit.

The premise is true; the conclusion is not. Enter's own commit response
**re-renders `#inspector-pane`**, and removing a focused element fires
`focusout` on it. At that instant the discarded input still holds the typed
value while its `data-committed` attribute still holds the OLD one, so the
"unchanged value" guard passes and the handler calls `requestSubmit()` on a
form whose commit is **still in flight**.

htmx's default `hx-sync` behaviour for an element that already has a request
in flight is to **drop** the duplicate — and a dropped request means htmx
never calls `preventDefault()`. The browser is then free to perform the
form's *native* submission. These forms carry no `action`, so that is a GET
to the current URL with the fields appended:

```
/pulses?path=qubits.q1.xy.operations.saturation
       &dot_path=…length&mode=value&value=5329
```

Measured outcomes, both reported by the user as separate bugs:

| tray state at that moment | what the user sees |
|---|---|
| unsaved edits pending | the browser's **"Leave site?"** prompt (symptom 1) |
| “Leave” clicked | **full page load** of the URL above — inspector closed (symptom 2) |
| nothing pending | the same navigation, **silently** — reads as a spontaneous refresh |

Two further measurements explain the rest of symptom 2:

* **focus is lost on every commit** — after the pane re-render
  `document.activeElement` is `<body>`, so the next keystroke or Enter goes
  nowhere ("enter를 눌러도 아무런 반응이 없을 때가 있음"). Nothing was
  restoring it.
* **five requests per single field commit**: `POST /pulse/edit`, a full
  50-row `GET /pulses` table refetch, `GET /diagnostics/summary`,
  `GET /diagnostics/banner`, `POST /api/pulse/synth` — plus the pane's own
  Plotly purge + re-render at +250 ms, which also shifted the panel scroll.

## The fix — three layers

**① Never re-commit a form that is already committing** (`app.js`, the
focusout handler). `InlineCommit.inFlight(form)` reads htmx's own
`.htmx-request` class **and** a `data-committing` marker maintained by
`htmx:beforeRequest` / `htmx:afterRequest`, so the guard survives htmx
bookkeeping changes. This removes the double commit at the source.

**② An htmx-owned form may never navigate natively** (armor, applies
app-wide). A document-level `submit` listener in the **bubble** phase — after
htmx's own handler — calls `preventDefault()` for any form carrying
`hx-post`/`hx-get`. When htmx issues the request it has already prevented the
default, so this only ever covers the *declined* case that produced the bug.
Guarded by `if (!window.htmx) return;` so a page without htmx keeps native
submission as its fallback.

**③ Focus, caret and panel scroll survive the re-render**
(`window.InlineCommit`). A commit records where focus must return to, and the
`htmx:afterSettle` on `#inspector-pane` restores it. Three modes:

* `key` — Enter: back to the **same** field (matched by the form's
  `dot_path`, else `data-param` — never by DOM index), caret preserved.
* `index` — Tab/click-away inside the pane: the n-th focusable of the
  re-rendered pane, i.e. exactly the tab stop the browser was heading for.
* `none` — focus left the pane: scroll is restored, focus is **not** yanked
  back.

Three invariants keep this from fighting the user:

* focus is only ever restored when the swap **dropped** it (`activeElement`
  is `<body>`) — focus the user has since moved is never stolen;
* a **click or tap cancels the pending restore outright** (`mousedown` /
  `touchstart`), so committing and then opening a *different* pulse can never
  drop focus into the new pulse's same-named field — otherwise the user could
  be typing into a parameter they never chose to edit. Ordering is safe:
  `mousedown` fires before the `focusout` that records a click-away commit,
  so the commit's own bookkeeping still wins;
* a wheel/PageUp/PageDown/Home/End abandons the **scroll** restore (focus
  restore still allowed).

The scroll is re-applied over `[0, 120, 300, 600, 1000] ms` because the pane
keeps growing as Plotly re-renders — a single write at settle time is clamped
to the not-yet-final `scrollHeight`.

**Refresh coalescing** (the churn, not the navigation): the post-commit
refreshes are **debounced, not removed** — `delay:400ms` on
`pulses-changed` (`_pulses.html`) and `delay:500ms` on `diagnostics-changed`
(both slots in `base.html`). htmx restarts a `delay:` countdown on every new
event, so a run of per-field commits refetches the row set once instead of
once per commit. Measured: a burst of 3 commits → **1** `GET /pulses`.

## Verification

Real browser (Edge), patched server, customer 17Q chip:

| check | result |
|---|---|
| V1 no "Leave site?" dialog on commit | pass |
| V2 no document request / page load on commit | pass (only a same-document `history.replaceState` from `_pulsesSyncUrl`) |
| V3 exactly one `POST /pulse/edit` per Enter | pass |
| V4 focus returns to the edited field | pass |
| V5 a second Enter, without re-clicking, commits again | pass |
| V6 Tab commits and lands on the natural next tab stop | pass |
| V7 panel scroll survives the re-render | pass |
| V8 burst of 3 commits ⇒ 1 table refetch | pass |
| V9 the value reaches the server | pass |

Form-flow regression sweep (the armor touches every htmx form): sidebar chip
**Load**, qubit-inspector inline edit, pulse **Duplicate**/**Rename**/
**Delete**, workspace **Add folder** — all still work, none navigates.

CI: `tests/pulses_commit_selfcheck.cjs` (19 checks, runs the real `app.js`
under jsdom) driven by `tests/test_pulses_commit.py`, which also pins the
guards at source level and the debounce modifiers in both templates. Full
gate on the canonical Windows env: **4,372 passed / 19 failed**, the failures
exactly the documented environmental baseline (18 + the known
`test_reader_survives_concurrent_os_replace` flake).

## Notes for future work

* The commit model is still "one field commit = full `#inspector-pane`
  re-render" (docs/40). Layer ③ makes it *feel* stable; a targeted row swap
  would remove the churn outright and is the natural next step.
* `hx-sync`'s silent **drop** of a duplicate request is a never-silent
  violation in general. It no longer bites here (there is no duplicate to
  drop), but any future surface that can legitimately fire two commits on one
  form should declare `hx-sync="this:queue last"` rather than rely on the
  default.
