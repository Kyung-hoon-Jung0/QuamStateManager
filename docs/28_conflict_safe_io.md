# 28: Conflict-Safe Live-File Access — Working Copy + Armored I/O

> The State Manager edits a private *working copy* of `state.json` /
> `wiring.json`; the live files an experiment program is writing are touched
> only on an explicit user sync or apply. This stops the State Manager from
> intermittently breaking experiment-program saves.
>
> **Read this if you are:** working on file I/O, the live-change banner, the
> Save / Apply-to-live flow, or Param History snapshots.
>
> **Status:** v1 shipped on `feat/conflict-safe-io`; **hardened** by
> red-team Phase 1 follow-up (`safe_io.read_state_wiring` pair-read,
> narrowed `apply_to_live` TOCTOU, loud post-write errors) and Phase 2
> (`loader.py` + `scanner.py` + `Saver._atomic_write` routed through
> `safe_io`; LRU eviction in `_activate_quam` preserves the working
> folder on disk so unapplied edits survive).

---

## Why this exists

When the State Manager has a `quam_state` folder open, it shares those files
with the experiment programs (QM calibration nodes) that also write them. On
Windows this is a real conflict:

- A plain Python `open()` for reading does **not** grant `FILE_SHARE_DELETE`.
- While *any* process holds `state.json` open, an experiment's atomic
  `os.replace` save of that file fails with `ACCESS_DENIED`.

So the monitoring tool was intermittently **breaking the researcher's
experiment data write** — saves that "sometimes work, sometimes don't."

### What the platform actually allows (measured)

| Write operation | Succeeds while a reader holds the target open? |
|---|---|
| `os.replace` / `MoveFileExW` | **No** — fails regardless of the reader's share mode |
| `ReplaceFileW` | **Yes** |
| POSIX-semantics rename | Yes |

No share mode on the *reader* side can make an experiment's `os.replace`
tolerate our open handle. The only robust defence is to **not hold the live
file open** when an experiment might be writing it.

## The fix — two layers

### Layer 1 — `core/safe_io.py` (armored I/O)

The single chokepoint for all live-file content access. After the Phase 2
sweep, this means **every** module that opens `state.json` / `wiring.json` /
`node.json` — `loader.py`, `scanner.py`, `working_copy.py`, `Saver`,
`history.py` — flows through `safe_io`; there is no longer a "fast path" via
raw `open()` that bypasses the share-mode guard.

- **Reads** (`read_state_wiring`) open with `FILE_SHARE_DELETE` via
  `CreateFileW` and slurp the bytes in one shot before closing — the handle
  is held for microseconds, and the share flags let writers using
  `ReplaceFileW` / `DeleteFile` proceed. Transient errors (a mid-write read,
  an atomic-replace window) are retried.
- **Pair atomicity (Phase 1 follow-up).** `read_state_wiring` brackets the
  two reads with `os.stat` snapshots of both files: if either file's mtime
  drifts between the before-stat and the after-stat, we re-read. Without
  this, an experiment that atomically updates both files could hand us a
  *torn snapshot* (state.json from the new version, wiring.json from the
  old). The retry loop converges in ≤4 attempts for typical writers; if it
  doesn't settle, we surface a warning and return the latest pair (partial
  inconsistency is preferable to hanging).
- **Writes** (`write_state_wiring`, `atomic_write_json`) use `ReplaceFileW`
  on Windows — proven to succeed even while a reader is attached, unlike
  `os.replace`. `atomic_write_json` is the single chokepoint for atomic
  JSON writes, used by both the live-file apply and the working-copy save
  (`Saver._atomic_write` was unified onto this helper in Phase 2).

### Layer 2 — `core/working_copy.py` (the working copy)

The State Manager operates on a private copy under
`instance/working_state/<chip-key>/`:

- On load, the live files are copied once into the working copy.
- The `QuamStore` and `Saver` are built on the **working copy** — all
  browsing, editing and "Save" hit the copy, never the live files.
- Background change detection is `os.stat` mtime only — it never opens live
  content, so during normal monitoring there is **zero** contention.
- The live files' *content* is read only on an explicit **sync**, and written
  only on an explicit **apply to live**.

## The user flow

```
load chip ──► working copy seeded from live (one armored read)
                │
   edit ──► Save ──► working copy on disk          (live untouched)
                │
   live changes ──► mtime poll ──► "Review changes" banner
                │
   Review ──► GET /state/review : diff live vs working copy
                │
   Sync ──► POST /state/sync : pull live into the working copy
                │
   Apply ──► POST /state/apply-to-live : push the working copy to the live
             chip (blocked with a warning if live changed since the last
             sync; an explicit force overwrites)
```

