"""An orphan's path noted against the library's (playcounts/matching.py)."""
import pytest

from music_librarian.playcounts.matching import LIKELY, SURE, Matcher, Named, named

LIBRARY = [("a", "Beyoncé/Lemonade/01 Formation.m4a", 0),
           ("b", "Beyoncé/Lemonade/02 Sorry.m4a", 10),
           ("c", "Daft Punk/Discovery/1-01 One More Time.m4a", 0),
           ("d", "Compilations/Hits/03 Formation (Live).mp3", 0)]


@pytest.mark.parametrize("path, expected", [
    ("/music/Beyoncé/Lemonade/01 Formation.m4a", Named("beyonce", "lemonade", "formation")),
    ("Daft Punk/Discovery/1-01 One More Time.m4a", Named("daft punk", "discovery", "one more time")),
    ("A/B/01 - Intro.mp3", Named("a", "b", "intro")),
    ("A/B/1979.mp3", Named("a", "b", "1979")),
    ("A/B/01 1979.mp3", Named("a", "b", "1979")),
    ("B/Loose.mp3", Named("", "b", "loose")),
    ("Loose.mp3", Named("", "", "loose")),
])
def test_named_from_a_path(path, expected):
    assert named(path) == expected


def test_same_track_under_another_spelling_comes_first():
    matches = Matcher(LIBRARY).best("file:///music/Beyonce/Lemonade/1 Formation.m4a")
    assert [key for _, key, _, _ in matches][:2] == ["a", "d"]
    assert matches[0][0] >= SURE > matches[1][0]


def test_close_play_counts_add_to_the_note():
    path = "/music/Beyoncé/Other/02 Sorry.m4a"
    without = Matcher(LIBRARY).best(path, plays=100, top=1)[0][0]
    close = Matcher(LIBRARY).best(path, plays=9, top=1)[0][0]
    assert LIKELY < without < close == pytest.approx(without + 5)


def test_top_and_words():
    assert len(Matcher(LIBRARY).best("/x/Beyoncé/Lemonade/01 Formation.m4a", top=2)) == 2
    found = Matcher(LIBRARY).best("/x/Beyoncé/Lemonade/01 Formation.m4a", words="DAFT discovery")
    assert [key for _, key, _, _ in found] == ["c"]
