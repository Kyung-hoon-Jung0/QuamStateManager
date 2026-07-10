# 21: Multi-Chip Support

> Researchers often work with several physical chips on the same OPX
> setup. This doc explains how the app distinguishes one chip's history
> from another's, what happens when a workspace contains experiments from
> multiple chips, how the user is prompted to disambiguate corner cases,
> and how legacy histories from older keying schemes are migrated.

---

## The problem

Three signals tell you what chip a `state.json` belongs to. None alone
is sufficient:

| Signal | Source | Reliability | Limitation |
|---|---|---|---|
| **Network identifier** | `wiring.json["network"]` (host + cluster_name) | High | Same OPX cluster = same network even when chip is physically different |
| **Chip structure** | `state.json["qubits"]` + `qubit_pairs` (names + connectivity) | Medium | Qubit names can be renamed by the user |
| **Workspace data folder name** | `data/<chip_label>/<date>/...` segment | High **when present** | Only some workflows use the `data/` convention |

The system combines all three:

- **Wiring fingerprint** is the primary identity. Different host →
  definitely different chip.
- **Qubits + pairs** disambiguate same-host different-chip cases.
- **Data folder name** is the user's intentional grouping signal —
  used to disambiguate the rare case of two different chips on the
  same hardware setup with same qubit naming convention.

---

## `ChipFingerprint` and `align()`

`core/history.py:108`:

```python
@dataclass(frozen=True, slots=True)
class ChipFingerprint:
    network: tuple[tuple[str, Any], ...]   # filtered (host, cluster_name) pairs
    qubits:  frozenset[str]
    pairs:   frozenset[str]


def fingerprint_of(quam_state_path: str | Path) -> ChipFingerprint | None:
    """Read state.json + wiring.json and return a ChipFingerprint."""

def align(loaded: ChipFingerprint, candidate: ChipFingerprint) -> str:
    """Return one of: 'aligned' | 'renamed' | 'different_chip' | 'unknown'."""
```

Decision matrix:

