# 55 — QUAlibrate Projects Integration (`~/.qualibrate` in the SM)

**Status: PLAN (design record).** Branch `feat/qualibrate-folder`.

## 1. Context

Customer feedback: *"many users want to handle the `.qualibrate` folder in
our SM."* `~/.qualibrate` is QUAlibrate's per-user config root: a root
`config.toml` holding the ACTIVE `[quam].state_path` (the exact folder the SM
loads), `[qualibrate.storage].location` (the exact dataset root the SM
browses), `[qualibrate.calibration_library].folder`, plus `projects/<name>/`
partial-overlay configs — one per chip/campaign (the studied power-user has
15). Today SM users re-enter both paths by hand on every campaign switch,
and nothing surfaces config rot (the studied ACTIVE project's `state_path`
is dangling; 12/15 projects silently inherit one shared dataset root; 4
overlays are zero-byte).

A three-agent study (folder inventory / `qualibrate_config` 0.1.12 source /
SM surface map) + synthesis established the facts below; SM already ships
~80% of the read plumbing (`core/qualibrate_config.py`,
`scheduler.find_dataset_roots`, `/workbench/watch|match`).

## 2. The No-Conflict Doctrine (top design constraint)

QUAlibrate itself creates/edits these configs (CLI `qualibrate-config
project …`, the app's project UI). The SM must NEVER race it. Verified
behavior (qualibrate_config 0.1.12 / qualibrate 1.4.0):

- QUAlibrate's root-config write is **non-atomic** (`open("wb")` truncate in
  place), **unlocked**, comment-destroying (`tomllib` → `tomli_w`); the
  overlay `update_project` write is atomic (`.tmp` + replace).
- The app/runner/composite **cache config via `functools.lru_cache`** — an
  external file edit is INVISIBLE to running services until the runner's
  `POST /refresh_settings` or a restart, and the next in-app project switch
  **silently clobbers** any external edit (unlocked read-modify-write).
- Fresh processes (a new node run resolving storage at save time) see the
  new file immediately — a half-refreshed system is *inconsistent*, not
  just stale.

Therefore, the binding rules:

1. **MVP is 100 % read-only.** Every read tolerates a mid-write torn file
   (parse failure → short single retry for user-facing pages; polls keep the
   existing tolerant-`{}` behavior). Reads never take locks, never touch
   mtimes beyond `os.stat`.
2. **Any write goes through QUAlibrate's own API first** (`POST
   api/project/active` etc.) — QUAlibrate then performs its own rewrite and
   runner refresh, and SM inherits zero file-race liability.
3. **Direct file writes are a LAST-RESORT fallback**, allowed only when a
   liveness gate says the services are down (runner probe at
   `[qualibrate.runner].address` + log-mtime recency), behind an explicit
   confirm that names the risk, atomic (`safe_io` tmp+replace), targeted
   (change only the intended key, preserving the rest byte-for-byte),
   parse-verified before and after, with byte-level rollback. Never a side
   effect of `/load`, a poll, or startup.
4. **The root config is never rewritten by SM** (editing it changes the
   effective config of every inheriting project). Tier-3 repairs write ONLY
   per-project overlays, mirroring QUAlibrate's own atomic overlay writer.
5. **Version gate:** `[qualibrate].version == 5` and `[quam].version == 3`
   are the semantics this design is pinned to. Unknown/newer versions ⇒ SM
   degrades to read-only and says so.

## 3. UI/UX design

### 3.1 Sidebar (the user's idea, adopted + refined)

New left-sidebar main item **"QUAlibrate"** (icon ⚗ or the qualibrate mark),
placed under the workspace block. It follows the existing collapsible subnav
pattern (`#config-subnav`, Generate Config → Config Viewer):

```
  ⚗ QUAlibrate            ● IQCC_QRS_1Q     ← active project, live badge
    ├─ ▸ Projects (15)                      ← toggleable submenu
    │    ● IQCC_QRS_1Q   ⚠                  ← active; ⚠ = dangling state_path
    │    ○ KRISS          ✓                 ← healthy
    │    ○ KRISS_CR_2Q    ✓   [SM]          ← [SM] = currently loaded in SM
    │    ○ HQ2_CZ         ⚠                 ← lint issue (hover = why)
    │    …
```

- **Clicking a project row = "Open in SM"** (safe, zero external writes):
  loads its effective `state_path` as the chip + adds its
  `storage.location` to the workspace dataset roots. This is the daily
  action, so it gets the one-click spot.
- **Clicking the main "QUAlibrate" item** opens the **Project Config
  Manager** page (3.2).
