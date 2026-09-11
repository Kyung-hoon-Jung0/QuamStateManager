# docs/184 — A fidelity of minus twenty percent

2026-09-12, customer, on-site, with a screenshot of the chip qualibrate was
running on 8001:

> 사진 보면 이상하지? - 부호의 RB가 있어 ㅋㅋ … 아마도 SM이 알아서 계산하는
> 과정에서 나온 수치 같아.

They were right that it is SM's own arithmetic. The tile read:

```
2Q CLIFFORD FID. (IRB×)
-20.18%  AVG
med -20.18%  ·  -117.27%–76.90%  ·  (2)
EPC 120.18%  (×5.37)
```

## It reproduces exactly

The tile beside it reads `2Q GATE FID. (IRB) 77.62%`, so EPG = 22.38%, and
`22.38% × 5.37 = 120.18%` — an error rate past 100%, hence a fidelity below
zero. The two pairs behind that average are 95.70% and 59.55%:

| pair | IRB gate fid. | EPG | × 5.37 = EPC | 1 − EPC |
|---|---|---|---|---|
| A | 95.70% | 4.30% | 23.09% | **76.91%** |
| B | 59.55% | 40.45% | 217.2% | **−117.2%** |

and their mean is −20.16%. Every number on the screenshot is accounted for.

## Why the arithmetic was wrong

`epc = n · epg` is a **first-order** identity. It is exactly what the lab's own
`fidelity.py:87` uses in the other direction (`epg = epc / n`), and it is
accurate while `n·epg ≪ 1` — the regime a working gate lives in. Pair B is not
in that regime, and the linear bridge extrapolates straight out of physics.

Note what is *not* wrong: the measured IRB numbers. A 59.55% two-qubit gate is a
bad gate, but it is a real measurement and the `2Q GATE FID. (IRB)` tile beside
it still reports both pairs. Only the **derived** Clifford-equivalent was
nonsense.

## The ceiling, which is the model's own

RB fits a depolarizing decay:

```
EPC = (d-1)/d · (1 - alpha),    alpha in [0, 1]
```

so for two qubits (d = 4) **no depolarizing fit can give an EPC above
(d−1)/d = 0.75**. That is the same expression docs/138 reproduced to 1e-12
against both of the lab's own numbers — citing it is citing their formula, not
inventing a rule.

A derived EPC above 0.75 is therefore not a worse gate; it is the bridge having
left the model. Such a pair is **set aside, and said** — using the vocabulary
these tiles already had for exactly this class (`agg.bad` → "N excluded", and
"N excluded · nothing usable" when none survive, docs/94). The tile adds the
reason, because "excluded" alone would not explain a number that used to be
there:

```
2Q CLIFFORD FID. (IRB×)
76.91%  AVG
med 76.91%  ·  76.91%–76.91%  ·  (1)  ·  1 excluded
EPC 23.09%
1 pair too noisy for the ×5.37 bridge (epc > 75%, the depolarizing limit)
```

Two details the harness found, both real:

- the note first named `divNote`, which is the **standard**-RB rows' divisor.
  The bridge that failed is the interleaved one, and a chip can carry interleaved
  runs without standard ones at all — it names the divisor actually used.
- `epc <= 0.75` on a float built as `(1-f)*div` is really "0.75 minus one ulp":
  the maximally-depolarizing case lands on `0.7500000000000001` and was refused
  for a rounding error. The ceiling carries a named epsilon.

## Deliberately not shipped

There **is** a well-defined exact answer under the same model: from EPG take
`alpha = 1 - 4·EPG/3`, compose `alpha^n`, and read `EPC = 3/4·(1 - alpha^n)`.
For pair B that is 36.19% — a real number, inside the model, bounded below by
the 25% depolarizing floor.

It is not shipped, because the lab's own code uses the **linear** relation in the
reverse direction, and shipping a third convention beside per-gate and
per-Clifford is precisely how docs/138's confusion started. Showing a number
derived by a rule the lab does not use would be trading an obviously-wrong value
for a plausibly-wrong one. Worth asking them; not worth assuming. The arithmetic
is recorded here so that conversation can start from a number.

## Verification

- `tests/rb_clifford_bridge_selfcheck.cjs` — 14 assertions against the real
  `chip-status.js`, on the screenshot's own inputs: no tile shows a negative
  fidelity, the good pair keeps its real value, the broken one is excluded *and*
  counted *and* explained, an all-broken chip renders an honest dash, a healthy
  chip is completely unchanged, and the ceiling is applied at the boundary in
  both directions.
- `tests/stress_rb_negative.cjs` — **8/8 in real headless Chrome**, zero console
  errors: the same inputs through the page's own `ChipStatus.mount`, with the
  rendered tile read back and checked for sideways overflow and a clipped note.
  (The page is `/topology`; `/chip-status` is only the report route.)
