"""The PHYSICAL marker and the dashboard's answers (physical/marks.py)."""
from music_librarian.physical import marks


def test_marker_added_and_removed_as_a_word():
    assert marks.with_marker([]) == ["PHYSICAL"]
    assert marks.with_marker(["Bought in 2004"]) == ["Bought in 2004 PHYSICAL"]
    assert marks.with_marker(["x PHYSICAL"]) == ["x PHYSICAL"]
    assert marks.without_marker(["Bought in 2004 PHYSICAL"]) == ["Bought in 2004"]
    assert marks.without_marker(["PHYSICAL"]) == []
    assert marks.without_marker(["a PHYSICAL b"]) == ["a b"]
    assert not marks.marked(["PHYSICALLY", "physical"]) and marks.marked(["x", "PHYSICAL"])


def test_answers_kept_and_taken_back(tmp_path):
    assert marks.load(tmp_path) == {"answers": {}, "links": {}}
    marks.update(tmp_path, "answers", {"5\tradiohead\tok computer": False, "x": "not a bool"})
    assert marks.load(tmp_path)["answers"] == {"5\tradiohead\tok computer": False}
    marks.update(tmp_path, "answers", {"5\tradiohead\tok computer": None})
    assert marks.load(tmp_path)["answers"] == {}
    marks.update(tmp_path, "links", {"7": "Daft Punk/Homework", "8": "", "9": 3})
    assert marks.load(tmp_path)["links"] == {"7": "Daft Punk/Homework", "8": ""}
