# 63 — Project-centric reorganization (the "project lens")

QUAlibrate always runs inside a **project**; the project already declares the
state path and the data storage. Users think project-first, but SM was
folder-first: state folders and dataset roots load independently, and every
identity surface (Param History chips, trends, topbar) derives from chip
fingerprints or path shapes. This release makes the **project the primary
organizing frame** — navigation, loading, Param/State History,
Datasets/trends — while independent (non-project) sources keep behaving
exactly as before, "displayed as-is".

## Locked decisions (user)

1. **Project lens, not isolation.** One shared underlying truth per chip; the
   project is the frame (labels, scoping, defaults) — never a data wall. The
   same chip under two projects shows the *same* history, titled per project.
   No storage migration of `instance/history/`.
2. **Full reorganization in one release** (nav + open flow + history lens +
   datasets lens + topbar identity + landing).
3. **Startup lands on the Projects page, clean.** Nothing auto-loads; the
   last project and last chip are one-click cards ("Continue" / "Resume").
4. **Read-only doctrine unchanged** (docs/55): SM writes NOTHING under
   `~/.qualibrate` — this release adds zero writes; the byte+mtime pin test
   in `tests/test_qualibrate_routes.py` is the contract and is *extended*
   here, not relaxed.

## The scope model

- **The scope is DERIVED, never stored-as-truth.** `_project_for_path(folder)`
  (web layer) reverse-matches a live folder against a **stat-cached**
  project → state_path index built from `qualibrate_config.list_projects()`
  (cache pattern mirrors `tray_status`: keyed on cfg-dir + root/overlay
  `mtime_ns`; steady state = a few `os.stat` calls, no TOML parsing).
- `ctx["qualibrate_project"]` is a **memo**: `POST /qualibrate/open` pins the
  explicitly chosen name; every other activation path (`/load`,
  `/workspace/select`, LRU-eviction rehydrates) re-derives at the
  `_activate_quam` tail. Eviction, rebuilds, reconciles and restarts all
  converge on the same answer by construction.
- **Ambiguity rule** (several projects can share one `state_path` via
  inheritance): the qualibrate-**active** matching project wins; else a
  **unique** match wins; else scope = `None` and the UI shows a
  "N projects use this chip" hint. A new info-level `state_path_shared`
  doctor finding mirrors `storage_shared`.
- **Scope = None ⇒ byte-identical legacy behavior** on every surface.

## Persistence (all under `instance/`, never `~/.qualibrate`)

- `last_session.json` gains `last_project` (string) — used ONLY to highlight
  the landing card; never auto-restored. Standalone loads do not clear it.
- **`project_dataset_roots.json`** (new file): `{ "<project>": ["<resolved
  root>", ...] }` — which dataset roots each project adopted via Open in SM.
  Deliberately a separate file: `workspace_roots.json` keeps its pinned
  bare-array format (external tooling contract), and its writer would
  otherwise silently drop unknown keys.
- `SnapshotMeta.project: str | None` — stamped on new snapshots from the
  snapshot's **source path** (via the reverse index), never from the active
  context (background snapshots record non-active chips). Lives in
  `meta.json` only (the `_SNAPSHOT_META_FIELDS` filter makes it
  bidirectionally version-safe); no SQLite schema change.

## Surfaces

- **Sidebar**: Projects block first (subnav server-rendered expanded +
  restore-registry entry so it stays open), then the State Load form (the
  explicit "otherwise" path), divider, Generate Config, chip nav, data nav,
  workspace tools. Command palette gains Projects.
- **Landing (`GET /`)**: with a qualibrate config → header strip + a *lazy*
  project-cards fragment (`GET /landing/projects`; keeps `/` cheap for the
  workbench iframe): per-project state/storage existence, doctor count,
  active dot, `[SM]` marker, Open form (disabled when dangling; 4xx bodies
  render inline), "Continue" highlight on `last_project`, "Resume <chip>"
  card from `last_quam_state_path`, one-click recents, standalone pointers.
  Without a config → the previous welcome, verbatim
  (`_landing_welcome.html`). The topbar brand links back to `/`.
- **Open flow**: `POST /qualibrate/open` now redirects to `/qubits` (matches
  `/workspace/select`, the sibling "open a chip" action). Plain `/load`
  keeps `/explorer`.
- **Param History**: header "Param History — <project>" when scoped; the
  loaded chip's selector chip displays "<project> · <chip_key>". **The key
  itself, `?chip_key=` URLs, `data-loaded-chip-key`, and `hist:<chip>/<ts>`
  compare refs are untouched** — display divergence is strictly one-way.
- **State History**: header suffix + a muted per-row project badge when the
  snapshot carries `project`. Display-only; no filtering (lens doctrine).
