"""enrich.common: the time a dry run estimates."""
import pytest

from music_librarian.enrich import common


@pytest.mark.parametrize("seconds, shown", [(20, "under a minute"), (90, "~2 min"), (3600, "~1 h"),
                                            (7830, "~2 h 10 min")])
def test_rough_durations(seconds, shown):
    assert common.rough(seconds) == shown
    assert common.ESTIMATE_LINE.match(f"      estimated time: {common.estimate(seconds)}")[1] == str(seconds)
