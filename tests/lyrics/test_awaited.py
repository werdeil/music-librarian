"""lyrics.awaited: the tracks whose lyrics are awaited, kept in a text file."""
from music_librarian.lyrics import awaited

TRACK = {"artist": "Beyoncé", "title": "Halo", "album": "I Am... Sasha Fierce", "duration": 261.6}


def held(state, **changes):
    return awaited.Awaited(state).holds({**TRACK, **changes})


def test_a_track_marked_is_held_whatever_its_case_and_accents(tmp_path):
    assert not held(tmp_path)
    awaited.mark(tmp_path, [TRACK])
    assert (tmp_path / awaited.FILE).read_text(encoding="utf-8").splitlines()[-1] == \
        "Beyoncé — Halo — I Am... Sasha Fierce — 4:21"
    assert held(tmp_path, artist="BEYONCE", title=" halo ")
    assert held(tmp_path, duration=263) and not held(tmp_path, duration=266)
    assert not held(tmp_path, album="B'Day") and not held(tmp_path, title="Halo (Live)")


def test_marked_twice_written_once_then_unmarked(tmp_path):
    awaited.mark(tmp_path, [TRACK])
    awaited.mark(tmp_path, [TRACK, None])
    text = (tmp_path / awaited.FILE).read_text(encoding="utf-8")
    assert text.startswith("# ") and text.count("Halo") == 1
    awaited.unmark(tmp_path, [{**TRACK, "artist": "beyonce"}])
    assert not held(tmp_path) and text.startswith("# ")


def test_lines_written_by_hand(tmp_path):
    (tmp_path / awaited.FILE).write_text("# a comment\nnot a track\nMoby — Porcelain\n"
                                         "Daft Punk — One More Time —  — \nA—B — C — D — 1:00 — E\n",
                                         encoding="utf-8")
    marked = awaited.Awaited(tmp_path)
    assert marked.holds({"artist": "Moby", "title": "Porcelain", "album": None, "duration": 241})
    assert marked.holds({"artist": "Daft Punk", "title": "One More Time", "album": "", "duration": None})
    assert not marked.holds({"artist": "Moby", "title": "Porcelain", "album": "Play", "duration": 241})
    assert len(marked.marked) == 2


def test_a_dash_in_a_name_kept_apart_from_the_separator(tmp_path):
    track = {"artist": "Artist", "title": "Intro — Outro", "album": None, "duration": None}
    awaited.mark(tmp_path, [track])
    lines = (tmp_path / awaited.FILE).read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "Artist — Intro - Outro —  —"
    assert awaited.Awaited(tmp_path).holds(track)
