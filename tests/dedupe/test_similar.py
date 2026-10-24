"""Which names are grouped as likely duplicates."""
import pytest

from music_librarian.dedupe.review import suggestion
from music_librarian.dedupe.similar import MUSICBRAINZ, NORMALIZED, PARTIAL, TYPO, find_groups


def groups(*names, mbids=None, different=()):
    counts = {n: 10 - i for i, n in enumerate(names)}
    return find_groups(counts, mbids or {}, set(different))


@pytest.mark.parametrize("a, b", [
    ("Beyoncé", "Beyonce"), ("R.E.M.", "REM"), ("The Beatles", "Beatles"),
    ("Simon & Garfunkel", "Simon and Garfunkel"), ("J.S. Bach", "J. S. Bach"),
    ("Bach, Johann Sebastian", "Johann Sebastian Bach"), ("Guns N’ Roses", "Guns N' Roses"),
    ("AC/DC", "ACDC"),
])
def test_normalized(a, b):
    assert groups(a, b) == [([a, b], {NORMALIZED})]


def test_typo():
    assert groups("Metallica", "Mettalica") == [(["Metallica", "Mettalica"], {TYPO})]


def test_partial_only_when_one_full_name():
    assert groups("Johann Sebastian Bach", "Bach") == [(["Johann Sebastian Bach", "Bach"], {PARTIAL})]
    assert groups("Angus Young", "Malcolm Young", "Young") == []


def test_unrelated_names_stay_apart():
    assert groups("John Lennon", "Julian Lennon", "Abba", "Adele") == []


def test_musicbrainz():
    ids = {"Puff Daddy": {"x"}, "Diddy": {"x"}}
    assert groups("Puff Daddy", "Diddy", mbids=ids) == \
        [(["Puff Daddy", "Diddy"], {MUSICBRAINZ})]


def test_homonyms_and_declared_different_never_grouped():
    assert groups("Nirvana", "NIRVANA", mbids={"Nirvana": {"us"}, "NIRVANA": {"uk"}}) == []
    assert groups("Metallica", "Mettalica", different=[frozenset({"metallica", "mettalica"})]) == []


def test_ties_put_the_full_spelling_first():
    assert find_groups({"Beatles": 2, "The Beatles": 2}, {}, set())[0][0] == ["The Beatles", "Beatles"]
    assert find_groups({"Bach, Johann Sebastian": 1, "Johann Sebastian Bach": 1}, {}, set())[0][0] == \
        ["Johann Sebastian Bach", "Bach, Johann Sebastian"]


def test_suggestion_of_a_partial_name():
    assert suggestion(["Bach", "Johann Sebastian Bach"], {PARTIAL, NORMALIZED}) == "Johann Sebastian Bach"
    assert suggestion(["Beyoncé", "Beyonce"], {NORMALIZED}) == "Beyoncé"