## Routes

| Route | Purpose |
|---|---|
| `GET /api/topology-mtime` | `os.stat` poll; reports `changed` (live vs synced) |
| `GET /state/review` | armored read of live + `Differ` vs the working copy |
| `POST /state/sync` | pull live into the working copy; rebuild the store |
| `POST /state/apply-to-live` | push the working copy to live (staleness-guarded) |

## Save vs. Apply

"Save" persists edits to the working copy — always safe, never touches the
chip. The pending tray then shows **Apply to live chip**, which does the one
armored write to the real `state.json` / `wiring.json`. If the live files
changed since the last sync, Apply is blocked with a conflict warning and an
explicit "Apply anyway" force option (a full 3-way merge is out of scope).

## Param History

`history.check_and_snapshot` captures the state files through `safe_io`
(armored), so creating a snapshot never blocks an experiment. Snapshots of
the loaded chip are taken on **sync** and on **apply** — keyed by the live
path. The old 3-second background snapshot poll (`/api/topology/poll`) is
removed; experiment runs are still captured by the workspace backfill.

## Apply-to-live failure modes (Phase 1 follow-up)

`apply_to_live` is the one place where the State Manager actively writes
the live files. Three guarantees, each tied to a specific failure mode:

1. **Tightest possible TOCTOU.** A staleness check (`live_changed(wc)`)
   runs at the top of the function and again immediately before the
   write. Between the two, the only I/O is reading the working copy
   (never the live folder), so a writer that lands during that window
   is caught by the second check rather than getting silently
   overwritten.
2. **Post-write mtime read failure is loud.** After `write_state_wiring`,
   we re-stat the live folder to capture the mtimes our write produced.
   If that stat raises (folder vanished, permission flipped), we raise
   `LiveFileError` and leave `wc.synced_*` unchanged. Future
   `live_changed` checks will then flag the divergence rather than
   treat the partial-write state as authoritative.
3. **Meta write happens before in-memory mtime advance.** The post-apply
   meta sidecar is persisted via `safe_io.atomic_write_json` *before*
   `wc.synced_state_mtime` / `wc.synced_wiring_mtime` are mutated. If
   the meta write fails, the in-memory copy stays in sync with the
   on-disk meta; without this ordering, an `OSError` on meta write
   would leave the in-memory copy ahead of disk and the next session
   would re-prompt for a needless sync.

## LRU eviction preserves the working folder (Phase 2 fix)

`_activate_quam` keeps up to 10 in-memory contexts in `_quam_cache`.
When an 11th chip is loaded, the **oldest in-memory context is dropped**
but the on-disk working folder is intentionally preserved. A later
re-load of the evicted chip takes the `working_copy.load()` path
(rehydrating from the existing folder + meta sidecar) rather than the
`working_copy.create()` path (which would re-seed from live and
silently destroy any Save'd-but-not-yet-Applied edits). Before this
fix, the LRU eviction called `working_copy.discard()`, which `rm -rf`'d
the working folder — heavy users could lose hours of recalibration
work by switching between chips.

## Residual risk

- `safe_io` cannot make an experiment's `os.replace` immune to our handle.
  The working-copy design shrinks our live-file contact to load + manual
  sync + manual apply — rare, brief, sub-millisecond windows — instead of a
  content read every 3 seconds.
- Manual edits made directly to the live file between syncs are not
  auto-snapshotted (a consequence of the manual-sync model); experiment
  states are still caught by the workspace backfill.
- A post-write meta write failure raises `LiveFileError` but the live
  chip *has* received the new state. The user must investigate and
  retry; the in-memory copy is unchanged so the next apply is safe.

## Key files

| File | Role |
|---|---|
| `core/safe_io.py` | share-delete reads, `ReplaceFileW` writes, retry |
| `core/working_copy.py` | working-copy lifecycle: create / sync / apply |
| `web/routes.py` | `_activate_quam`, `/state/*`, `/api/topology-mtime` |
| `web/templates/_state_review.html` | live-vs-working diff overlay |
| `web/templates/_pending_tray.html` | Save + Apply-to-live controls |

## Phases

1. `core/safe_io.py` + concurrency tests.
2. `core/working_copy.py` + tests.
3. Wire the working copy into `_activate_quam`.
4. Review / sync / apply routes + UI.
5. Param History rewire + docs.
