<!-- Moved verbatim from root CLAUDE.md (section: Commands, block 8) on 2026-11-14. -->

**Run web dev server** (use a non-default port — stale dev servers stack up; serve from a Windows env, never WSL — broken loopback forwarding on this machine):
```bash
conda run -n LabD_17Q python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5050)"
```
