"""The plugin's DPSV computed from the counts (playcounts/dpsv.py)."""
import pytest

from music_librarian.playcounts.dpsv import compute, rounded


@pytest.mark.parametrize("value, expected", [(12.5, 13), (-12.5, -13), (12.4, 12), (12.6, 13),
                                             (-12.4, -12), (0, 0)])
def test_rounds_half_away_from_zero(value, expected):
    assert rounded(value) == expected


@pytest.mark.parametrize("plays, skips, expected", [
    (0, 0, 0), (None, None, 0), (-5, None, 0),
    (1, 0, 13),          # (100 - 0) / 8 = 12.5, the value seen in a real persist.db
    (0, 1, -25),
    (1000, 0, 100), (0, 1000, -100),
])
def test_from_one_kind_of_event(plays, skips, expected):
    assert compute(plays, skips) == expected


def test_the_latest_kind_of_event_is_replayed_last():
    assert compute(2, 1, last_played=200, last_skipped=100) == -6
    assert compute(2, 1, last_played=100, last_skipped=200) == 5
