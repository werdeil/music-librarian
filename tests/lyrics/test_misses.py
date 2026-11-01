"""lyrics.misses: lookups that found nothing, retried after a delay or a tag change."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import sqlite3
from pathlib import Path

import pytest

from music_librarian.lyrics import misses
from music_librarian.lyrics.misses import Misses, instrumentals, recorded

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


def test_instrumental_kept_past_the_delay_until_the_tags_change(tmp_path, clock):
    with opened(tmp_path, clock) as m:
        m.record(tmp_path / "01.m4a", META, instrumental=True)
        assert m.instrumental(tmp_path / "01.m4a", META) and not m.recent(tmp_path / "01.m4a", META)
    clock[0] += 365 * DAY
    with opened(tmp_path, clock) as m:
        assert m.instrumental(tmp_path / "01.m4a", META)
        assert not m.instrumental(tmp_path / "01.m4a", {**META, "title": "Title"})
    assert recorded(tmp_path / "state", now=lambda: clock[0]) == {}
    assert instrumentals(tmp_path / "state") == {tmp_path / "01.m4a": misses.query(META)}


def test_a_record_from_before_instrumentals_is_read(tmp_path, clock):
    state = tmp_path / "state"
    state.mkdir()
    db = sqlite3.connect(state / misses.FILE)
    db.execute("CREATE TABLE misses (path BLOB PRIMARY KEY, query TEXT, checked_at REAL)")
    db.execute("INSERT INTO misses VALUES (?, ?, ?)", (b"/x/01.m4a", misses.query(META), clock[0]))
    db.commit()
    db.close()
    assert instrumentals(state) == {} and len(recorded(state, now=lambda: clock[0])) == 1
    with opened(tmp_path, clock) as m:
        assert m.recent("/x/01.m4a", META)
        m.record("/x/02.m4a", META, instrumental=True)
    assert list(instrumentals(state)) == [Path("/x/02.m4a")]
