# 117 — Auto-apply: the moment the covenant changed (2026-08-12)

User request, after several labs asked for it:

> 유저들이 auto save mode를 넣어달라고 하네. … review탭처럼, **auto save 모드
> 일때**는, 유저가 값을 modify하고 그 셀을 나가기만 하면 **곧바로 live**에
> 적용이 되는거야. 훨씬 "공격적인 auto save"인 것이지. … 근데 그렇게 변경된 것이
> **실시간 변경 로그를 나열해주는 logger**가 바로 상단에 뜨면 정말 좋을 것
> 같거든? **심지어 그 logger에는 X버튼이 있어서 그것을 revert할 수 있는 기능까지
> 있는거야!!**

And, on the covenant: *"너 말이 맞아. SM의 철학을 변경할 순간이야."*

## What changed, exactly

`docs/107`'s covenant said: **any direct live write requires ≥ 1 explicit press
of Apply-to-live.** It now reads: **a direct live write happens only on an
explicit Apply press OR inside a user-enabled auto-apply session** (default OFF,
always visible while armed, auto-disarmed on conflict).

The floor did not move — one explicit user act still stands between an edit and
the chip. Its SCOPE moved: the act authorizes a session instead of a single
write. Everything the old sentence protected still holds: nothing writes live
silently, nothing forces over a chip that moved, every applied change is
revertible — now individually.

Amended in all three places that state it: `docs/107`, the `core/undo_journal.py`
module docstring (it quotes the covenant), and `README.md` (which promised the
chip is "never automatically" written).

## The decision that made it small

**Do not add a save+apply epilogue to `/field/edit` and `/field/edit-batch`.**
`POST /state/apply-to-live` stays the ONE thing in SM that writes the live
files; an armed session just presses it. That gives:

- the two most-pinned endpoints a **zero diff** — the manual path is unchanged
  by construction, not by test;
- no fourth copy of the save→push epilogue (there were already two);
- no server-side timer;
- and an amended sentence that is literally what the code does.

## The four moving parts

**The session** is `ctx["auto_apply"]` — armed_at, the session's one revert
anchor, a snapshot throttle, a flush count. It lives on the ctx, so it ends for
free on chip switch, LRU eviction and restart, and it is never written to disk:
an armed session must not outlive the window that shows it. Per the user's
choice there is **no idle timeout** — armed until turned off.

**The trigger** is one `MutationObserver` on the tray (`web/static/auto-apply.js`,
kept out of app.js so it is testable alone). Every commit path in the app already
ends in a `#pending-tray` swap, and there are three different mechanisms doing
it — `_swapPendingTray`'s hand-rolled `outerHTML` replace, declarative htmx
swaps, and OOB swaps from the inspector routes (which htmx announces on
`detail.elts`, **not** on the tray, so an event listener would miss them). The
observer sees all three and no existing file had to change.

Timing is the user's explicit choice: **flush immediately (0 ms), and coalesce
anything that arrives while a write is in flight into exactly one more.** A
single edit is never delayed; tabbing across ten rows cannot queue ten live
writes. It shares `window._applyInFlight` with the manual Apply, so the two can
never race.

**The applied log** is the undo journal (docs/107), labelled — not a second
store. Units gain an optional `meta` (`{"src": "auto"}`), so the log is already
per chip, already on disk (survives F5), already segmented one-row-per-user-
action, and already holds the `old`/`new` the revert needs. Cap 50 rows in the
tray, because the tray renders on every page.

**The ✕** is compare-and-swap, mirroring `autofit/writer.revert_patches`: the
unit's `new` must still be what the chip holds, or a later change to the same
path would be destroyed by a blind restore. The comparator moved to
`edit_policy.cas_equal` so the robot path and the user path can never drift
(bools deliberately never enter the numeric branch). A refusal writes nothing
and says which value moved. Like every other undo in SM the ✕ only STAGES, under
gid `alr:` — never `jrn:`, which routes `/undo` deeper and moves the journal
cursor — and the armed session flushes it through the same one door.

## Policies the user chose (and what they cost)

| Question | Choice | Consequence |
|---|---|---|
| Timing | immediate + coalesce | one write per burst; a single edit has no delay |
| ↺ Revert last apply | the whole **session** | ONE pre-apply snapshot per session instead of one per edit — which is also what stops a 10-minute session writing hundreds of MB of history. Per-change revert is the log's ✕ |
| A run in progress | **keep applying** | reported in the pill, never blocked (docs/86: a run going wrong is a reason to be editing). If the node and the user touch the same file, the next flush conflicts — loudly, without clobbering |
| Idle timeout | **none** | armed until turned off. The pill is deliberately coloured and breathing for exactly this reason |

Two of these override my recommendation (I proposed pausing during a run and a
10-minute idle disarm). Recorded here so the trade is visible: a forgotten armed
session plus a node writing the chip ends in a conflict, and the conflict path
disarms and shows the existing tray — the failure is loud and nothing is lost.

## Gates

**Cannot arm**: no live chip · dataset archive · read-only live folder · a chip
that has ALREADY diverged (arming into a guaranteed immediate conflict is a
trap, not a mode).

**Disarms itself**: a staleness conflict (with the existing conflict tray and its
honest choices) · a save/push `OSError` (the read-only message already exists) ·
any unexpected failure · chip switch / eviction / restart. Every disarm carries
`HX-Trigger: autoApplyDisarm`, and the client stops scheduling on it even if a
stale tray still carries the attribute for one render.

**Not gates**: the FSP-compensation and stored-as-text 409s keep their popups —
they fire before anything is staged, so nothing flushes, and they answer a
different question than consent.

## Honesty details worth keeping

- The docs/115 teaching line says edits stay private until you press Apply.
  While the mode is ON that sentence is FALSE, so it is replaced by the truth
  about the mode. A wrong explanation is worse than none.
- Under an armed session the per-apply success toast is suppressed — one toast
  per edit is noise no one can outrun. The applied log IS the feedback.
- A full page render gets its tray context from `_ctx()`, not `_render_tray`
  (base.html includes the partial directly), so BOTH stamp the session. Missing
  that made the pill vanish on every navigation while the mode was still on —
  caught in a real browser, then pinned. Same trap `mutation_seq` hit in
  docs/110.

## Verified

Server: `tests/test_auto_apply.py` (22) — arming and its gates, the flush
landing on live and being labelled, the manual path's `HX-Trigger` pinned
literally, `force=True` never reached from any auto path, conflict ⇒ disarm +
the edit still recoverable + a later resolve lands it, one anchor per session,
applied log ordering/per-chip isolation, and the ✕'s CAS (exact restore,
refusal with nothing written, float tolerance both ways).

Client: `tests/auto_apply_selfcheck.cjs` (13) under jsdom with the real
`auto-apply.js` — immediate flush, exactly one coalesced follow-up, zero writes
when disarmed or when nothing is pending, the outerHTML swap channel, the disarm
signal, the log toggle.

Real browser on a copy of a real chip (the customer's live files were never
touched): armed → typed into a grid cell → left the row → **the live
`state.json` on disk changed** → the log row appeared with its Δ → ✕ put the
value back on live and struck the row through → an out-of-band write to live →
the next edit refused, disarmed, and showed the conflict tray with the live
content intact.
