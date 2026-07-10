# 41 · Scheduler — guided browser walk-through (verification)

A hands-on checklist to verify the **Experiment Scheduler** end-to-end in the browser. Everything
up to now is logic- and test-verified (1654 pass / 96 skip / 0 fail) but **never clicked through**.
Work top-to-bottom. Each step has an **action**, the **expected** result, and a **☐** to tick.

> Legend: **[safe]** = no hardware touched. **[LIVE]** = drives the real OPX/chip — only do these
> when you actually intend to run on hardware (i.e. the real overnight use). You can verify
> everything except the actual node execution with **[safe]** + a **Dry run**.

---

## 0. Prerequisites

| | |
|---|---|
| **App env** | `qm_mng` runs the Flask app (no QM stack needed for the UI itself). |
| **Run env** | `LabB` (Windows conda) — the interpreter that actually runs the experiments. It has `qualang_tools`/`quam_builder`/`quam`/`qm` and its qualibrate config points at the right project. |
| **Calibrations folder** | `<qualibration-graphs>/superconducting\calibrations\1Q_2Q_calibrations` (WSL: `<qualibration-graphs>/superconducting/calibrations/1Q_2Q_calibrations`). |
| **A chip open** | Load a `quam_state` folder in the sidebar first (the one the experiments target). |

**Start the dev server** (non-default port so stale servers don't collide):

```bash
conda run -n qm_mng python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5070)"
```

Open **http://127.0.0.1:5070/** → load your chip → click **Scheduler** in the left nav.

---

## Part A — Setup & Verify  **[safe]**

### A1 · Environment
- **Action:** In **1 · Environment**, the env list loads; pick **LabB** (or paste its `python.exe`
  path) and click **Probe**.
- **Expected:** the picked env row shows a green **usable** badge; the probe line lists the QM
  stack as found. ☐

### A2 · Calibrations folder
- **Action:** In **2 · Calibrations folder**, browse/paste the `1Q_2Q_calibrations` path.
- **Expected:** the field accepts it (used by Scan in section 6). ☐

### A3 · Chip
- **Action:** In **3 · Chip (quam_state)**, click **Use open chip** (or paste the path).
- **Expected:** the field fills with the open chip's `quam_state` folder; the "Open chip:" line
  matches it. ☐

### A4 · Read effective config  **[safe]**
- **Action:** In **5 · Verify**, click **Read effective config**.
- **Expected:** a JSON read-out of the env's *project-merged* qualibrate config — confirm
  `quam.state_path` and `storage.location` point where you expect (this is read via the env's
  own resolvers, not raw TOML). ☐

### A5 · Pre-flight (all green)  **[safe]**
- **Action:** Click **Run pre-flight**.
- **Expected:** the checklist renders; with a matching chip + good env it's all **pass** (a
  couple of **warn** rows are fine — warn never blocks). The identity/path/library/storage rows
  read sensibly. ☐

### A6 · Pre-flight catches a mismatch  **[safe]**  *(the Strict gate)*
- **Action:** Temporarily put a *wrong* path in **3 · Chip** (or close the chip) and **Run
  pre-flight** again.