- The active-project badge in the sidebar doubles as a mismatch warning:
  when SM's loaded chip ≠ the active project's resolved state_path, the
  badge turns amber ("SM is editing a different chip than QUAlibrate
  writes").

### 3.2 The Project Config Manager page (`/qualibrate`)

Table-first layout (brainstormed against a 3-pane alternative; the table won
because 15 projects × 3 paths is fundamentally tabular and the doctor
findings attach naturally to rows):

- **Top strip**: active project + effective values + liveness indicator
  ("runner reachable / logs active 11 d ago / services appear down") +
  config-root provenance (which env vars resolved it).
- **Projects table**: one row per project — name, active ●, effective
  `state_path` / `storage.location` / `calibration_library` each with an
  existence badge (✓/✗) and a **source chip** ("own" vs "inherited from
  root") so the overlay model becomes visible for the first time.
- **Detail pane** (row click): raw TOML viewer for the overlay + the root,
  side by side with the MERGED effective view (read-only in MVP; the
  "editor" of the customer ask arrives as Tier-3 guided actions, not a
  free-text TOML textarea — free-text editing of a file another app
  round-trips through `tomli_w` is a footgun we deliberately do not build).
- **Doctor panel**: the lint list — dangling paths (with sibling-folder
  suggestions), storage collisions ("12 projects share
  `dataset\KRISS_CR`"), empty overlays, orphaned dataset dirs, env-var
  mismatches. Each finding: severity + what it breaks + (Tier 3) a guided
  fix button.
- **Action buttons** (Tier 2+, hidden in MVP): "Set active in QUAlibrate"
  (REST-first), "Create project", per-row "Repair…".

**Two switch semantics, never conflated** (the key UX decision): *Open in
SM* (local, safe, instant) vs *Set active in QUAlibrate* (external write,
guarded). The former is the row click; the latter is an explicit button
inside the manager with its own confirm flow.

### 3.3 Chip-identity tray + Workbench

- The topbar tray gains "⚗ <active project>" beside the chip name; amber on
  mismatch, red when the active project's state_path is dangling.
- `/workbench/match` verdicts become project-aware: "QUAlibrate active
  project = P, resolved state_path = X (MISSING), SM chip = Y".

## 4. Phases

### Phase 1 — MVP (read-only: picker + badge + doctor)

`core/qualibrate_config.py` (extend the existing 126-line reader):
- **Env-var fix (verified live bug)**: honor QUAlibrate's real variables —
  `QUALIBRATE_CONFIG_FILE` (dir-**or**-file) and `QUAM_STATE_PATH` — before
  SM's legacy `QUALIBRATE_CONFIG_DIR`/`QUALIBRATE_STATE_PATH` aliases.
- `list_projects() -> [ProjectInfo]`, `active_project()`,
  `effective_config(name)` — merge fidelity per qualibrate_config 0.1.12:
  deep-merge overlay over root (`recursive_update_dict` semantics), 0-byte
  overlay = pure inheritor, **empty-string `state_path=""` is an explicit
  override**, overlay can never rename the active project, default storage
  `user_storage/${#/qualibrate/project}` with lazy template substitution.
- `lint(projects) -> findings` (doctor rules above).
- **Path dialect**: a `_native_path()` helper mapping `D:\…`/`d:\…` →
  `/mnt/d/…` when running under WSL (existence badges must never lie);
  pure-Windows/pure-Linux installs pass through.
- Torn-read policy: single 150 ms retry for page loads; polls unchanged.
- When a QM env is selected, prefer the authoritative
  `scheduler.read_effective_config` envelope (qualibrate's own resolvers);
  label the SM-side parse "approximate" otherwise.

Web: `GET /api/qualibrate/projects` (+ `/api/qualibrate/doctor`),
`/qualibrate` page (full + HTMX partial), sidebar item + subnav in
`base.html`, tray badge in `_pending_tray.html`, `/workbench/match`
enrichment. "Open in SM" = existing `_activate_quam` + `find_dataset_roots`
+ `_save_workspace_roots`; the project name is stashed in the ctx dict
(`project=<name>`) — a scope, **not** a new context type.

Tests: synthetic `.qualibrate` tree builder fixture (root + overlays incl.
0-byte, empty-string, dangling, collision cases — the studied folder's
anomaly set IS the fixture spec); merge-fidelity table tests; env-var
resolution tests; path-dialect tests; route/page smoke; sidebar JS
selfcheck. Real-folder test path-gated and **read-only**.

### Phase 2 — Switch active project (the one write)

- **Discovery**: probe for the app's REST base (config `[qualibrate.app]`
  key when present → well-known default port → user setting in SM). Open
  question flagged below.
- REST path: `POST api/project/active` → QUAlibrate rewrites its own root
  and refreshes the runner. SM then re-reads and refreshes its badge.
- File fallback (services down only): liveness gate (runner probe + log
  mtime), explicit confirm naming the staleness caveat, `safe_io` atomic
  write of a **targeted `project = "…"` line edit** (preserving all other
  bytes, incl. comments qualibrate itself would destroy), parse-verify,
  rollback snapshot, then best-effort `POST {runner}/refresh_settings`.
- Never offered while the liveness gate says "running" (REST only then).

### Phase 3 — Guided overlay repair (+ create project)

Overlay-only atomic writes (mirror `update_project`): fix dangling
`state_path` (sibling suggestion, always user-confirmed), "materialize
inherited `storage.location` into this overlay" (de-collides inheritors one
at a time), set calibration folder, create project (empty or from current).
Every write preceded by a dry-run effective-config diff across ALL projects.
Root config editing: **never** — "materialize into overlay" is the offered
alternative.

## 5. Open questions (to confirm with users/customers)

1. What does "handle" mean to them — viewer, switcher, or editor? (MVP is
   safe under any answer; ship order depends on it.)
2. Does SM run on the same host as QUAlibrate? (`.qualibrate` is per-user,
   per-machine; a remote story is out of scope here.)
3. App REST base-URL discovery — `[qualibrate.app]` is empty in the studied
   config; only the runner address is pinned. Which port does the app use
   in customer installs?
4. Are the shared-storage "collisions" sometimes deliberate (one dataset
   tree per campaign)? Repair UX presents, never auto-fixes.
5. Version drift: semantics pinned to qualibrate_config 0.1.12 (config v5 /
   quam v3); SM read-only-degrades on unknown versions.
