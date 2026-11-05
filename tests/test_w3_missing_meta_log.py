"""W3-7: a snapshot folder caught between its copy and its meta.json (one
per capture, transient) is debug noise, not a warning; one that stays
without meta.json past the grace still warns."""
import logging
import os
import time

from quam_state_manager.core import history


def _levels(caplog, child):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=history.logger.name):
        history._log_missing_meta(child)
    return [r.levelno for r in caplog.records if "without meta.json" in r.getMessage()]


def test_a_young_folder_is_debug(tmp_path, caplog):
    d = tmp_path / "2026-11-05T10-00-00"
    d.mkdir()
    assert _levels(caplog, d) == [logging.DEBUG]


def test_an_old_folder_still_warns(tmp_path, caplog):
    d = tmp_path / "2026-11-05T10-00-00"
    d.mkdir()
    old = time.time() - 60
    os.utime(d, (old, old))
    assert _levels(caplog, d) == [logging.WARNING]


def test_both_listing_paths_use_it():
    import inspect
    src = inspect.getsource(history.HistoryManager)
    assert src.count("_log_missing_meta(child)") == 2
    assert 'logger.warning("Skipping snapshot dir without meta.json' not in src
