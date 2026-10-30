from statistics import median
from time import perf_counter
from quam_state_manager.core import addition_rules
from tests.test_add_verdict_wording import rule, manifest


def synthetic():
    qs = ['q' + str(i) for i in range(60)]
    pairs = [[qs[i], qs[(i + d) % 60]] for d in (1, 2) for i in range(60)][:100]
    return dict(qubits=qs, qubit_pairs=pairs, lines=[{'element': q, 'line': 'flux'} for q in qs])


def test_expand_budget():
    s, r, m = synthetic(), rule(), manifest()
    addition_rules.expand(s, r, m)
    times = []
    for _ in range(5):
        start = perf_counter()
        result = addition_rules.expand(s, r, m)
        times.append((perf_counter() - start) * 1000)
    elapsed = median(times)
    print(f'expand median: {elapsed:.2f} ms (100 pairs, 60 qubits)')
    assert len(result['pairs']) == 100
    assert elapsed <= 80, times
