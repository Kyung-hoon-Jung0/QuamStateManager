# QUAM State Manager (QSM)

A desktop + web app to view, edit, compare and track the QUAM `state.json` /
`wiring.json` of a superconducting-qubit chip, with its experiment runs beside it.

### **[See what QSM can do &rarr;](https://kyung-hoon-jung0.github.io/QuamStateManager/)**

## Install

Close any running QSM first, then install into the environment that runs your experiments.

**conda**
```bash
conda activate <your-lab-env>
pip install "git+https://github.com/Kyung-hoon-Jung0/QuamStateManager.git@v1.5.4"
```

**uv** (inside your project folder)
```bash
uv pip install "git+https://github.com/Kyung-hoon-Jung0/QuamStateManager.git@v1.5.4"
# or record it in pyproject.toml:
uv add "quam-state-manager @ git+https://github.com/Kyung-hoon-Jung0/QuamStateManager.git@v1.5.4"
```

## Run

```bash
qsm browser          # conda / activated env
uv run qsm browser   # uv project
```

It opens `http://127.0.0.1:5050`. Point **State Load** at a `quam_state/` folder
(the one holding `state.json` + `wiring.json`). Nothing is written to your chip
until you press **Apply to live**.

## Only if needed

- `--force-reinstall` (pip) / `--reinstall` (uv): the same version is already
  installed, or a previous install broke.
- `--no-deps`: replace only QSM and leave the lab's packages alone (e.g. when an
  upgrade of `click` would break QUAlibrate).

```bash
pip install --force-reinstall --no-deps "git+https://github.com/Kyung-hoon-Jung0/QuamStateManager.git@v1.5.4"
uv pip install --reinstall --no-deps "git+https://github.com/Kyung-hoon-Jung0/QuamStateManager.git@v1.5.4"
```

| Problem | Fix |
|---|---|
| `invalid peer certificate: UnknownIssuer` / `CERTIFICATE_VERIFY_FAILED` (a proxy re-signs HTTPS) | uv: add `--native-tls`; pip: add `--trusted-host pypi.org --trusted-host files.pythonhosted.org` |
| `couldn't find remote ref refs/tags/vX.Y.Z` | that tag does not exist; pick one from the Tags page |
| `No module named quam_state_manager.web` | close QSM, delete any `~uam_state_manager*` folder in the env's `site-packages`, reinstall with `--force-reinstall` |
| `qsm` not found | wrong env: activate the lab env (or use `uv run qsm`) and reinstall |

---

## For developers

The safety model, the Agent cockpit, the CLI, where data lives, the architecture and
the build notes are in [docs/DEVELOPERS.md](docs/DEVELOPERS.md).

```bash
git clone https://github.com/Kyung-hoon-Jung0/QuamStateManager.git
cd QuamStateManager
pip install -e ".[dev]"

# Windows: PYTHONUTF8=1 is required (the node selfcheck drivers emit UTF-8)
python -m pytest tests/ -q
npm install && npm run selfcheck     # jsdom pins that drive the real shipped JS
```

`npm install` matters: without jsdom the `.cjs` selfchecks skip and look green.

## License

[MIT](LICENSE)
