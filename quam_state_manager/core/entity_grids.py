"""Which collections a chip actually HAS, so a grid exists for each of them.

Customer, 2026-09-10: "live state edit이 twpa를 지원하지 않네? json tree view는
당연히 잘 보이거든? 지금은 없지만 나중에 qdac도 그렇고.. 이거 adaptive하게
해서 display하게 할수는 없니?"

Live State Edit had exactly two grids, one per HARDCODED collection: ``qubits``
and ``qubit_pairs``. Their chip also carries ``twpas``, so a TWPA pump's
amplitude and RF frequency existed on no grid and the search could not find
them. Adding a third hardcoded grid would fix this chip and leave the next
component out again -- the docs/94 / docs/126 ① silent-skip shape.

So the collections are DISCOVERED from the loaded chip, and each one gets a
grid through the same derivation the pair grid uses
(``pair_columns.derive_entity_columns``). Nothing here names ``twpa`` or
``qdac``; a lab whose chip carries a component this code has never heard of
gets its grid on the day they open it.

What counts as a collection is deliberately narrow, because the alternative to
a rule is a page full of noise:

* a top-level (or ``wiring.``-level) dict,
* whose values are themselves dicts -- entities, not settings,
* that is not already a grid, not structural, and not empty.

``ports`` is excluded on purpose: it is four levels deep and its leaves already
reach the qubit and pair grids as alias columns through the wiring pointer
chain (docs/94, docs/126 ①). ``extras`` is excluded because it is free-form
text by policy (docs/81). Nothing here is a denylist of component names.
"""

from __future__ import annotations

from typing import Any

from quam_state_manager.core.loader import natural_key

# Collections that already have their own grid, or that are structure rather
# than entities. NOT a list of component names -- every one of these is here
# for a reason that is about the SHAPE of the thing, not what it is called.
_STATE_SKIP = frozenset({
    "qubits",         # its own grid
    "qubit_pairs",    # its own grid
    "ports",          # 4 levels deep; its leaves reach both grids as aliases
    "extras",         # free-form by policy (docs/81)
    "wiring",         # the other document's collections (see `discover`)
    "network",        # not a collection of entities -- one settings object
})

_WIRING_SKIP = frozenset()

# A label for the collections we happen to know; anything else is humanized
# from its own key, so an unknown component is named, never hidden.
_LABELS = {
    "twpas": "TWPAs",
    "octaves": "Octaves",
    "mixers": "Mixers",
    "qdac": "QDAC",
    "qdacs": "QDACs",
    "qubits": "Qubits",
    "qubit_pairs": "Qubit Pairs",
}


def humanize(key: str) -> str:
    """``qubit_pairs`` -> ``Qubit Pairs``; a known key keeps its own spelling."""
    if key in _LABELS:
        return _LABELS[key]
    return " ".join(w.upper() if len(w) <= 3 and w.isalpha() and w.islower()
                    and w in ("rr", "xy", "lo", "if", "mw", "dc", "rf")
                    else w.capitalize()
                    for w in str(key).split("_")) or str(key)


def _is_entity_collection(value: Any) -> bool:
    """A dict of dicts, with at least one entity in it.

    ``__package_versions__`` is a dict of STRINGS and fails here without being
    named; an empty ``octaves`` fails too, because a grid with no rows is a
    heading over nothing.
    """
    if not isinstance(value, dict):
        return False
    entities = [v for k, v in value.items()
                if not (isinstance(k, str) and k.startswith("__"))]
    if not entities:          # empty, or nothing but private keys
        return False
    return all(isinstance(v, dict) for v in entities)


def discover(merged: dict, doc: str = "state") -> list[dict]:
    """The extra grids this chip needs, in render order.

    Each entry: ``{key, root, label, ids, expand_ports}``. ``key`` is the
    client-facing grid id (also the ``?grid=`` token for cell hydration);
    ``root`` is the dot path of the collection.

    ``doc="state"`` returns the state.json collections that do NOT already have
    a grid. ``doc="wiring"`` returns every collection under ``wiring.``, with
    port expansion OFF -- on a wiring grid the ``#/ports/...`` pointer IS the
    value the file holds and the thing a user edits, so expanding it into the
    port's own leaves would show them a different document.
    """
    if not isinstance(merged, dict):
        return []
    out: list[dict] = []
    if doc == "wiring":
        wiring = merged.get("wiring")
        if not isinstance(wiring, dict):
            return []
        for key in sorted(wiring.keys(), key=natural_key):
            if key.startswith("__") or key in _WIRING_SKIP:
                continue
            val = wiring.get(key)
            if not _is_entity_collection(val):
                continue
            ids = [k for k in sorted(val.keys(), key=natural_key)
                   if not (isinstance(k, str) and k.startswith("__"))]
            out.append({"key": "w_" + key, "root": "wiring." + key,
                        "label": humanize(key), "ids": ids,
                        "expand_ports": False})
        return out

    for key in sorted(merged.keys(), key=natural_key):
        if key.startswith("__") or key in _STATE_SKIP:
            continue
        val = merged.get(key)
        if not _is_entity_collection(val):
            continue
        ids = [k for k in sorted(val.keys(), key=natural_key)
               if not (isinstance(k, str) and k.startswith("__"))]
        out.append({"key": "e_" + key, "root": key, "label": humanize(key),
                    "ids": ids, "expand_ports": True})
    return out