- **Expected:** the offending check goes **fail** (e.g. "quam_state matches the open chip" / "A
  QUAM chip is open"). ☐
- **Then restore the correct chip path** before continuing.

---

## Part B — Build the queue & Dry-run  **[safe]**

### B1 · Scan the folder
- **Action:** In **6 · Experiments**, click **Scan folder**.
- **Expected:** the library lists nodes + graphs (e.g. `1Q_08_…`, `1Q_999_…`). Each row shows
  its kind (node/graph) and whether it has the `custom_param` hook. The **filter** box narrows
  the list as you type. ☐

### B2 · Load parameter forms  **[safe]**
- **Action:** Click **Load parameter forms** (runs a hardware-safe inspection scan in the env).
- **Expected:** after a moment, experiments expose their full parameter schema (used by the ⚙
  editor). No hardware is touched. ☐

### B3 · Add to queue
- **Action:** Add 2–3 experiments (a node and, if you like, a graph) to the queue (**7 · Queue**).
- **Expected:** rows appear with status **queued** in a **dense, aligned table** — `⠿` grip,
  enable checkbox, name (long names truncate with `…`; **hover shows the full name instantly**),
  a **targets chip**, status, and the action cluster. The cluster never overflows the right edge,
  and columns line up across rows. ☐

### B4 · Edit, target, reorder, expand
- **Action:** On a queued row: click the **targets chip** (shows `all active`) to edit it inline
  (type `q0, q1`, **Enter** saves, **Esc** cancels); open the **⚙ param editor** (tweak a value);
  **drag** the grip to reorder; open the **⋯ menu** and try **Duplicate**, **View log**, **Move
  up/down**, and **Expand per qubit/pair**.
- **Expected:** the chip updates to the typed targets; ⚙ edits persist (a `●N` badge appears);
  reorder sticks; the **⋯ menu closes on outside-click / Esc** and flips upward near the bottom of
  the page; expand fans the experiment into one-per-target (a **graph** expand warns it runs the
  *whole graph* per target). Inline actions are **⚙ ↗ ✕**; everything else lives in **⋯**. ☐

### B5 · Turn ON Dry run, then Start  **[safe]**
- **Action:** In **4 · Run options**, tick **Dry run (`simulate=True`)**. The **dry-note** by the
  Start button should indicate a dry/no-hardware run.
- **Action:** Click **▶ Start**. (A dry run with tmux mode off still warns it pauses if the tab
  closes — that's expected.)
- **Expected:**
  - The run starts; the **run-state line** shows `running — <node> [targets] · <elapsed> ·
    N/total`, and the **elapsed ticks** every ~1.5 s. ☐
  - The **top-bar badge** appears (`Running: <node>`, with a `done/total` count) and is visible
    from other pages too. ☐
  - The per-node **log** tail streams under the queue. ☐
  - Editing is **locked** while running (try Save / a field edit on a chip page → you get a
    "locked while the Scheduler is running" notice, no mutation). ☐

### B6 · Dry-run result attribution  **[safe]**  *(the ↗ fix)*
- **Action:** Let the dry run finish.
- **Expected:** a dry-run / no-output node does **NOT** get a green **↗** run→Datasets link
  (the link is attributed only to a *successful, output-producing* item). Rows end **done**. ☐

### B7 · Pause / Cancel / Clear
- **Action:** Start again, then **⏸ Pause** (finishes current, then pauses) and **⏹ Cancel**.
  Then **Clear finished**.
- **Expected:** Pause stops *after* the current node (lock stays on until it's reaped, then
  releases); Cancel stops the queue; Clear removes terminal rows. ☐

---

## Part C — LIVE overnight run  **[LIVE — real hardware]**

> Only when you actually intend to run on the chip. Do a final pre-flight first.

### C1 · Strict-start refusal + force  *(the preflight-on-Start fix)*
- **Action:** With Dry run **off**, deliberately leave one preflight check failing (e.g. a
  slightly wrong chip path) and click **▶ Start**.
- **Expected:** the run is **refused** — a dialog lists the failing checks and the queue does
  **not** start. Choosing **Start anyway** forces past it; cancelling leaves it stopped. ☐
- **Then fix the path so preflight is clean before the real run.**

### C2 · LIVE confirm
- **Action:** With a clean preflight and Dry run **off**, click **▶ Start**.
- **Expected:** a **⚠ LIVE RUN** confirm appears (counts the enabled experiments; warns about
  hardware; if tmux mode is off, warns the queue pauses when the tab/computer sleeps). ☐
- **Tip:** for a true unattended overnight run, tick **Keep running if the browser closes (tmux
  mode)** so a closed tab doesn't pause the queue.

### C3 · Live chip refresh during the run  *(integration)*
- **Action:** While a node runs and writes the chip, open **Chip Status** / **Explorer** in
  another tab.
- **Expected:** after each node is reaped, the chip view **refreshes itself once** (the worker
  reconciles + broadcasts; no manual reload), and your **unsaved edits are never clobbered** (if
  any, you'd see a `live_diverged` banner instead). ☐

### C4 · Datasets integration + ↗ link
- **Action:** After a node that produces a run finishes, open **Datasets** (kept live during the
  run).
- **Expected:** the new run appears in the table without a manual rescan; the corresponding
  queue row shows a **↗** that deep-links to **that** run's dataset page. ☐

### C5 · Failure-stop
- **Action:** (If one occurs, or contrive one) let a node fail with **On failure = stop**.
- **Expected:** the queue **pauses** on the failure; the run-state line shows the failure
  distinctly; the chip still got its post-node refresh *before* the lock released. ☐

### C6 · Heartbeat / browser-close
- **Action:** Close the browser tab mid-run with **tmux mode OFF**.
- **Expected:** within ~30 s the worker **finishes the current node, then pauses** (it won't
  start the next one). Re-open the tab → badge shows **paused**; **Start** resumes. ☐

---

## What each step proves (traceability)

| Step | Verifies |
|---|---|
| A5/A6, C1 | Strict pre-flight — advisory in Verify, **enforced** at Start (refuse + force) |
| B5 | live elapsed + current-node on row & badge; editing lock |
| B6, C4 | ↗ attribution — only a successful, newer run; correct deep link |
| B7, C5 | pause-after-current; failure-stop pauses + lock-through-refresh |
| C3 | worker-driven post-node chip reconcile (headless, nav-away safe) |
| C4 | DatasetStore stays live + rescans without racing the worker |
| C6 | heartbeat pause-on-disconnect |

## If something's off
- Note the **step number** + what you saw vs. expected.
- The per-node **log** (under the queue, and `/scheduler/log?id=<item>`) has the subprocess
  stdout/stderr.
- Server logs show the worker + the refresh hook (`scheduler refresh hook failed` etc.).
- Design + rationale: `docs/40_scheduler.md`.
