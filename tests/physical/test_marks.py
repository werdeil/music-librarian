"""The PHYSICAL marker and the dashboard's answers (physical/marks.py)."""
from music_librarian import physical
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
    assert marks.load(tmp_path) == {"synced": {}, "answers": {}, "editions": {}, "conflicts": {}}
    marks.update(tmp_path, "synced", {"air\tmoon safari": True})
    marks.update(tmp_path, "editions", {"bjork\tdebut": "77"})
    marks.update(tmp_path, "answers", {"5\tradiohead\tok computer": False, "x": "not a bool"})
    held = marks.load(tmp_path)
    assert held["synced"] == {"air\tmoon safari": True} and held["editions"] == {"bjork\tdebut": "77"}
    assert held["answers"] == {"5\tradiohead\tok computer": False}
    marks.update(tmp_path, "synced", {"air\tmoon safari": None})
    assert marks.load(tmp_path)["synced"] == {}


def test_each_side_carries_its_change_over():
    decide, owned = physical.decide, {"id": "1"}
    assert decide(True, owned, True, None) == decide(False, None, False, None) == physical.AGREE
    assert decide(False, owned, False, None) == decide(False, owned, None, None) == physical.MARK
    assert decide(True, None, True, None) == physical.UNMARK
    assert decide(True, None, False, None) == physical.ADD
    assert decide(False, owned, True, None) == physical.REMOVE
    assert decide(True, None, None, None) == physical.CONFLICT
    assert decide(True, None, None, "add") == physical.ADD
    assert decide(True, None, None, "drop") == physical.UNMARK
