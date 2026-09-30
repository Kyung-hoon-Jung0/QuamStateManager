# docs/231: data folder, top bar, collapsed Apply, English-only UI (customer 2026-09-30)

Four reports on the 21-qubit arbel chip, all verified in real headless Chrome
on a copy of that chip.

## 1. The Dataset Load box showed another project's folder

**Symptom.** After opening the arbel project, the sidebar's Dataset Load box
still read `D:\work\dataset\SNU_1Q`.

**Cause.** The box is restored from browser `localStorage`, which holds the
last path typed in that browser. It was never synced to the open project. The
State Load box has had exactly that sync for the chip since the stale-path fix
(`window.__activePath`).

**Fix.**
- `_ctx` publishes `active_dataset_path`: the project scope's storage folder,
  only when it exists.
- `base.html` writes it into the box on every full render, and into
  `localStorage`.
- With no project, the remembered path stays, as before.
- The storage folder is read once per scope and kept on the context
  (`_scope_storage`), so a render never re-reads the TOMLs. An explicit
  project open drops that cached value.

**Aside: "No runs found under this folder yet".** The customer's scanner held
both runs by the time this was investigated (read-only check against their
server), and on a live root a new run showed in an open page within about
60 s (the sidebar poll interval), measured.

One real way to freeze the sidebar was found and fixed: the poll skipped
every tick while the filter box had focus, so a click into it, or on its
keyword chips, stopped all updates until the caret left. The poll now defers
only while typing (a keystroke in the last 5 s).

An earlier "FROZEN" result from my repro script was a wrong check: the
unfiltered tree lazy-loads its date groups, so run names are never in its
HTML. That result was retracted.

## 2. The top bar painted over itself

**Symptom.** In the Sync-Qualibrate frame, at the L text size, with a "Live
chip changed · 42 values" pill, "QUAM State Manager" was drawn over the ⌗
link and the pill. The same happened in Split view at about 700 px.

**Cause.** The left group is one nowrap row (sync-ux). Its items still carried
`min-width: 0` from the older wrapping bar, so an overcommitted row shrank
each item below its own text. The ladder that decides what gives way was
written as `@media` rules on the window width, while what has to fit is the
content. Measured before the fix: 41 to 58 px of overlap at 1150 and 1600 px.

**Fix.**
- Items keep their content width: `flex-shrink: 0`.
- `window.TopbarFit` (app.js) walks the ladder by measurement, as `tb-fit-N`
  classes on `.topbar`. It adds one step at a time while a row overflows, and
  restarts from 0 on every pass, so room is given back.
  - Steps 1–5 are the old media ladder.
  - Steps 6–10 go further: a narrower search box, the Versions chip hidden,
    "⚠ 8" without the word "issues", the search box reduced to its icon, the
    Agent dot hidden.
  - Step 11 is the floor: the two groups wrap together into two rows rather
    than overlap.
- Overflow is read from the items' own boxes, never `scrollWidth`, so an open
  popover does not count.
- It runs on load, resize, htmx settle, bar mutations and text-size or sidebar
  changes.
- Inside the Sync-Qualibrate frame the ⌗ link is hidden (`html.sm-in-frame`),
  since it opens the frame you are already in.
- The media queries remain as the no-script baseline.

**Measured in real Chrome.** Zero overlaps at every width from 560 to 1920 px,
at both the M and L text sizes, with an unapplied edit in the pill. 700 px
with L text is one clean row; 560–600 px is two rows.

## 3. Collapsing the bar (☰) hid Apply

**Symptom.** With the bar collapsed, only the Auto pill floated. An unapplied
edit had no Apply.

**Cause.** `html.topbar-hidden #pending-tray > :not(.state-status-badge):not(.auto-sync-wrap)
{display:none}` has specificity (1,3,1). The sync-ux rule meant to show the
control, `#pending-tray > .sync-control {display:inline-flex}`, has (1,2,1),
so it lost.

**Fix.** The older rule exempts `.sync-control` too. Real Chrome journey:
1. An unapplied edit, then ☰, then the floating card shows
   "● 1 unapplied edit ▾ | ↑ Apply 1 | ⚡ Auto".
2. Pressing Apply there applies ("In sync") and the bar stays collapsed.
3. After a reload the page is intact.

## 4. English-only UI

"한글은 전부 영어로, compact하게." Every Korean string that reaches the screen
is now compact English:
- the sync control's hover texts and action tooltips;
- the Auto-Sync panel's three explanations;
- the sync-panel gloss;
- the workbench "Show", multi-window and Auto-Sync conflict tooltips;
- the Live-Edit "live now" cell tip;
- the Agent-lock message.

Customer quotes in code comments are unchanged; they never render. The one
remaining Korean literal, a stopword list that filters users' own Korean notes
in `tag_vocab.py`, is never shown.

## Pins

`tests/test_cust_0930_ui.py` (10) plus `tests/topbar_fit_selfcheck.cjs` (12
assertions). Each was mutation-checked red, then green:
- the collapsed rule without `:not(.sync-control)`;
- the no-shrink rule removed;
- `active_dataset_path` forced to "";
- a Korean tooltip put back;
- the poll pausing on focus again;
- TopbarFit not giving steps back;
- TopbarFit reading `scrollWidth`.

Four existing tests that pinned the old wording, or the old rule verbatim,
were updated to the new contract: `test_agent_runs`, `test_autosync_merge`
(×2), `test_web::TestRound15ChromeHiding`.

## Found, not fixed: the instance folder inside a conda env

`default_instance_path()` treats *any* `pyproject.toml` next to the package as
"a repo checkout". In the customer's `kriss_arbel` env, the
`dash-bootstrap-components` wheel ships a `pyproject.toml` into
`site-packages`. SM therefore keeps its state in
`envs\kriss_arbel\var\quam_state_manager.web.app-instance` instead of the
per-user folder: a separate project-env memory, separate workspace roots and
separate history per env.

Fixing it moves where that env's state lives, which is a visible change for
the lab (the env picks and data roots made there would not follow), so it is
left for a decision.
