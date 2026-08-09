# 103 — Performance profile: search and loading on real big chips (1.0-prep)

*2026-08-10. Question: is anything about search or loading actually slow?
Measured in-process (no network noise), cold + median-of-3-warm, on the two
biggest real chips available (a 21Q chip, 908 KB state; the 10Q
tunable-coupler chip, 370 KB) plus a 285-run dataset root.*

## The numbers (before this branch)

| Surface | 21Q cold | 21Q warm | 10Q warm | size |
|---|---|---|---|---|
| `/load` (chip activation) | 229 ms | — | 101 ms | — |
| `/qubits` | 159 ms | **2.1 ms** | 2.5 ms | 105 KB |
| `/bulk` (Live State Edit) | 419 ms | **320 ms** | 219 ms | **10.0 MB** |
| `/bulk/all-values` | 77 ms | 63 ms | 33 ms | 2.3 MB (gzipped on wire) |
| `/pulses` | 96 ms | 4.4 ms | 3.4 ms | 170 KB |
| `/api/search?q=amp` | 96 ms | **0.9 ms** | 0.8 ms | 9 KB |
| `/api/topology` | 9 ms | 1.8 ms | 0.8 ms | 173 KB |
| `/diff` (bare) | 31 ms | 14 ms | 7 ms | 83 KB |
| `/datasets` page (285 runs) | 336 ms | 19 ms | — | 343 KB |
| `/datasets/changes-since` tick | — | 3.5 ms | — | — |
| `/workspace/tree` | — | 6.5 ms | — | 38 KB |

**Verdict: search is NOT a bottleneck** (sub-millisecond warm even at 21Q —
the <1 ms index claim holds), and neither is ordinary loading (activation
~0.1–0.23 s, every page ≤ 20 ms warm except one). The one real cost is
**`/bulk`**: a 10 MB HTML response (docs/85 ships every cell deliberately)
with a ~320 ms server render — and, until this branch, no loading cue at
all.

## Shipped here

- **`/bulk` gzips when the client accepts it** — the same stdlib pattern
  `/bulk/all-values` already used, Content-Length pinned to actual bytes.
  Measured on the 21Q chip: **10,011 KB → 404 KB (25×)**, and the
  end-to-end request got *faster* (450 → 332 ms in-process — compressing is
  cheaper than pushing 10 MB through the stack). Byte-identical after
  decompression (asserted).
- **The main pane now shows in-flight state**: `#table-pane.htmx-request`
  dims like the inspector always has, and `/bulk`, `/diff`, `/topology`,
  `/autofit`, `/compare-hub` joined `SLOW_PREFIXES` so the global loader
  covers the routes that are actually slow on big chips — a slow `/bulk`
  used to leave the old page fully interactive-looking (the double-click
  report).

## Known, deliberately deferred (the L-sized item)

The 10 MB DOM itself: ~9.5k cells × ~6 nodes on a 452-column chip is a
client-side mount/search cost that only column-window virtualization
removes (docs/85 measured the sub-linear but real 1.9× mount). That is a
structural change to the grid renderer — backlog (docs/104), not a
night-shift edit. Everything else measured is healthy.

Raw numbers: `D:\work\sm-verify-allpulse\perf_probe.json`. Bulk suites
green post-change (66 passed).
