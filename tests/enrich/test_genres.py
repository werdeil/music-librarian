"""enrich.genres: genres aligned with the validated list."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import os

import pytest

from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.enrich import genres
from music_librarian.media import tags

LIST = """\
# validated genres
Rock
Rap / Hip-hop / RnB: hip hop, rap, r&b
Électronique: electronic, electronica
Chanson Française
"""


@pytest.fixture
def genre_list(tmp_path):
    p = tmp_path / "genres.txt"
    p.write_text(LIST, encoding="utf-8")
    return p


@pytest.fixture
def album(tmp_path, make_track):
    """One track per format, each with the genre given by `tag`."""
    d = tmp_path / "Musique" / "Artiste" / "Album"

    def tag(*values):
        out = []
        for i, ext in enumerate((".m4a", ".mp3", ".flac"), 1):
            p = make_track(d / f"0{i} Titre{ext}", title="Titre")
            if values:
                tags.write_genre(str(p), values[0])
            os.utime(p, (1_000_000_000, 1_000_000_000))
            out.append(p)
        return d, out
    return tag


def run(genre_list, *targets, dry=False):
    return genres.main([*map(str, targets), "--genres", str(genre_list), *(["--dry-run"] if dry else [])])


@pytest.mark.parametrize("value, expected", [
    ("rock", "Rock"), ("ROCK", "Rock"), ("Hip-Hop", "Rap / Hip-hop / RnB"),
    ("Rap / Hip-Hop / RnB", "Rap / Hip-hop / RnB"), ("R&B", "Rap / Hip-hop/ RnB"),
    ("electronique", "Électronique"), ("chanson francaise", "Chanson Française"),
    ("Electronica/Dance", "Électronique"), ("Shoegaze; Rock", "Rock"), ("Shoegaze", None),
])
def test_resolve(genre_list, value, expected):
    assert genres.resolve([value], genres.load(genre_list)) == expected


def test_alias_claimed_twice(tmp_path):
    p = tmp_path / "genres.txt"
    p.write_text("Rock: hard rock\nRock Dur: Hard-Rock\n", encoding="utf-8")
    with pytest.raises(genres.GenreListError, match="already stands for"):
        genres.load(p)


def test_retagged_with_mtime_kept(genre_list, album, capsys):
    d, tracks = album("hip hop")
    assert run(genre_list, d) == 0
    assert all(tags.read_genres(str(t)) == ["Rap / Hip-hop / RnB"] for t in tracks)
    assert os.stat(tracks[0]).st_mtime == 1_000_000_001
    out = capsys.readouterr().out
    assert 'GENRE    Artiste/Album  "hip hop" → "Rap / Hip-hop / RnB" (3 track(s))' in out
    assert "re-tagged: 3" in out


def test_already_aligned_left_alone(genre_list, album, capsys):
    d, tracks = album("Rock")
    assert run(genre_list, d) == 0
    assert os.stat(tracks[0]).st_mtime == 1_000_000_000
    assert "re-tagged: 0" in capsys.readouterr().out


def test_unknown_kept_and_reported(genre_list, album, capsys):
    d, tracks = album("Shoegaze")
    assert run(genre_list, d) == 0
    assert tags.read_genres(str(tracks[0])) == ["Shoegaze"]
    out = capsys.readouterr().out
    assert 'UNKNOWN  Artiste/Album  "Shoegaze" (3 track(s))' in out and "unknown genre: 3" in out


def test_without_genre_reported(genre_list, album, capsys):
    d, _ = album()
    assert run(genre_list, d) == 0
    out = capsys.readouterr().out
    assert "NOGENRE  Artiste/Album  (3 track(s))" in out and "without genre: 3" in out


def test_dry_run_writes_nothing(genre_list, album, capsys):
    d, tracks = album("rock")
    assert run(genre_list, d, dry=True) == 0
    assert tags.read_genres(str(tracks[0])) == ["rock"]
    assert 'WOULD    Artiste/Album  "rock" → "Rock"' in capsys.readouterr().out


def test_no_list_does_nothing(tmp_path, album, capsys):
    d, tracks = album("rock")
    assert run(tmp_path / "absent.txt", d) == 0
    assert tags.read_genres(str(tracks[0])) == ["rock"]
    assert "SKIPPED  no genre list" in capsys.readouterr().out


def test_list_from_state_folder(tmp_path, album, monkeypatch):
    d, tracks = album("rock")
    state = tmp_path / "state"
    state.mkdir()
    (state / "genres.txt").write_text("Rock\n", encoding="utf-8")
    monkeypatch.setenv("MUSIC_LIBRARIAN_STATE", str(state))
    assert genres.main([str(d)]) == 0
    assert tags.read_genres(str(tracks[0])) == ["Rock"]


def test_write_failure_reported(genre_list, album, capsys, monkeypatch):
    d, _ = album("rock")

    def broken(path, genre):
        raise tags.GenreTagError("damaged header")

    monkeypatch.setattr(tags, "write_genre", broken)
    assert run(genre_list, d) == 2
    out = capsys.readouterr().out
    assert out.count("damaged header") == 3 and "failed: 3" in out


def test_stop_between_albums(genre_list, album, capsys):
    d, tracks = album("rock")
    STOP.set()
    assert run(genre_list, d) == EXIT_STOPPED
    assert tags.read_genres(str(tracks[0])) == ["rock"]
