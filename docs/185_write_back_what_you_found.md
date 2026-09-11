# docs/185 — One changed number rewrote all twelve thousand lines

2026-09-12. From the write-path stress round, on a byte copy of the customer's
original `state.json`.

## Measured

```
before: 434,839 bytes, indent = 2
  POST /load
  POST /field/edit   dot_path=qubits.q1.T1  value=0.0000299
  POST /state/apply-to-live
after:  548,877 bytes, indent = 4
```

**+26%, every one of ~12,000 lines rewritten, for one changed number.**

No *value* changed that a press did not ask for, so the covenant held. What
broke is everything outside SM: git, rsync, a backup, a file-watcher, a reader
using size as a heuristic. *"What did that press change"* becomes unanswerable
from the file itself — which is exactly the question a lab asks after an
automated calibration writes to their chip.

## Three things SM does not own

The indent was the visible 26%, but it is not the only one:

| | |
|---|---|
| **indent** | 2 vs 4 spaces, or tabs |
| **line endings** | the KRISS chip on this machine is **CRLF**, and `open(.., "w")` translates by *platform* — so the same SM rewrites the same file differently on Windows and on Linux |
| **trailing newline** | the KRISS chip has **none**; SM always appended one |

`safe_io.json_format_of(path)` reads all three off the file (8 KB of head, 4
bytes of tail), and `_write_tmp_json` writes them back. Best effort by
construction: unreadable, missing, or single-line (compact, no indent to copy)
all return `None`, and the caller then writes exactly what it writes today.

**A file that does not exist yet is written exactly as before** — indent 4,
trailing newline, platform line endings. That is every file SM owns on its
first write, so nothing of SM's own moves.

## The working copy needed its own line

The live write is a byte copy *of the working copy* (docs/141 ①):
`apply_to_live` ships `state_b` straight from
`read_state_wiring_raw(wc.working_folder)`. A working copy born at indent 4
therefore reformats the live chip however careful the live writer is. So
`write_state_wiring` takes `like=<folder>` — the folder whose formatting this
pair should carry — and the three working-copy writers pass the **live** folder.

## Verification

`tests/test_json_formatting.py` — 16 pins. The sniffer (2-space, CRLF without a
trailing newline, tabs, a missing file, a compact file, an undecodable file);
the writer (a 2-space file stays 2-space and the same size; a no-op write is
*byte-identical*; a new file is unchanged from today; compact stays compact);
`like` carrying the live format onto the working copy; and the reported path end
to end through the real routes — **one edited value is a one-line diff**, with a
size change under 40 bytes rather than +114,038, and a companion pin that the
value really did land.

**Mutation sweep: 10/10.** The tenth needed its own pin: `sync_from_live`
rewrites the working copy from the live chip — the path a drift banner's *take
live* and every auto-pull go through — and a working copy re-born at indent 4
reformats the chip on the *next* apply, which a load-then-edit test cannot see.

### Recorded, not mine

`tests/test_safe_io.py::test_reader_survives_concurrent_writes` fails on this
machine. Measured rather than assumed: it fails identically at HEAD, at
`f85fc45`, and at commits from 2026-09-10 and **2026-09-07**, deterministically
(3/3), with the reader seeing `FileNotFoundError`. It is a `@win_only`
file-locking test of the docs/87 OS-behaviour class and is untouched by this
round — but it is long-standing rather than a flake, and nothing says so, which
is the docs/155 §10a shape. Left open here deliberately rather than folded into
a formatting round.
