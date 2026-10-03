"""Read-only archive audit for docs/256; pass archive roots as arguments.

Example: python tools/check_run_instant.py path/to/archive_a path/to/archive_b
Only node.json files are opened, in read mode. No archive names are embedded.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quam_state_manager.core.timefmt import archive_offset_hint, run_instant


def aware_created(node):
    value = node.get("created_at") if isinstance(node, dict) else None
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
        return date if date.utcoffset() is not None else None
    except ValueError:
        return None


def audit(root: Path, label: str) -> dict:
    records = []
    errors = []
    for path in sorted(root.rglob("node.json")):
        try:
            with path.open(encoding="utf-8") as stream:
                records.append((path.parent, json.load(stream)))
        except (OSError, ValueError) as exc:
            errors.append({"relative_path": str(path.relative_to(root)), "error": str(exc)})
    hint = archive_offset_hint(node for _, node in records)
    qualities = Counter()
    offsets = Counter()
    deltas = []
    checked = mismatches = 0
    for folder, node in records:
        instant, quality = run_instant(node, folder, offset_hint=hint)
        qualities[quality] += 1
        offset = archive_offset_hint([node])
        if offset is not None:
            offsets[offset] += 1
        created = aware_created(node)
        if created is None:
            continue
        delta = created.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        expected = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        checked += 1
        mismatches += instant != expected or quality != "offset"
        if hint is not None:
            folder_instant, folder_quality = run_instant({}, folder, offset_hint=hint)
            if folder_instant is not None and folder_quality == "archive_offset":
                deltas.append((folder_instant - expected) / 1_000_000)
    deltas.sort()
    distribution = {"count": len(deltas)}
    if deltas:
        distribution.update(min=min(deltas), median=statistics.median(deltas),
                            p95=deltas[math.ceil(len(deltas) * 0.95) - 1],
                            max=max(deltas), mean=statistics.mean(deltas),
                            negative=sum(d < 0 for d in deltas),
                            zero=sum(d == 0 for d in deltas), positive=sum(d > 0 for d in deltas))
    return {"archive": label, "run_count": len(records), "quality_counts": dict(qualities),
            "offsets_seen": dict(offsets), "offset_hint": hint,
            "aware_created_checked": checked, "exact_mismatches": mismatches,
            "folder_minus_created_s": distribution, "read_errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()
    for root in args.roots:
        if not root.is_dir():
            parser.error(f"Archive root is not a directory: {root}")
    reports = [audit(root, f"Archive {i}") for i, root in enumerate(args.roots, 1)]
    print(json.dumps(reports, indent=2))
    return int(any(report["exact_mismatches"] or report["read_errors"] for report in reports))


if __name__ == "__main__":
    raise SystemExit(main())
