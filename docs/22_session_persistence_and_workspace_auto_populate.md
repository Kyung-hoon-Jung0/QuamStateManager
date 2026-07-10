# 22: Session Persistence + Workspace Auto-Populate

> What gets remembered between launches, where it lives on disk, and how
> the workspace tree fills itself in based on what the user has loaded.
> Also: test fixture isolation that prevents pytest runs from corrupting
> the user's real `instance/`.

---

## Persisted state at a glance

Three JSON files in `app.instance_path` (typically
`<project-root>\instance\`):

| File | What it remembers | Updated by |
|---|---|---|
| `workspace_roots.json` | List of folders the user added as workspace roots | `POST /workspace/add`, `POST /workspace/remove` |
| `last_session.json` | Last loaded `quam_state` path + recent paths + remove-from-workspace exclusions | `POST /load`, `POST /workspace/remove`, auto-restore self-heal |
| `chip_decisions.json` | User's persisted answers to chip-ambiguity prompts | `POST /param-history/decide` (see `21_multi_chip_support.md`) |

All three are gitignored via `instance/` in `.gitignore` so user data
never gets committed.

The browser also keeps a few `localStorage` keys (`quam_load_path`,
`quam_workspace_path`, `quam_theme`, `quam_split_sizes`, etc.) but those
are **secondary caches** — the server-side JSON files are the source
of truth. localStorage being lost (e.g. user opens a fresh browser
profile) just means the input field starts blank; the actual loaded
chip and workspace are restored regardless.

---

## `last_session.json` schema

```json
{
  "last_quam_state_path": "<quam-states>/example_chip_1q\\...\\quam_state",
  "recent_quam_state_paths": [
    "<quam-states>/example_chip_1q\\...\\quam_state",
    "<work-root>\\LabB\\...\\superconducting\\quam_state"
  ],
  "workspace_excluded": [
    "<work-root>\\Old_Chip"
  ]
}
```

- `last_quam_state_path`: the folder the user last successfully loaded
  via `POST /load`. Auto-restored on first request after each app start.
- `recent_quam_state_paths`: LRU list, capped at **10**, deduplicated by
  absolute path. Surfaced as a "▾" dropdown next to the Load button so
  the user can switch between chips with one click.
- `workspace_excluded`: paths the user explicitly removed from the
  workspace tree. Future `/load` calls do not re-auto-add a chip folder
  whose path is in this list, so the user's "remove" stays sticky.

---

## Auto-restore on first request

`web/routes.py` keeps three module-level one-shot flags:

```python
_workspace_loaded = False
_session_loaded   = False
_rehydrated       = False
```

A single `@bp.before_request` hook runs three idempotent steps the first
time any request arrives after server start:

1. **Workspace roots** — `_load_workspace_roots()` reads
   `workspace_roots.json` and re-registers each folder via
   `Workspace.add_root(...)`. Folders that no longer exist on disk are
   silently skipped.

2. **Last session** — `_load_session()` reads `last_session.json`. If
   `last_quam_state_path` exists and the folder is still readable,
   calls `_activate_quam(last_path)` so the dashboard opens already
   loaded into the user's chip. On `_activate_quam` failure (folder
   moved, files corrupted, etc.) the bad path is dropped via
   `_drop_bad_path()` and the file is rewritten without it.
   The auto-restored chip's chip folder is then handed to
   `_maybe_auto_add_workspace_root()` so the workspace tree picks up too.

3. **Rehydration** — `_rehydrate_workspace_from_recents()` walks
   `recent_quam_state_paths`, derives each chip folder via
   `_chip_folder_for(path)`, and auto-adds chip folders that exist on
   disk and aren't in `workspace_excluded`. This recovers a workspace
   that was wiped (e.g. by an older test run that leaked into the real
   instance) without forcing the user to re-load anything.

Together this means: **start the app → the dashboard opens with the
user's chip already loaded, the workspace tree populated, and Param
History ready** — even after wiping the browser's localStorage.

---

## Workspace auto-populate on Load

`POST /load` (`web/routes.py:445`) does three things on success:

```python
_activate_quam(folder)            # 1. activate as the loaded chip context
_remember_load_path(folder)       # 2. update last_session.json
_maybe_auto_add_workspace_root(folder)   # 3. add chip folder to workspace tree
```

`_chip_folder_for(quam_state_path)` returns the chip folder *only* when
the loaded path matches the per-experiment layout:

```
<workspace>/<chip>/<date>/#N_<exp>_HHMMSS/quam_state/
                  ^^^^^
                  this is the chip folder
```

For paths that don't match (e.g. `<chip>/quam_state/` standalone, or
test tmp paths that have no obvious chip folder), the auto-add is a
no-op — the chip folder is too ambiguous to derive safely. The user can
still add manually.

`_maybe_auto_add_workspace_root(path)` then:

1. Computes `chip_folder = _chip_folder_for(path)`. Returns if None or
   the folder doesn't exist.
2. Skips if the chip folder is already in `workspace.root_folders`.
3. Skips if the chip folder appears in `last_session.json["workspace_excluded"]`
   (user previously removed it; respect that choice).
4. Otherwise: `ws.add_root(chip_str)`, `_save_workspace_roots()`, and
   invalidate the cached `DatasetStore`.

The same helper is called from the auto-restore path so an auto-loaded
chip also auto-adds its folder.

---

## "Remove from workspace" remembers

`POST /workspace/remove` (`web/routes.py:1288`) was extended to record
the removed path in `last_session.json["workspace_excluded"]`. This way
a subsequent Load of any per-experiment path under that same chip won't
silently re-add the chip folder behind the user's back.

---

## Recent-paths dropdown next to Load

A small "▾" button next to the Load input toggles a dropdown populated
from `GET /api/recent-paths`. Clicking a row fills `#load-path-input`
and submits the load form via `htmx.trigger(form, 'submit')`. One-click
project switching, no need to retype paths.

The dropdown is implemented in pure CSS + ~40 lines of `app.js`
(`toggleRecentPaths`). Click-outside closes it.

---

## Test fixture isolation

Older test runs called `create_app(testing=True)` without overriding
the instance path. Flask's default fell back to
`<package_root>/instance/` — i.e. the **user's real** instance dir.
Every test that posted to `/load` or `/workspace/add` ended up writing
into the user's real `last_session.json` and `workspace_roots.json`.
The corrupted state surfaced as "I had things loaded yesterday but
this morning everything is empty/has random pytest paths".

Two layers of defence now (`web/app.py:38`):

```python
def create_app(*, testing: bool = False, instance_path: str | None = None) -> Flask:
    if testing and instance_path is None:
        # Defensive: never let testing=True share state with the real
        # instance/. Auto-allocate a one-shot tmp instance dir.
        import tempfile
        instance_path = tempfile.mkdtemp(prefix="quam_test_instance_")
    ...
```

1. If `testing=True` is set without an explicit `instance_path`, an OS
   tmp dir is allocated. Every test gets fresh isolated state.
2. The official test fixture (`tests/test_web.py:113`) passes
   `tmp_path / "_app_instance"` explicitly so existing tests using
   `client` / `loaded_client` are isolated regardless.

`_purge_test_leftovers(instance_path)` (`web/app.py:38`) runs on every
`create_app` and cleans three forms of leftover pollution:

- `instance/history/pytest-NN/` and `instance/history/Temp/` subdirs.
- Paths under the OS tempdir inside `workspace_roots.json`.
- Paths under the OS tempdir inside
  `last_session.json` (`last_quam_state_path` + `recent_quam_state_paths`).

Defensive: only paths under the system tempdir are touched, so a real
research data path is never mistakenly purged.

---

## Files

| File | Role |
|---|---|
| `quam_state_manager/web/routes.py` | `_session_file`, `_load_session`, `_save_session`, `_remember_load_path`, `_drop_bad_path`, `_chip_folder_for`, `_maybe_auto_add_workspace_root`, `_rehydrate_workspace_from_recents`, the extended `@before_request` hook, `GET /api/recent-paths`, `POST /workspace/remove` extension |
| `quam_state_manager/web/app.py` | `create_app` accepts `instance_path`, auto-allocates tmp dir for `testing=True`, runs `_purge_test_leftovers` on every start |
| `quam_state_manager/web/templates/base.html` | Recents dropdown button + container in the Load `path-input-group` |
| `quam_state_manager/web/static/app.js` | `toggleRecentPaths()`, click handler that fills + submits the load form |
| `quam_state_manager/web/static/style.css` | `.recents-dropdown`, `.recents-item`, `.recents-empty` styling (works in light + dark theme via `--recents-text` variable trick to escape Pico's button-scoped `--pico-color`) |
| `tests/test_web.py` | `TestSessionPersistence` (~6 tests), `TestWorkspaceAutoAdd` (~5 tests) |

---

## Tests

`TestSessionPersistence`:

- `test_session_persists_after_load` — `/load` → file written.
- `test_session_auto_restores_on_first_request` — pre-seed file → first
  request activates the chip, no "No state loaded" message.
- `test_session_handles_missing_folder` — pre-seed with non-existent
  path → app boots cleanly, file is pruned.
- `test_recent_paths_lru_cap_and_dedup` — 12 distinct paths → list
  capped at 10, newest first; loading an existing path bumps to head
  without duplicating.
- `test_api_recent_paths_endpoint` — JSON shape check.
- `test_recents_button_present_in_load_form` — UI button presence.

`TestWorkspaceAutoAdd`:

- `test_load_auto_adds_chip_folder_to_workspace` — per-experiment path
  → chip folder auto-added.
- `test_load_auto_add_skips_when_already_present` — no duplicate.
- `test_workspace_remove_blocks_reauto_add` — Remove records the
  exclusion; subsequent Load doesn't re-add.
- `test_rehydrate_adds_chip_folders_from_recents` — pre-seed recents
  with valid + missing paths → only valid ones rehydrate.
- `test_rehydrate_skips_excluded_paths` — excluded paths not re-added.

All tests pass against the isolated tmp instance — running the suite
no longer pollutes the user's real `instance/`.

---

## End-to-end user workflow

1. **First launch**: empty instance. User clicks the 📁 Browse button,
   picks a `quam_state` folder, hits **Load**.
2. The chip is activated. `last_session.json` and `recent_quam_state_paths`
   are updated. If the path matches the per-experiment layout, the chip
   folder is auto-added to the workspace tree.
3. **Closes the app, reopens later** (could be days later, could be a
   different browser):
   - The first request fires the `@before_request` hook.
   - Workspace roots restore from `workspace_roots.json`.
   - The last loaded chip auto-activates from `last_session.json`.
   - Rehydration walks recent paths and re-adds any chip folders that
     fell out of the workspace.
   - The dashboard opens with everything ready — no Load needed.
4. **Switches projects**: clicks the ▾ next to the Load input → picks
   a recent path → 1-click switch.
5. **Removes a workspace folder explicitly** (the X next to it in the
   sidebar): `workspace_excluded` records the path. Future per-experiment
   loads under that chip don't quietly re-add the folder.
