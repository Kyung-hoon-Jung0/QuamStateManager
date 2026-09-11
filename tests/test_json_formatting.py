"""docs/185 — one changed number rewrote all twelve thousand lines.

Write-path stress round, on a byte copy of the customer's original
``state.json`` (434,839 bytes, 2-space indent): one ``/field/edit``, one
apply-to-live, and the file came back **548,877 bytes at 4-space** — +26%, every
line rewritten, for a single value.

No VALUE changed that a press did not ask for, so the covenant held. What broke
is everything *outside* SM — git, rsync, a backup, a file-watcher, a reader
using size as a heuristic. "What did that press change" becomes unanswerable
from the file itself.

It is not only the indent. A file has three formatting facts SM does not own:

============  ==============================================================
indent        2 vs 4 spaces, or tabs
line endings  the KRISS chip on this machine is CRLF, and ``open(.., "w")``
              translates by PLATFORM — the same SM rewrites the same file
              differently on Windows and on Linux
trailing NL   the KRISS chip has none; SM always appended one
============  ==============================================================

So SM writes back what it found. A file that does **not** exist yet — every file
SM owns, on its first write — is written exactly as before, so nothing of SM's
own moves.

The working copy needs its own line, because the live write is a byte copy *of
the working copy* (docs/141 ①): ``apply_to_live`` ships ``state_b`` straight
from ``read_state_wiring_raw(wc.working_folder)``. A working copy born at
indent 4 therefore reformats the live file however careful the live writer is.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.web.app import create_app

_STATE = {
    "qubits": {"q1": {"id": "q1", "f_01": 5.0e9, "T1": 2.0e-5},
               "q2": {"id": "q2", "f_01": 5.2e9, "T1": 2.1e-5}},
    "qubit_pairs": {},
    "active_qubit_names": ["q1", "q2"],
}
_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _write_as(path: Path, data, *, indent, newline="\n", trailing=True):
    """A file written the way something OTHER than SM wrote it."""
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    if newline != "\n":
        text = text.replace("\n", newline)
    if trailing:
        text += newline
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _indent_of(path: Path):
    txt = path.read_bytes().decode("utf-8")
    for line in txt.split("\n")[1:]:
        s = line.lstrip(" \t")
        if not s or s == "\r":
            continue
        lead = line[:len(line) - len(s)]
        if lead:
            return "\t" if lead[0] == "\t" else len(lead)
    return None


class TestTheSniffer:
    def test_it_reads_a_two_space_file(self, tmp_path):
        p = tmp_path / "s.json"
        _write_as(p, _STATE, indent=2)
        fmt = safe_io.json_format_of(p)
        assert fmt["indent"] == 2 and fmt["newline"] == "\n" and fmt["trailing"]

    def test_it_reads_crlf_and_a_missing_trailing_newline(self, tmp_path):
        """The KRISS chip on this machine, exactly."""
        p = tmp_path / "s.json"
        _write_as(p, _STATE, indent=4, newline="\r\n", trailing=False)
        fmt = safe_io.json_format_of(p)
        assert fmt["indent"] == 4
        assert fmt["newline"] == "\r\n"
        assert fmt["trailing"] is False

    def test_tabs(self, tmp_path):
        p = tmp_path / "s.json"
        _write_as(p, _STATE, indent="\t")
        assert safe_io.json_format_of(p)["indent"] == "\t"

    def test_a_missing_file_is_None(self, tmp_path):
        assert safe_io.json_format_of(tmp_path / "nope.json") is None

    def test_a_compact_file_has_no_indent_to_copy(self, tmp_path):
        """None, not a guess: the caller then keeps its own default rather than
        being told a single-line document wants indent 0."""
        p = tmp_path / "s.json"
        p.write_text(json.dumps(_STATE), encoding="utf-8")
        assert safe_io.json_format_of(p) is None

    def test_an_unreadable_file_is_never_an_error(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_bytes(b"\xff\xfe not json at all")
        safe_io.json_format_of(p)      # must not raise


class TestWritingBackWhatWasFound:
    def test_a_two_space_file_stays_two_space(self, tmp_path):
        p = tmp_path / "s.json"
        _write_as(p, _STATE, indent=2)
        before = p.stat().st_size
        safe_io.atomic_write_json(p, _STATE)
        assert _indent_of(p) == 2
        assert p.stat().st_size == before

    def test_crlf_and_no_trailing_newline_survive(self, tmp_path):
        p = tmp_path / "s.json"
        _write_as(p, _STATE, indent=4, newline="\r\n", trailing=False)
        original = p.read_bytes()
        safe_io.atomic_write_json(p, _STATE)
        assert p.read_bytes() == original, "a no-op write was not a no-op"

    def test_a_new_file_is_written_exactly_as_before(self, tmp_path):
        """Every file SM owns takes this path on its first write. Indent 4, a
        trailing newline, and the platform's own line endings — unchanged."""
        p = tmp_path / "new.json"
        safe_io.atomic_write_json(p, _STATE)
        assert _indent_of(p) == 4
        assert p.read_bytes().endswith(b"\n")

    def test_a_compact_write_is_still_compact(self, tmp_path):
        p = tmp_path / "cache.json"
        safe_io.atomic_write_json(p, _STATE, compact=True)
        assert b"\n    " not in p.read_bytes()

    def test_only_the_changed_value_differs(self, tmp_path):
        """The property the customer actually cares about: a one-value edit is
        a one-line diff."""
        p = tmp_path / "s.json"
        _write_as(p, _STATE, indent=2)
        before = p.read_bytes().decode("utf-8").split("\n")
        changed = json.loads(json.dumps(_STATE))
        changed["qubits"]["q1"]["T1"] = 2.99e-5
        safe_io.atomic_write_json(p, changed)
        after = p.read_bytes().decode("utf-8").split("\n")
        assert len(before) == len(after)
        diff = [i for i in range(len(before)) if before[i] != after[i]]
        assert len(diff) == 1, [before[i] for i in diff][:5]
        assert "T1" in before[diff[0]]


