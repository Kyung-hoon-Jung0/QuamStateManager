# 115 — The first hour (docs/110 #14 + the README half of #16)

*2026-08-11. docs/104 #14, user-approved in the docs/110 campaign. The
complaint is about the moment a new grad student opens SM: the mental
model that makes everything else safe lived on ONE swap-away pane, the
most common first action had no button, and the README taught neither.*

## What shipped

**`/help` — the manual has a permanent address.** The working-copy model
(live chip / working state / snapshot) and the feature tour lived only on
the landing; navigating anywhere destroyed them, exactly while the user
was forming that model. `/help` renders the **same shared fragment**
(`_landing_getting_started.html` — one source, so the two can never
drift) plus a full shortcut reference, and the sidebar links to it from
every page (beside Settings / Calculator, the docs/89 tools row).

**A real "Open a state folder" CTA.** The standalone path was a muted
sentence at the bottom of the landing while the WSL locate block led the
page. Both landings now carry a button row: *📁 Open a state folder…* and
*? How SM works — 2 min*. The locate block still leads for QUAlibrate
users — it answers a different question ("where is my config?").

**The tray teaches while the model is being formed.** The teaching
element was hidden until the user already had pending changes — i.e.
after they had learned by doing (or by accident). It now appears from the
first chip open: *edits live in your working state; the live chip changes
only when you press Apply to live, and that is reversible* + a link to
`/help`. Dismissible once (`quam_tray_teach_done`), then never again.

**README teaches the model.** A new *How it works — the one thing to know
first* section (the three objects, the one rule, and the three
consequences a user actually meets: cross-save Ctrl+Z, the drift
question, one-click Apply-to-chip from a run) plus *A typical session* —
the six-step path from opening a chip to tracing what changed.

## Deferred, with the reason

**Screenshots in the README.** The finding asked for them, and every
screenshot SM can currently produce is of a real customer chip (qubit
names, frequencies, cluster hosts). Publishing those in a public README
would leak exactly what the repo scrub exists to prevent. This needs a
synthetic-chip capture pass first (the autofit sim chip is a candidate
source), so it is deferred rather than done badly.

Pinned by `tests/test_onboarding.py` (6): `/help` serves the model + the
shortcuts and is reachable from every page (and answers HTMX with a
fragment), the landing carries the CTA, the tray carries the teaching
line naming both Apply-to-live and its revert, and the dismissal is
localStorage-gated.
