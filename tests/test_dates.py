"""dates: the edition's year, alone, the one date a track holds."""
import pytest

from music_librarian import dates


@pytest.mark.parametrize("value, year", [("2011", True), ("0000", False), ("2011-05-02", False),
                                         ("11", False), ("", False), ("année", False)])
def test_is_year(value, year):
    assert dates.is_year(value) is year


@pytest.mark.parametrize("years, found", [
    (["2011", "2011"], ()),
    (["2011", ""], (dates.MISSING,)),
    (["2011-05-02", "2011"], (dates.FORMAT,)),
    ([" ", "abc"], (dates.MISSING, dates.FORMAT)),
])
def test_problems(years, found):
    assert dates.problems(years) == found
