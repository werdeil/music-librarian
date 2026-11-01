"""lyrics.misses: lookups that found nothing, retried after a delay or a tag change."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian.lyrics import misses
from music_librarian.lyrics.misses import Misses, recorded

META = {"artist": "Nirvana", "title": "Titre", "album": "Album"}
DAY = 86400


@pytest.fixture
def clock():
    return [1_000_000_000.0]


def opened(tmp_path, clock, days=30):
    return Misses(tmp_path / "state", days=days, now=lambda: clock[0])


def test_recent_until_the_delay(tmp_path, clock):
    with opened(tmp_path, clock) as m:
        m.record(tmp_path / "01.m4a", META)
        assert m.recent(tmp_path / "01.m4a", META)
        assert not m.recent(tmp_path / "02.m4a", META)
    clock[0] += 29 * DAY
    with opened(tmp_path, clock) as m:
        assert m.recent(tmp_path / "01.m4a", META)
    clock[0] += 2 * DAY
    with opened(tmp_path, clock) as m:
        assert not m.recent(tmp_path / "01.m4a", META)


def test_changed_tags_retried(tmp_path, clock):
    with opened(tmp_path, clock) as m:
        m.record(tmp_path / "01.m4a", META)
        assert not m.recent(tmp_path / "01.m4a", {**META, "title": "Title"})


def test_forget(tmp_path, clock):
    with opened(tmp_path, clock) as m:
        m.record(tmp_path / "01.m4a", META)
        m.forget(tmp_path / "01.m4a")
        assert not m.recent(tmp_path / "01.m4a", META)


def test_zero_days_always_retries(tmp_path, clock):
    with opened(tmp_path, clock, days=0) as m:
        m.record(tmp_path / "01.m4a", META)
        assert not m.recent(tmp_path / "01.m4a", META)


@pytest.mark.parametrize("value, days", [(None, 30), ("7", 7), ("0", 0), ("-3", 0), ("soon", 30)])
def test_retry_days_from_env(monkeypatch, value, days):
    if value is None:
        monkeypatch.delenv("LYRICS_RETRY_DAYS", raising=False)
    else:
        monkeypatch.setenv("LYRICS_RETRY_DAYS", value)
    assert misses.retry_days() == days


def test_recorded_lists_the_misses_within_the_delay(tmp_path):
    clock = [1000.0]
    with Misses(tmp_path, days=30, now=lambda: clock[0]) as misses:
        misses.record(tmp_path / "old.m4a", {"artist": "A", "title": "T"})
        clock[0] += 31 * 86400
        misses.record(tmp_path / "new.m4a", {"artist": "A", "title": "U"})
    assert recorded(tmp_path, days=30, now=lambda: clock[0]) == {tmp_path / "new.m4a": clock[0]}
    assert recorded(tmp_path / "nothing", days=30) == {}