- **Datasets/Collections/Trends**: when scoped, the folder-chip filter
  *pre-selects* the project's recorded roots (intersection with the present
  folder set; seeding happens client-side only when the scope/folder-set
  identity changes, so user toggles stay authoritative within a scope); the
  All chip remains the escape and a muted "scoped to <project>" hint names
  the frame. Trends pre-selects multiple roots only when the same-chip gate
  passes. Dataset uids (`<folder_key>:<run_id>`) untouched.
- **Topbar ⚗ badge**: shows SM's own scope first (`⚗ <scope>`), with a muted
  `(qualibrate: <active>)` when the two differ. `dangling`/`match` semantics
  (vs the qualibrate-active project) are unchanged; a mere scope difference
  is never a warning color. All logic stays inside
  `_qualibrate_tray_badge()` so full renders and the OOB tray swaps can't
  diverge.

## Performance guarantees

- `_load_toml_retry` no longer sleeps 150 ms per **0-byte overlay** (a
  supported "pure inheritor"; a real config with 4 of them would have added
  ~0.6 s to every chip load).
- The reverse index never calls `lint()` (which stats/iterdirs configured
  paths, potentially on dead mounts) and parses TOML only when an mtime
  changed.
- `GET /` renders no project listing inline — the cards are a lazy fragment.
- The test suite is isolated from the developer's real `~/.qualibrate` by an
  autouse `QUALIBRATE_CONFIG_FILE` fixture in `tests/conftest.py`.

## Edge-case ledger

- **LRU eviction / restart**: scope re-derives on the next activation — no
  stored state to lose.
- **Project deleted from qualibrate while scoped**: the memo label persists
  harmlessly until the next activation (then derives to None); the Projects
  subnav/landing show a muted "no longer a qualibrate project" hint.
- **Two projects → one state_path**: ambiguity rule above; explicit Open
  pins the user's choice and survives cache-hit re-activations.
- **Windows ↔ WSL**: `native_path` now maps both directions
  (`D:\…` ↔ `/mnt/d/…`), so reverse-matching works when the config was
  written from the other side.
- **Project roots vanished/empty**: scope pre-selection is an intersection
  with the folders actually present — a stale root is a natural no-op.
- **Dataset-archive opens**: reverse-match simply misses (archives are not
  project state paths) — scope None, read-only behavior unchanged.

## Non-goals (explicit)

- No re-keying of `instance/history/` and no fix here for the pre-existing
  `chip_name_for` generic-folder bug ("data"/"quam_states" chip dirs) —
  follow-up work.
- No qualibrate writes of any kind: project switching (docs/55 Phase 2) and
  overlay repairs (Phase 3) remain designed-but-unimplemented.
- No per-project isolation of history or datasets (lens only).
- No changes to the `hist:` ref or dataset-uid formats.

## Implementation status (2026-07-27) — SHIPPED

All nine steps landed on `feat/project-centric-reorg`, one commit per step,
each independently green:

| Step | Commit | What |
|------|--------|------|
| 0 | `de83f65` | this design record |
| 1 | `abb6f66` | prerequisites: `_load_toml_retry` 0-byte skip, `native_path` WSL→Win inverse (`_to_native`), `state_path_shared` lint, `tests/conftest.py` env isolation |
| 2 | `114f044` | scope core: `project_state_paths()` stat-cached reverse index, `_project_for_path` (active→unique→None), `_acquire_project_scope` at both `_activate_quam` exits + explicit pin, `last_project` persistence, `_ctx()["project_scope"]` |
| 3 | `7fb29a1` | startup lands clean — `_ensure_workspace_loaded` keeps pruning/roots/rehydrate, drops only the auto-activation |
| 4 | `4d14433` | sidebar: Projects → State Load → Generate Config; subnav expanded + SUBNAVS-persisted; palette entry |
| 5 | `1b2c528` | project-first landing (`_landing_shell/_landing_projects/_landing_welcome`, lazy cards, Resume/Continue, 4xx inline); `/qualibrate/open` → `/qubits` |
| 6 | `db0cf09` | history lens: `SnapshotMeta.project` stamped from the snapshot's SOURCE path at all 11 call sites; scoped Param/State History headers, `<project> · <key>` selector, `sh-project-badge`; raw-key contracts pinned |
| 7 | `c3ec5db` | datasets/trends lens: `instance/project_dataset_roots.json` (+record/strip), `data-scope` + `#ds-scope-folders`, client seeding (proper subset, All escapes), Trends same-chip-gated pre-select |
| 8 | `f9eca41` | tray badge `sm_scope`: name = scope-or-active + muted `(qualibrate: <active>)` on differ; colors unchanged; scope-only badge never "dangling" |
| 9 | (this commit) | docs sweep (docs/10, docs/22, docs/55, CLAUDE.md), doctrine pin extended over a full scoped session, full suite |

Verification: `tests/test_project_scope.py` (45 tests across
TestScopeDerivation/Pinning/ReverseIndex/SidebarReorg/HistoryLens/
DatasetLens/TrayBadge/Landing) + the extended read-only doctrine pin in
`tests/test_qualibrate_routes.py` + full-suite run.