| network | qubits + pairs | result |
|---|---|---|
| differ | (don't care) | `different_chip` |
| same | same | `aligned` |
| same | differ | `renamed` (same hardware, qubit labels diverged) |
| either is None | — | `unknown` |
| both networks empty | qubits same | `aligned` |
| both networks empty | qubits differ | `different_chip` |

---

## Chip-stable keying — `_key_for(quam_state_path)`

The history key derives from path structure but folds per-experiment
loads back to their chip name. `core/history.py:83`:

```python
_EXPERIMENT_PATTERN = re.compile(r"^#?\d+_.+_\d{6}$")
_DATE_PATTERN       = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def chip_name_for(quam_state_path: Path) -> str:
    """Recognise <workspace>/<chip>/<date>/#N_<exp>_HHMMSS/quam_state/
    and return the <chip> component. Falls back to parent.name otherwise."""
```

So loading either of these:

- `…/Example 1Q chip/quam_state/`
- `…/Example 1Q chip/2026-04-30/#34_qubit_spectroscopy_171213/quam_state/`

both produce key `ExampleChip_1Q` and write to `instance/history/ExampleChip_1Q/`.
Before this fix, per-experiment loads created a per-experiment-named
key like `_34_qubit_spectroscopy_171213`, which led to fragmentation
across hundreds of dirs.

---

## Chip selector UI

The Param History dashboard shows two chip groups:

```
┌─ Chip ─────────────────────────────────────────────────┐
│  ● ExampleChip_1Q · 2203                               │
└────────────────────────────────────────────────────────┘
▼ Other chip histories on disk (1) — not in your workspace
   ExampleChip_21Q  · 595 snapshots · latest 20260430_171230  · 21 qubits   [View history]
```

- **Active chips** = the currently-loaded chip key (always 1). Workspace
  data folder names are *not* surfaced here as separate chips even when
  the workspace has experiments from multiple chips — that role is
  filled by the alignment banner.
- **Archived chips** = chip dirs that exist on disk but aren't the
  loaded chip. Collapsed by default. Clicking "View history" navigates
  to `/param-history?chip_key=<key>` with `since=all` (since archived
  data is usually older than 7 days from a prior backfill).

`HistoryManager.list_chip_histories()` (`core/history.py:1238`) walks
`instance/history/*/index.sqlite`, skipping `pytest-*` and `Temp`
leftovers and any dir with an empty index.

When viewing a non-loaded chip, the route reads from the target chip's
SQLite via a synthetic path (`Path("/__chip_key__") / chip_key /
"quam_state"`) whose `_key_for` resolves to that key. The
"current value" overlay on each sparkline is suppressed because the
loaded chip doesn't match the displayed one.

---

## Workspace alignment scan

`HistoryManager.scan_workspace_alignment(quam_state_path, workspace)`
(`core/history.py:1149`) groups every workspace experiment entry by its
fingerprint relative to the loaded chip:

```python
{
    "loaded": {"chip": "ExampleChip_1Q", "fingerprint": ChipFingerprint(...)},
    "aligned":         [ExperimentEntry, ...],   # network + qubits + pairs match
    "renamed":         [ExperimentEntry, ...],   # network match, labels differ
    "different_chip":  {chip_label: [entries, ...]},
    "unknown":         [ExperimentEntry, ...],
    "counts": {"aligned": N, "renamed": N, "different_chip": N, "unknown": N, "total": N},
}
```

The dashboard surfaces this in a colored alignment banner above the
sparkline grid:

| State | When | Action |
|---|---|---|
| 🟢 Green | only `aligned` | none |
| 🟠 Orange | any `renamed` present | "Merge them anyway" button (re-runs backfill with `force_renamed=True`) |
| 🟡 Yellow | mix of aligned + different_chip | each "other chip" name is a clickable Switch link |
| 🔴 Red | no aligned, only different_chip | Switch button to the dominant other chip |
| ℹ️ Info | viewing non-loaded chip | "Viewing history for chip X · current load is Y · [Switch back]" |

---

## Live chip-swap auto-routing

When a researcher physically swaps chips in the same fridge and reuses
the same `quam_state` folder, the new content's fingerprint will diverge
from the existing chip dir's. `check_and_snapshot` detects this and
routes the new snapshot to a different dir.

`HistoryManager._resolve_snapshot_dir(loaded_path)` (`core/history.py:380`):

```
                    fingerprint_of(loaded_path)  ──────┐
                                                       ▼
   _key_for(loaded_path)  ─►  candidate_dir = root/<key>/
                                                       │
                  ┌────────────────────────────────────┤
                  │                                    │
       candidate empty?                       candidate has snapshots
                  │                                    │
                  ▼                                    ▼
        return candidate                  sample = newest snapshot's fp
                                                       │
                                          align(fp_now, sample) ?
                                                       │
                ┌──────────┬───────────────────────────┘
                │          │
            aligned      different
                │          │
                ▼          ▼
        return         find any other dir whose
        candidate      fingerprint matches fp_now
                                  │
                  ┌───────────────┴────────────────┐
                  │                                │
              found                             not found
                  │                                │
                  ▼                                ▼
         return matching         create new dir <key>_alt_<host>_<qcount>q
         (swap_to_existing)      (swap_to_new)
```

`SnapshotMeta` records the routing decision in `chip_swap_detected` when a
sync/apply snapshot runs, and the Param History dashboard shows an orange
banner pointing at the new dir with a Dismiss button. The banner state persists across renders via
`current_app.config["last_chip_swap"]` and is cleared by
`POST /param-history/dismiss-chip-swap`.

---

## Backfill: alignment + ambiguity prompts + auto-routing

`HistoryManager.backfill_from_workspace(...)` (`core/history.py:1281`)
ingests workspace experiments. The full decision tree:

| Bucket from alignment scan | Default behaviour |
|---|---|
| `aligned`, same `data_folder` as loaded path | Ingest into loaded chip's dir. |
| `aligned`, different `data_folder`, decision recorded as `"same"` | Ingest into loaded chip's dir. |
| `aligned`, different `data_folder`, decision recorded as `"different"` | Skip (counted as `skipped_decision_different`). User is expected to load the other chip and backfill separately. |
| `aligned`, different `data_folder`, no decision | **Defer** — surface in `pending_decisions`. UI shows a banner with "같은 chip — merge" / "다른 chip — 분리" buttons. |
| `renamed` (same hardware, label drift) | Skip unless `force_renamed=True`. The orange alignment banner has a "Merge them anyway" button that re-runs backfill with the flag. |
| `different_chip` | **Auto-route** to that chip's own dir (`instance/history/<chip_label>/`). Was: silently dropped. |
| `unknown` (entry's quam_state unreadable) | Skip silently. |

The backfill loop itself was extracted into
`HistoryManager._ingest_entries_into(target_dir, entries, …)`
(`core/history.py:1281`) so the same routine handles both the loaded
chip's group and each `different_chip` group, with proper progress
reporting offsets.

### Backfill report

```python
{
    "ingested":                  int,   # into loaded chip's dir
    "skipped_renamed":           int,   # renamed entries skipped (no force_renamed)
    "skipped_different":         int,   # post-routing skip count (usually 0)
    "skipped_unknown":           int,   # unreadable entries
    "skipped_duplicate":         int,   # content-hash dedup
    "skipped_pending_decision":  int,   # awaiting user prompt
    "skipped_decision_different":int,   # user said "different chip"
    "pending_decisions":         list[{data_folder, count, chip_key}],
    "other_chips": {chip_key: {"ingested": N, "skipped_duplicate": N}},
}
```

---

## `chip_decisions.json` — persisted ambiguity decisions

When the user clicks "같은 chip — merge" or "다른 chip — 분리" on a
pending-decision banner, `POST /param-history/decide` calls
`save_chip_decision(instance_path, chip_key, data_folder, decision)`,
which writes to `instance/chip_decisions.json`:

```json
{
  "ExampleChip_1Q::ExampleChip_21Q": "different",
  "ExampleChip_1Q::ExampleChip_2Q": "same"
}
```

Subsequent backfills consult this file via `load_chip_decisions(instance_path)`
and skip the prompt entirely for known pairs.

---

## Two migrations

The keying scheme has changed twice. To avoid stranding past data,
`web/app.py:create_app()` runs both migrations once on every startup,
each gated by its own flag.

### v1 — `migrate_legacy_histories(instance_path)`

For each legacy `instance/history/<key>/` whose key matches the
per-experiment pattern `^_\d+_.+_\d{6}$`, walks each snapshot's
`meta.json["source_path"]` and computes the proper chip name via
`chip_name_for(source_path)`. Snapshots get moved to the proper dir;
SQLite rows merged via ATTACH + INSERT OR IGNORE; the emptied legacy
dir is moved to `instance/history_legacy_backup/<key>/` for recovery.

Gated by `instance/migrated_v1.flag`.

### v2 — `migrate_legacy_histories_v2(instance_path)`

Older `backfill_from_workspace` had a bug where every ingested snapshot's
`meta.json["source_path"]` got stamped with the LOADED chip's path,
not the per-experiment entry's. v1 trusted that field, so it routed
many snapshots to the wrong chip dir.

v2 routes by **content fingerprint** instead of `source_path`. To keep
this affordable on workspaces with ~10⁴ snapshots, the migration builds a
single `{ChipFingerprint → chip_dir_name}` index up front via
`_build_fingerprint_index(history_root)`, then per-snapshot routing is
an O(1) lookup:

```python
fp_index = _build_fingerprint_index(history_root)  # one walk; ~N reads

for src_dir in source_dirs:
    for snap in src_dir.iterdir():
        fp = fingerprint_of(snap)
        target_key = fp_index.get(fp) or _synthesise_chip_key(fp)
        # … move snap into target_key/, merge SQLite rows …
```

Tie-breaker for the index: when the same fingerprint appears in multiple
chip dirs (the v1-bug condition this migration is *meant* to clean up),
the dir with the higher *purity ratio* (fp-count ÷ total snaps in that
dir) wins — secondarily by absolute count, finally by alphabetical name.
A clean `ExampleChip_21Q` (1/1 LabB snaps) outranks a polluted `ExampleChip_1Q` (1/2)
for the LabB fingerprint, which is the routing the bug used to get
backwards. Each snapshot in every chip dir is re-routed if its actual
content contradicts where it currently lives. Empty dirs are removed.

Gated by `instance/migrated_v2.flag`. Idempotent.

The buggy `backfill_from_workspace` was also fixed at the source — new
snapshots now record `source_path = src_state.resolve()` (the per-experiment
entry's path) so future migrations don't see the same poison.

---

## Files

| File | Role |
|---|---|
| `quam_state_manager/core/history.py` | All identity / alignment / chip-swap / decision / migration code |
| `quam_state_manager/web/app.py` | Wires both migrations into `create_app()`; runs `_purge_test_leftovers` for tmp-dir leftovers |
| `quam_state_manager/web/routes.py` | `/param-history` accepts `?chip_key`, computes alignment + chip selector, surfaces `last_chip_swap` and `pending_decisions`; `POST /param-history/decide` and `dismiss-chip-swap` |
| `quam_state_manager/web/templates/_param_history.html` | Chip selector row, archive `<details>` section, 4-state alignment banner, chip-swap banner, decision banner |
| `quam_state_manager/web/static/style.css` | `.chip-selector-row`, `.archive-row/.archive-item`, `.alignment-banner-{green,orange,yellow,red,info}`, `.chip-swap-banner`, `.chip-decision-banner` |
| `quam_state_manager/web/static/app.js` | `paramHistoryDecide`, `dismissChipSwap` |
| `tests/test_history.py` | `TestChipIdentity`, `TestChipFingerprint`, `TestWorkspaceAlignmentScan`, `TestChipSwapAutoRouting`, `TestChipDecisionsAndAmbiguity`, `TestLegacyMigration`, `TestMigrationV2Fingerprint`, `TestListChipHistories` |

---

## End-to-end user workflow

A typical multi-chip session:

1. Researcher loads `Example 1Q chip/quam_state` (the live state). Chip key
   becomes `ExampleChip_1Q`. Auto-restore on next launch will reactivate it.
2. Adds `Example 1Q chip/` as a workspace root. Auto-add wires the chip's
   experiment tree into the sidebar.
3. Clicks **Import from workspace ↻** in Param History. The progress
   bar climbs. 600 unique state snapshots get ingested into
   `instance/history/ExampleChip_1Q/`. Duplicates from the same calibration
   session are dedup'd via content hash.
4. Wants to look at ExampleChip_21Q data on the same OPX — adds the LabB
   workspace. The alignment banner becomes yellow and lists "ExampleChip_21Q
   (125)". The pending-decision banner appears asking whether ExampleChip_21Q
   is the same chip or different.
5. Clicks **다른 chip — 분리**. Backfill re-runs; ExampleChip_21Q's 125 entries
   skip silently. The decision is saved in `chip_decisions.json` so
   the prompt never re-appears.
6. Loads the actual LabB state at `superconducting/quam_state` →
   `_key_for` returns `superconducting`. Chip swap detected; new dir
   created. Backfilling the LabB workspace now ingests into
   `instance/history/superconducting/`.
7. Both chips browsable from the Param History chip selector. Each has
   its own snapshot stream, properties, and trigger breakdown.
