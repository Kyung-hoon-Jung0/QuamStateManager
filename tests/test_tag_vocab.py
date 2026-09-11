"""docs/182 — the words a PERSON typed reach the search box's typeahead.

Customer, on-site (2026-09-11):

    "우리 검색어 창에 지금 타이핑을 하면 저절로 뜨는데... 이거! 제발 data tag랑
     note에 사용자가 기재한 단어들도 넣어달라고 함!!!! 다만, 검색 pop up할때
     뜨는건 run 번호: tag 이름 (혹은 note) 이렇게 뜨도록. note는 내용이 다
     담기게 하는게 아니고 그냥 note (검색어 ...) 그냥 이렇게 compact하게."

The grammar could always find them — ``tag:flagged`` and ``note:todo`` are in
the search help — but you had to already know the word. Every other vocabulary
the box completes from is machine-generated (node names, parameter keys); tags
and notes are the only words in the archive a *person* chose, which makes them
exactly the ones worth being reminded of.

The load-bearing engineering decision is where the words are read from:
``quashboard_tags.json`` **directly**, never through ``DatasetStore``. That file
is the source of truth (``DatasetStore._load_tags`` reads exactly it) and costs
one ``open`` per data folder; going through a store would let a keystroke
trigger the cold run scan docs/170 spent a round bounding (31.6 s at the
customer's share latency).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import tag_vocab
from quam_state_manager.web.app import create_app


def _tags_file(folder: Path, tags=None, notes=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "quashboard_tags.json").write_text(
        json.dumps({"bookmarks": [], "tags": tags or {}, "notes": notes or {}}),
        encoding="utf-8")


class TestNoteWords:
    def test_a_note_contributes_words_not_its_text(self):
        got = tag_vocab.note_words("Recalibrated after the fridge cycled")
        assert "recalibrated" in got and "fridge" in got
        assert "the" not in got          # a filler matches almost everything

    def test_one_letter_words_are_not_vocabulary(self):
        assert tag_vocab.note_words("a b cd") == ["cd"]

    def test_korean_is_as_searchable_as_english(self):
        """The customer writes notes in Korean. A `\\w`-based tokeniser without
        re.UNICODE would silently drop every one of them."""
        got = tag_vocab.note_words("냉각기 재시작 후 재보정")
        assert "냉각기" in got and "재보정" in got

    def test_duplicates_collapse_and_order_is_first_seen(self):
        assert tag_vocab.note_words("beta alpha beta") == ["beta", "alpha"]

    def test_the_word_cap_is_a_cap(self):
        got = tag_vocab.note_words(" ".join("w%d" % i for i in range(200)))
        assert len(got) == tag_vocab.MAX_WORDS_PER_NOTE


class TestBuild:
    def test_tags_and_notes_from_one_folder(self, tmp_path):
        f = tmp_path / "ds"
        _tags_file(f, tags={"11": ["flagged", "favorite"], "12": ["flagged"]},
                   notes={"11": "check the readout later"})
        built = tag_vocab.build([f])
        by = {d["t"]: d for d in built["tags"]}
        assert by["flagged"]["n"] == 2
        assert by["flagged"]["r"] == [12, 11]        # newest first
        assert by["favorite"]["r"] == [11]
        assert built["notes"] == [{"r": 11, "w": ["check", "readout", "later"]}]

    def test_two_folders_merge_by_tag_name(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        _tags_file(a, tags={"1": ["wip"]})
        _tags_file(b, tags={"2": ["wip"]})
        built = tag_vocab.build([a, b])
        assert [d["t"] for d in built["tags"]] == ["wip"]
        assert built["tags"][0]["r"] == [2, 1]

    def test_a_corrupt_entry_costs_that_entry_not_the_vocabulary(self, tmp_path):
        """The same per-key tolerance `_load_tags` has, and for the same
        reason: a hand-edited file must not empty the whole box."""
        f = tmp_path / "ds"
        f.mkdir()
        (f / "quashboard_tags.json").write_text(json.dumps(
            {"tags": {"oops": ["x"], "7": ["good"], "8": "not-a-list"},
             "notes": {"nope": "text", "9": "real note"}}), encoding="utf-8")
        built = tag_vocab.build([f])
        assert [d["t"] for d in built["tags"]] == ["good"]
        assert [d["r"] for d in built["notes"]] == [9]
        assert built["dropped"] == 3

    def test_an_unreadable_file_is_simply_no_vocabulary(self, tmp_path):
        f = tmp_path / "ds"
        f.mkdir()
        (f / "quashboard_tags.json").write_text("{ not json", encoding="utf-8")
        assert tag_vocab.build([f]) == {"tags": [], "notes": [], "dropped": 0}

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert tag_vocab.build([tmp_path / "nothing"])["tags"] == []

    def test_an_empty_tag_name_is_not_offered(self, tmp_path):
        f = tmp_path / "ds"
        _tags_file(f, tags={"1": ["", "   ", "real"]})
        assert [d["t"] for d in tag_vocab.build([f])["tags"]] == ["real"]


class TestVersion:
    def test_it_moves_when_a_tag_is_typed(self, tmp_path):
        f = tmp_path / "ds"
        _tags_file(f, tags={"1": ["a"]})
        v1 = tag_vocab.version([f])
        _tags_file(f, tags={"1": ["a"], "2": ["b"]})
        assert tag_vocab.version([f]) != v1

    def test_a_missing_folder_still_has_a_version(self, tmp_path):
        assert tag_vocab.version([tmp_path / "nope"])


class TestTheRoute:
    @pytest.fixture
    def env(self, tmp_path):
        data = tmp_path / "ds"
        _tags_file(data, tags={"11": ["flagged"]}, notes={"11": "check readout"})
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(data)})
        return {"app": app, "client": c, "data": data}

    def test_it_answers_the_tags_and_notes(self, env):
        body = env["client"].get("/workspace/tag-vocab").get_json()
        assert [d["t"] for d in body["tags"]] == ["flagged"]
        assert body["notes"] and body["notes"][0]["r"] == 11
        assert "readout" in body["notes"][0]["w"]
        assert body["v"]

    def test_an_unchanged_version_costs_one_comparison(self, env):
        c = env["client"]
        v = c.get("/workspace/tag-vocab").get_json()["v"]
        assert c.get("/workspace/tag-vocab?v=" + v).status_code == 204

    def test_a_newly_typed_tag_changes_the_version(self, env):
        c = env["client"]
        v = c.get("/workspace/tag-vocab").get_json()["v"]
        _tags_file(env["data"], tags={"11": ["flagged"], "12": ["urgent"]})
        r = c.get("/workspace/tag-vocab?v=" + v)
        assert r.status_code == 200
        assert "urgent" in [d["t"] for d in r.get_json()["tags"]]

    def test_it_never_builds_a_dataset_store(self, env, monkeypatch):
        """The whole reason the file is read directly. A keystroke that can
        trigger the cold run scan is a keystroke that can hang the box for
        half a minute on a slow share (docs/170)."""
        from quam_state_manager.web import routes as routes_mod
        called = []
        monkeypatch.setattr(routes_mod, "_get_or_create_store",
                            lambda *a, **k: called.append(1))
        assert env["client"].get("/workspace/tag-vocab").status_code == 200
        assert called == []

    def test_no_workspace_is_an_empty_vocabulary_not_an_error(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        r = app.test_client().get("/workspace/tag-vocab")
        assert r.status_code == 200
        assert r.get_json()["tags"] == []


_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "tag_typeahead_selfcheck.cjs"


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not on PATH")
def test_tag_typeahead_selfcheck_passes():
    """The popup's format, which is what the customer actually specified.

    Pins `#<run>: <tag>` and `#<run>: note (<word>)` against the real shipped
    sidebar-typeahead.js, that a note's other text never reaches a row, that
    accepting inserts the grammar's own scope, that the cap is said out loud,
    and that a word the tokenizer would mangle is not offered as a row nobody
    can accept.
    """
    import subprocess
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "tag_typeahead_selfcheck ok" in r.stdout, (r.stdout + r.stderr)
