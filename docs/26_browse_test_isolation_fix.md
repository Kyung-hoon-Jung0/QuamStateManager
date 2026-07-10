# 26: Flaky `/browse` Test — Shared pytest tmp dir vs the `[:50]` Cap

> A project audit (2026-05-16) found `test_browse_parent_of_quam_state`
> passing in isolation but failing in a full-suite run. Root cause was a
> test-isolation defect, not a product bug. This doc records the
> diagnosis and the one-test fix so the same false alarm isn't chased
> again.
>
> **Read this if you are:** debugging a "this test only fails in the full
> suite" report, or touching the `/browse` directory-listing endpoint.

---

## Symptom

```
python -m pytest tests/ -q
→ FAILED tests/test_web.py::TestBrowse::test_browse_parent_of_quam_state

python -m pytest tests/test_web.py::TestBrowse::test_browse_parent_of_quam_state -q
→ 1 passed
```

Passes alone, fails in the full suite. Classic test-pollution signature.

## Root cause

`synth_folder` (`tests/test_web.py:106`) is the pytest `tmp_path` itself —
so `synth_folder.parent` is the **shared** per-run pytest directory
(`pytest-of-<user>/pytest-NN/`).

The old test browsed that shared parent and asserted its own folder
appeared in the listing:

```python
parent = synth_folder.parent          # = pytest-NN/, shared by ALL tests
resp = client.get(f"/browse?path={parent}")
assert synth_folder.name in folder_names
```

In a full-suite run, every test that uses `tmp_path` drops a sibling
folder into `pytest-NN/` — hundreds of them. The `/browse` endpoint
(`web/routes.py:1451`) intentionally caps its result:

```python
"dirs": children[:50],
```

`children` is sorted alphabetically. `test_browse_parent_of_quam_sta0`
sorts well past the first 50 sibling `test_*` folders, so it is
truncated out of the response and the assertion fails.

The `[:50]` cap is **correct production behavior** — the folder browser
must not ship thousands of directory entries to the client. The test was
the defect: it depended on uncontrolled shared state.

## Fix

`test_browse_parent_of_quam_state` now builds an **isolated** parent
directory it fully controls, instead of browsing the shared pytest tmp
dir:

```python
def test_browse_parent_of_quam_state(self, client, tmp_path):
    parent = tmp_path / "browse_root"
    quam = parent / "my_quam_state"
    quam.mkdir(parents=True)
    (quam / "state.json").write_text("{}", encoding="utf-8")
    (quam / "wiring.json").write_text("{}", encoding="utf-8")
    resp = client.get(f"/browse?path={parent}")
    data = resp.get_json()
    assert resp.status_code == 200
    folder_names = [d.split("\\")[-1].split("/")[-1] for d in data["dirs"]]
    assert "my_quam_state" in folder_names
```

`parent` contains exactly one child, so the `[:50]` cap can never hide
it. The test still verifies the same thing — browsing the parent of a
`quam_state` folder lists that folder.

No product code changed.

## Files changed

| File | What |
|---|---|
| `tests/test_web.py` | `test_browse_parent_of_quam_state` rewritten to use an isolated `tmp_path` subtree instead of `synth_folder.parent`. |

## How to verify

```bash
python -m pytest tests/ -q
→ 771 passed, 1 skipped
```

The full suite is green and order-independent again.

## Related deferred work (audit 2026-05-16)

The same audit confirmed every feature branch is merged into `main` and
no `TODO`/`FIXME` markers remain in `quam_state_manager/`. Outstanding
items are all intentionally deferred and gated on scale:

- Param History Phase 2 / Phase 3 — see `23_param_history_performance.md`.
- Slow-route loader for Compare / Trend Tracker — see
  `24_param_history_ux_polish.md`.
- Dataset bookmark-column sort, scroll-position persistence — see
  `25_dataset_virtual_scroll.md`.
- Full Compare `data.json`/`node.json` merge, Trend Tracker multi-property
  overlay — see `16_advanced_ui_features.md` (Known limitations).