class TestLikeCarriesTheLiveFormat:
    def test_the_working_copy_is_born_in_the_live_format(self, tmp_path):
        live, work = tmp_path / "live", tmp_path / "work"
        _write_as(live / "state.json", _STATE, indent=2)
        _write_as(live / "wiring.json", _WIRING, indent=2)
        safe_io.write_state_wiring(work, _STATE, _WIRING, like=live)
        assert _indent_of(work / "state.json") == 2
        assert _indent_of(work / "wiring.json") == 2

    def test_without_like_a_new_folder_is_SMs_own_format(self, tmp_path):
        work = tmp_path / "work"
        safe_io.write_state_wiring(work, _STATE, _WIRING)
        assert _indent_of(work / "state.json") == 4

    def test_a_missing_like_folder_is_not_an_error(self, tmp_path):
        work = tmp_path / "work"
        safe_io.write_state_wiring(work, _STATE, _WIRING, like=tmp_path / "gone")
        assert _indent_of(work / "state.json") == 4


class TestEndToEnd:
    """The reported path, through the real routes."""

    @pytest.fixture
    def env(self, tmp_path):
        live = tmp_path / "chip"
        _write_as(live / "state.json", _STATE, indent=2)
        _write_as(live / "wiring.json", _WIRING, indent=2)
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        return {"app": app, "client": c, "live": live}

    def test_an_edit_and_apply_does_not_reformat_the_chip(self, env):
        live_state = env["live"] / "state.json"
        before_lines = live_state.read_bytes().decode("utf-8").split("\n")
        before_size = live_state.stat().st_size

        c = env["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1",
                                           "value": "2.99e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200

        after_lines = live_state.read_bytes().decode("utf-8").split("\n")
        assert _indent_of(live_state) == 2, "the chip was reformatted"
        # 434,839 -> 548,877 was +26%. Now it is the length of one number.
        assert abs(live_state.stat().st_size - before_size) < 40, (
            before_size, live_state.stat().st_size)
        assert len(before_lines) == len(after_lines)
        diff = [i for i in range(len(before_lines))
                if before_lines[i] != after_lines[i]]
        assert len(diff) == 1, [before_lines[i] for i in diff][:5]
        assert "T1" in before_lines[diff[0]]

    def test_the_value_really_did_land(self, env):
        """…and the whole point is not lost: the edit IS written."""
        c = env["client"]
        c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2.99e-5"})
        c.post("/state/apply-to-live")
        doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert doc["qubits"]["q1"]["T1"] == 2.99e-5


class TestTheResyncPathToo:
    """`sync_from_live` rewrites the working copy from the live chip, and it is
    the path a drift banner's "take live" and every auto-pull go through. A
    working copy re-born at indent 4 reformats the chip on the NEXT apply, so
    the mutation that drops `like` there is invisible to a load-then-edit test.
    """

    def test_a_resync_keeps_the_live_format(self, tmp_path):
        from quam_state_manager.core import working_copy

        live = tmp_path / "chip"
        _write_as(live / "state.json", _STATE, indent=2)
        _write_as(live / "wiring.json", _WIRING, indent=2)
        wc = working_copy.create(str(tmp_path / "_inst"), live)
        assert _indent_of(wc.working_folder / "state.json") == 2

        # Something outside SM rewrites the chip AND reformats it while doing
        # so — another tool, a reformat, a hand edit. This is the case `like`
        # exists for: the working copy already exists, so the self-sniff would
        # keep its own stale 2-space and the next apply would rewrite the whole
        # chip back to it. (With the formats equal, the self-sniff covers it and
        # dropping `like` is a real no-op — which is why the first version of
        # this pin could not see the mutation.)
        moved = json.loads(json.dumps(_STATE))
        moved["qubits"]["q2"]["T1"] = 9.9e-5
        _write_as(live / "state.json", moved, indent=4)
        _write_as(live / "wiring.json", _WIRING, indent=4)

        working_copy.sync_from_live(wc)
        assert _indent_of(wc.working_folder / "state.json") == 4, \
            "the re-synced working copy would reformat the chip on the next apply"
