# docs/232: SM's state never lives inside a conda env (and what one kept moves out)

2026-09-30, found while tracing docs/231 on the customer's `kriss_arbel` env.

## The defect

`default_instance_path()` decides where settings, working copies and history
live. It returned `None` ("a repo checkout: keep Flask's `instance/`") whenever
*any* `pyproject.toml` sat next to the package. In `site-packages` that is
supposed to be impossible. However, `dash-bootstrap-components` ships its own
`pyproject.toml` straight into `site-packages`. So in that env Flask derived:

```
D:\miniconda3\envs\kriss_arbel\var\quam_state_manager.web.app-instance
```

That env kept its own copy of everything SM remembers: project env choices,
dataset roots, the State History of the chip it opened, working copies. Every
other env shared the per-user folder
(`%LOCALAPPDATA%\QUAM State Manager`). Deleting or rebuilding the env would have
silently thrown that state away.

## The fix

`is_sm_checkout(pyproject)` is True only when the file's `[project].name` is
`quam-state-manager` in any spelling. `tomllib` reads it, with a regex fallback.
Any other `pyproject.toml` beside the package is ignored, and the per-user
folder is used.

## The move (`core/instance_migrate.py`)

On the first start with the fix, `default_instance_path()` carries each
env-local instance folder of *this* Python into the per-user folder, once.
When there is nothing to move it costs a single `stat`.

- **The per-user folder wins every conflict.** Every other env has been
  writing there all along.
  - `project_envs.json`: projects are merged and the user folder's entry wins.
  - `workspace_roots.json` and `project_dataset_roots.json`: unions.
  - `last_session.json`: recent paths are unioned; everything else is the
    user folder's.
  - `config_generator.json` (the selected env): the user folder's copy is
    kept.
- **Working copies, the agent folder and scheduler state:** anything the user
  folder lacks is copied. Nothing is overwritten.
- **History chip folders:** copied. A folder name already taken by another
  chip is copied under a free name, preferring the chip's declared display
  name, and the alias that routes the chip's `extras.chip_name` follows it.
  This is the customer's exact case: `history/quam_states` was arbel in the
  env's folder and KRISS_CZ (647 snapshots) in the user's.
- **History indexes:** copied through SQLite's backup API, so a
  write-ahead-log `index.sqlite` arrives whole.
- **Not moved:** caches (schema, probe, scan, story), live-process
  registrations and migration flags.
- **The env folder is never changed** except for one marker,
  `MOVED_TO_USER_FOLDER.json`, which records what moved and makes the move
  one-shot. The folder stays a complete backup.
- **Concurrent starts:** two SM processes starting together cannot both move
  (`.migrate.lock`, 10 min stale limit).

## Verified

- **Unit pins:** `tests/test_instance_move.py`, 14 tests. They cover:
  - the checkout test: SM's own file, dash's file, other spellings, and a
    nameless or missing file;
  - the installed-layout end-to-end;
  - merge precedence and the caches rule;
  - the env folder left byte-identical plus the marker, and the move running
    once;
  - a working copy the user folder already has;
  - a WAL-mode index arriving with all 500 rows;
  - two chips sharing one folder name staying two chips, driven through the
    real `HistoryManager`;
  - the concurrency lock.

  All 7 mutations were red: any pyproject counted as a checkout, no rename on
  collision, a plain file copy of SQLite, the env folder winning, no marker,
  caches moved, the lock ignored.
- **Real data, on copies:** the customer's env folder (57 MB) moved into a
  copy of their user folder (533 MB).
  - 4 settings merged, arbel's history went to `history/arbel`, and the
    working copy moved with its undo journal. No errors, and the env folder
    gained only the marker.
  - Before: arbel resolved to 30 snapshots in the env folder and 0 in the
    user folder. After: 30 under `history/arbel`. KRS 5Q: 647 before and
    after.
- **Real Chrome, same setup:** an SM laid out like the customer's install
  (dash's real `pyproject.toml` beside the package, `sys.prefix` = a copy of
  the env, the env's own `QUALIBRATE_CONFIG_FILE`) showed:
  - the arbel card still reads `env kriss_arbel ✓`;
  - arbel opens with no env prompt;
  - the Dataset box shows `arbel_20260929`;
  - State History lists all 30 snapshots;
  - no JS errors.

## Note on docs/231's commit message

`a1cf2f86` says "12,089 passed". The real total of its three shards was
3,924 + 3,715 + 4,294 = **11,933** passed. That was an addition error in the
message, not in the run.
