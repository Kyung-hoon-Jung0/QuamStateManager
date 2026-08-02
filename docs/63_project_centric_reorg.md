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
  **unique** match wins; else scope = `None` (never guess). *(Amended by the
  2026-07-28 audit:)* the shipped explanation surface is the info-level
  `state_path_shared` **Doctor finding** (which mirrors `storage_shared` but
  groups by `path_match.fs_key`, so case/spelling variants of one folder
  cluster exactly like the scope engine's `same_folder` match) plus the
  explicit-Open pin; an *in-context* "N projects use this chip" banner on
  the ambiguous chip itself is deferred follow-up, not shipped.
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
  snapshot's **source path**, never from the *globally active* context
  (background snapshots record non-active chips). *(Amended by the
  2026-07-28 audit:)* `_scope_for(path, ctx)` is **memo-first**: when the
  call site holds the context that OWNS the source path, that context's
  memoized scope (an explicit Open pin included) is the stamp — it is the
  session truth the headers show, and the pure reverse match can never
  recover a pin on a shared state_path (it abstains with None, so header
  and stamp would contradict). The reverse index stays the fallback for
  ctx-less paths. Lives in `meta.json` only (the `_SNAPSHOT_META_FIELDS`
  filter makes it bidirectionally version-safe); no SQLite schema change.

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

Verification: `tests/test_project_scope.py` (TestScopeDerivation/Pinning/
ReverseIndex/SidebarReorg/HistoryLens/DatasetLens/TrayBadge/Landing) + the
extended read-only doctrine pin in `tests/test_qualibrate_routes.py` +
full-suite run.

## §A — Pre-release adversarial audit (2026-07-28)

Four parallel review agents (scope core+landing / history lens /
datasets+trends lens / config+badge+doctrine+packaging) swept the whole
branch diff; every finding was hand-verified against the code before fixing.
Nothing rose to data-loss level. Fixed in the audit batch:

1. **P1 — `/qualibrate/open` pin cross-wire.** The explicit pin used to bind
   to `_active_ctx()` re-read *after* the storage scan (seconds on a network
   mount); a concurrent `/load` in that window would pin the WRONG chip to
   the project. `_activate_quam` now **returns the activated context** and
   the pin binds to it immediately (the `_active_wc_lock` bug class).
2. **Pinned scope now reaches snapshot stamps** — the memo-first
   `_scope_for(path, ctx)` amendment above (header truth == stamp truth on a
   shared state_path). Pinned by
   `TestScopePinning::test_pin_reaches_snapshot_stamps`.
3. **Scope acquisition moved BEFORE context publication** in both
   `_activate_quam` exits: a concurrent `/datasets` render could observe an
   active scoped chip without its memo → transient `data-scope=""` →
   one-time wipe of the user's folder-filter toggles.
4. **`tray_status` cache rebuild made atomic** (single immutable entry
   tuple, no `clear()/update()/read-back`): two racing first hits on `GET /`
   (pywebview window + workbench iframe) could KeyError — and `home()` is
   the first unguarded caller.
5. **Re-opening an already-scoped project now refreshes `last_project`**
   (the memo-equality guard used to skip it → stale "Continue" highlight).
6. **Type-corrupt `last_session.json` degrades instead of 500ing** — path
   keys are sanitized in `_load_session` (both `GET /` and the
   before_request hook `Path()` them).
7. **Embedded-NUL hardening**: `path_match.same_folder`/`fs_key` and lint's
   sibling-suggestion `iterdir` now catch `ValueError` (POSIX raises it, not
   OSError, for NUL bytes — reachable via a mangled-but-valid-JSON memo or a
   TOML `\u0000` escape; was a render-500 class on the landing and scoped
   Datasets on macOS/Linux).
8. **`state_path_shared` groups by `fs_key`** (see the amended ambiguity
   bullet) so the Doctor hint fires exactly when the scope engine abstains.
9. **The qualibrate/scope test files now genuinely run on native Windows**:
   fixture paths are interpolated `as_posix()` (backslashes are escapes in
   TOML basic strings — raw `WindowsPath` made the configs unparseable, 26
   tests hard-failed and the None-expecting ones passed vacuously).
   `annotate_snapshot` project-survival and corrupt-session landing pins
   added alongside.

Reviewed and **accepted as-is** (documented, not bugs to fix now): the
instance-file memos (`last_session.json`, `project_dataset_roots.json`) are
lockless read-modify-write like their pre-existing siblings — last writer
wins, seed-only data, self-healing on the next Open; `fs_key` does not
bridge Windows↔WSL dialects (recording is dialect-consistent per OS;
mixed-dialect is the dev harness only — degrade is "no seed", never wrong
data); project-roots entries for deleted projects are filtered naturally by
the present-folder intersection (no GC pass); the trends scope hint shows on
full cover too (honest there — trends' default is first-folder-only).

## §B — Config-location picker + WSL bridge (2026-07-28, customer feedback)

"No config found" is NOT the same as "not a qualibrate user": Windows and
Linux keep `~/.qualibrate` in different homes, and the common split
deployment — **qualibrate inside a WSL distro, SM native Windows** — puts the
config somewhere SM's default never looks. The landing used to fall silently
back to the standalone welcome; now the user can point SM at the folder.

**Resolution ladder** (first hit wins): `QUALIBRATE_CONFIG_FILE` env →
`QUALIBRATE_CONFIG_DIR` env (legacy alias) → **the UI-chosen override**
(`qualibrate_config.set_dir_override`, persisted in
`instance/qualibrate_location.json`, installed at `create_app`) →
`~/.qualibrate`. Env stays above the choice deliberately: an environment
variable is deployment-level intent, and the test suite's isolation relies
on it winning; `/qualibrate/use-location` refuses (with an explanation) when
an env var pins the location. `config_source()` / `list_projects()["source"]
== "sm-override"` surface the provenance; the `/qualibrate` topstrip gains a
"Config location…" details (change + reset-to-default).

**Surfaces**: the no-config welcome leads with the locate block — path input
(`~`, quoted copy-as-path, dir-or-`config.toml`, both slash dialects all
accepted by `_normalize_config_input`) + **Check** (READ-ONLY probe:
exists / has config.toml / N projects / active / version gate) + **Scan
common locations** (this user's home; every WSL distro's `/home/*` via
`\\wsl.localhost` from Windows; every `C:\Users\*` profile from WSL —
network-share stats, so scan is user-clicked only, never on a render path).
A `/home/…` path typed on Windows gets distro-anchored suggestions instead
of a guess. "Use this folder" persists the memo (instance-side ONLY — the
docs/55 doctrine is untouched, pinned by
`test_locate_never_touches_the_tree`) and `HX-Refresh`es; every cache keys
on the cfg dir, so the whole app flips atomically.

**WSL value bridge**: when the config itself is read from a distro share,
POSIX values OUTSIDE `/mnt` (`/home/u/chip`) live on that distro's own
filesystem — `native_path` now anchors them onto the same share
(`\\wsl.localhost\<distro>\home\u\chip`); `/mnt/<x>/…` still prefers the
direct drive (same bytes, faster I/O). Pure string work via
`_wsl_root_of(_config_dir())`; POSIX hosts ignore the root.

**safe_io P9 fallback** (found by this review — pre-existing, promoted to
mainstream by the picker): WSL's P9 file server does not implement
`ReplaceFileW` (WinError 50 `ERROR_NOT_SUPPORTED`, probed empirically), so
every apply-to-live/save onto a `\\wsl.localhost` live folder died with
`LiveFileError`. `_replace_into_place` now falls back to `os.replace` on
WinError 50/1 — the open-target `ACCESS_DENIED` weakness that fallback
re-introduces is exactly what the retry loop already absorbs, and Win32
share locks never mapped onto a Linux-side writer anyway. Verified
end-to-end on a real WSL-homed chip: adopt config → cards → open (~230 ms)
→ edit → save → apply-to-live over P9 (~60 ms, live bytes confirmed);
renders with a UNC config run ~10 ms warm (the tray's per-render existence
stat costs ~8 ms over P9 — accepted).

Tests: `tests/test_qualibrate_location.py` (precedence, bridge forms, P9
fallback, locate/use routes, landing block, restart persistence, read-only
pin).

## Amendment (2026-08-03, r16 ③ — dataset-roots question)

`_record_project_roots` no longer merges silently once a project HAS
recorded roots: a genuinely-new fs_key goes to
`instance/project_roots_pending.json` and the `#dataset-roots-banner`
(base.html, below the chip banner) asks — **Use only the new path**
(replaces the project's record; the old folder stays in the workspace /
under "All") / **Keep both** (the old merge) / **×** (decline memo: stop
asking for this project, merge silently from then on — including the roots
that were pending). First-time recording stays automatic, so
`TestDatasetLens`' open-records pin is unchanged. Route:
`POST /project-roots/confirm`; pins in
`tests/test_web.py::TestProjectRootsAskR16`.
