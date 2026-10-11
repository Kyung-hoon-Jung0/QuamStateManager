"""Checks of the text users see in captured Trends HTML."""
from __future__ import annotations

import html
import re
from pathlib import Path


def saved_provenance_note(body: str) -> str:
    """Normalize the opened run's provenance note, including markup and wrapping."""
    note = re.search(r'<p\b[^>]*class="ds-provenance-note"[^>]*>(.*?)</p>', body, re.S)
    if not note:
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", note.group(1))).split())


def saved_writer_is_not_proven(body: str) -> bool:
    note = saved_provenance_note(body)
    return ("writer is not proven" in note
            and "this run's saved state is the first to carry it" in note.replace("\u2019", "'")
            and "records no patch that set it" in note
            and "may not be the run that measured it" in note)


def validate_legacy_runs(runs: list[str]) -> None:
    """A smoke request must name existing saved states on the served rig."""
    if not runs or any(not (Path(run) / "state.json").is_file() for run in runs):
        raise ValueError("Legacy smoke requires existing saved-state folders on the served rig")
