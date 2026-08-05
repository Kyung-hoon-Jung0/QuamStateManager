# 78 — The type-anomaly alert that raises itself

*Status: shipped 2026-08-05. Branch `feat/type-baseline-popup`. Builds on
docs/56 (typed editing), the r14 stored-as-text visibility amendment, and
docs/77 (the one-click repair).*

## Why

SM already *detected* both ways a chip's types can go wrong:

* **values stored as TEXT** — an external regeneration writes `"0.13"` instead
  of `0.13` (r14: Explorer marks, a delta-gated banner, per-edit 409 offers),
* **env-schema mismatches** — the chip disagrees with the selected
  environment's dataclass schema (docs/56: the "Environment match" diagnostics
  domain).

But acting on either meant the user going and finding a button. docs/77 added
the batch repair; the remaining complaint was about *when* it is offered:

> "If it has to be fixed anyway, why do I have to go there myself? When SM
> notices, it should just show me — and let me choose auto-correct or fix it
> by hand."

There was also a real bug behind the complaint: the alarm banner had no
refresh trigger, so after a repair it kept advertising the old count until the
next full page load.

## What ships

### 1. The alert raises itself when NEW CONTENT lands

Not on a timer, and never on the user's own edits. The server arms a one-shot
flag at the three choke points where content the user did not type enters the
working copy:

| choke point (`web/routes.py`) | covers |
|---|---|
| `_activate_quam` (both cache-hit and slow path) | opening / switching a chip: `/load`, `/workspace/select`, `/qualibrate/open`, `/generate/load`, sidebar select |
| `_rebuild_after_working_copy_replaced` | all four callers: live pull (`/state/sync`), snapshot stage (incl. the tray's revert-last-apply), restore-live, dataset **Load State** |
| `_reconcile_cached_quam_ctx` (`RECONCILE_SYNCED` branch) | SM adopting an experiment's / qualibrate's write — the path users actually complain about |

`_arm_type_alarm(ctx, reason)` only records `{reason, at}`; it never opens
anything. `GET /type-alert` is what consumes it, and its gate order IS the
"don't nag" policy:

1. no chip / archive → 204
2. **not armed → 204** (this is why ordinary editing, rendering and polling can
   never prompt)
3. no live anomaly, or both signatures already dismissed → pop the flag, 204
4. the same set was already shown in this session → pop, 204
5. otherwise 200 + the dialog, flag popped → **at most once per content-entry
   event**

The client (`TypeAlert` in `app.js`) asks on `DOMContentLoaded` and on the
`diagnostics-changed` / `stateRestored` / `liveDriftChanged` events the app
already fires — **no new poller, no route changes, no live-file reads**. It
refuses to fetch while an input has focus, mid-drag, over another modal, or in
a background tab, retrying once after 3 s; because the server flag is only
consumed by a 200, deferring costs nothing.

### 2. Auto-correct is one click — and still never blind

The dialog the alert opens **is** the docs/77 repair dialog (same overlay, same
`/type-fix/apply`, same signature re-validation, same one-change-group undo),
with an alert header explaining what arrived and from where. So:

* popup appears → press **Convert N field(s)** → repaired. One click, as asked.
* nothing is written that the user did not see: the per-field proposal (path,
  what it holds now, what it becomes, resulting type) and the refused list with
  reasons are on screen.

A "counts-only, apply straight away" mode was deliberately rejected — it would
write N values of which the user saw three.

Other actions: **I'll fix them myself** (closes, jumps to the field in the Json
Tree View), **Review on Diagnostics** (when env mismatches exist), **Don't show
this again** (memo), and Cancel/Esc/backdrop which close **without** a memo —
closing is not dismissing, and the banner stays as the fallback surface.

### 3. The two classes are separate, and only one is repairable

`core/type_fix.py` gained the pure layer: `strnum_signature(paths)`,
`env_signature(findings)`, `env_items(...)`, `alert_summary(...)`.

* **Stored-as-text** — SM can repair. Signature is `sha1(sorted paths)[:16]`,
  byte-identical to r14's, so every dismissal already on disk stays valid.
  Deliberately NOT `plan_signature` (which folds in stored values): re-raising
  because `"0.13"` became `"0.14"` would nag about a set the user answered.
* **Env-schema mismatch** — SM reports, the user judges. **Never auto-fixed**:
  the library may simply have changed. Its signature is keyed on the aggregated
  identity `(kind, class, field, code)`, so the same defect appearing on a 21st
  qubit is not a new thing to say.

Dismissal (`<token>::typealarm` in `chip_name_prompts.json`) now carries both
`sig` and `env_sig` and is gated per class; a legacy record (no `env_sig`)
correctly lets the env class raise once.

The payload is computed once per `mutation_seq` in `_type_alarm_memo(ctx)` and
shared by the banner, the alert and the diagnostics card. The env half reads
the manifest already attached to the store — the request path never spawns a
probe. `editable` is False on an archive, which also closes an older doctrine
hole: a read-only archive used to be offered a repair it could not apply
(`/type-fix/plan` and `/type-fix/apply` now refuse it outright).

### 4. Diagnostics: a "Types & values" card

`_types_card_state(ctx)` + `templates/_diagnostics_types.html` +
`GET /diagnostics/types-card`, following the env-card conventions and
self-refreshing on `diagnostics-changed`. Row A is the repairable class with
the **Auto-correct N values…** button; row B lists the env mismatches with the
honest caption *"SM will not change these — decide which is right"* and a
per-item **Go to field**.

The per-row `Fix types…` button was removed from `_diagnostics_list.html`: it
repeated one whole-chip action on up to 100 rows, which is exactly the "just a
line in a long list" the user objected to.

Unlike the alert, the card is **not** delta-gated — a dismissed anomaly is
silenced as a *prompt*, never hidden from the *report*.

### 5. The stale banner is fixed

`#type-alarm-slot` gained `hx-get="/type-alarm/banner"` on
`diagnostics-changed` / `stateRestored` / `liveDriftChanged`. First paint stays
the server-side include (no flash, no extra request on load).

## Pins

* `tests/test_type_autofix.py` — `TestTheAlertPayload` (both classes; the
  dismiss-signature formula is unchanged; env signature ignores instance
  counts; archives get no plan), `TestTheContentEntryTrigger` (opening arms it;
  an edit or a plain render does not; archives are never armed),
  `TestTheAlertEndpoint` (one-shot; names its trigger; dismissed sets stay
  quiet; a NEW anomaly raises again), `TestArchivesAreNotOfferedRepair`,
  `TestTheBannerRefreshes`, `TestTheDiagnosticsCard`.
* `tests/type_alert_popup_selfcheck.cjs` (driver:
  `tests/test_gen_ux_selfchecks.py`) — the never-interrupt rules, one dialog
  per event, closing ≠ dismissing, and that auto-correct runs the docs/77 apply
  path with the plan signature.

## Not in this phase

Retaining the env schema as a **baseline** across version changes, and letting
the user teach SM *"in this environment that type is right now"* (an
env-scoped, class-field-scoped verdict store). Phase 1 stores no verdict state
and keys the env class on `(kind, class, field, code)` — exactly the key that
work will use — so nothing here has to be undone. See docs/79.
