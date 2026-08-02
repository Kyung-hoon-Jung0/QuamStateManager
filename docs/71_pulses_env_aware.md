# 71 — env-aware Pulses + uv/.venv env support (r15 feedback ③, 2026-08-02)

Three stacked changes (one branch each): ① uv/.venv env discovery + folder
input (this doc §1), ② the env-aware add-pulse flow (§2, branch
`feat/pulses-env-aware`), ③ the CZ-first two-step pair flow (§3, branch
`feat/cz-gate-first`). §2/§3 are appended when their branches land.

## §1 — uv/.venv folder support (`feat/env-venv-discovery`)

User report: uv-based labs could reach their env only by hand-typing the
interpreter FILE path into the wizard's custom box; nothing discovered venvs.

### Folder resolution — `config_generator.resolve_python_interpreter`

Accepts the interpreter file verbatim, a venv/conda FOLDER (tries
`Scripts/python.exe`, root `python.exe`, `bin/python` — OS-native layout
first), or a project folder holding a **`.venv`** (the uv convention; both
layouts under it). Pure stat, never spawns, `None` when nothing resolves.
Wired into BOTH `POST /generate/select-env` (resolves before the old is-file
gate; persists the resolved interpreter; 400 text names the accepted forms)
and `GET /generate/probe` (probes the resolved file; echoes `"resolved"` so
`useCustomEnv` selects the interpreter, not the folder). Downstream needed
nothing: `_site_packages_for` already handled `Scripts/` layouts and every
cache keys on the resolved interpreter path.

### Discovery — `config_generator.discover_uv_venvs`

qualibrate configs carry NO interpreter key (verified against the full parsed
key set), but every project's `[qualibrate.calibration_library] folder` points
into its calibration repo — and uv puts the venv at `<repo>/.venv`. So: for
each project from `qualibrate_config.list_projects()` (active first), take the
folder's `native` path and walk UP (itself + ≤4 ancestors) looking for
`.venv/pyvenv.cfg` with a resolvable interpreter. Per-interpreter dedupe,
dangling/None folders skipped, `Exception`-proof (discovery must never break
the wizard), READ-ONLY on the qualibrate tree (docs/55 doctrine).
`discover_envs()` appends these after the conda rows; every row now carries a
`kind` tag (`"conda"` / `"uv-venv"`) and the env list renders a small
"UV VENV" badge (`.gen-env-kind`).

Note (this machine, 2026-08-02): discovery legitimately finds 0 today —
none of the configured projects' repos currently has a `.venv` checked out;
the real uv venvs live in non-project repos and are reachable via the folder
input (verified: `D:\work_laptop\temp\iqcc` → its `.venv` interpreter). The
moment a project's repo is uv-synced, its env appears in the picker
unprompted — the customer deployment pattern this was built for.

### Pins

`tests/test_env_venv_discovery.py` — resolution (file/two layouts/nested
`.venv`/conda-root/missing), discovery (walk-up hit, depth bound, dangling
skip, one-repo dedupe, pyvenv.cfg required), `discover_envs` kind-tagging +
ordering, and the folder-accepting routes (select-env 200 echoes the resolved
`selected`; unresolvable 400 names the accepted forms; probe echoes
`resolved`).

Also in this branch: CLAUDE.md's test command switched to the Windows conda
`SNU_17Q` env (the WSL `qm_mng` env is obsolete per the 2026-08-02 directive)
with the known OS-environmental failures cataloged.
