"""enrich.genres: genres aligned with the validated list."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import os

import pytest

from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.enrich import genres
from music_librarian.integrations import itunes
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
            p = make_track(d / f"0{i} Titre{ext}", title="Titre", artist="Artiste", album="Album")
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
    ("Rap / Hip-Hop / RnB", "Rap / Hip-hop / RnB"), ("R&B", "Rap / Hip-hop / RnB"),
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
    assert 'GENRE    Artiste/Album  "hip hop" → "Rap / Hip-hop / RnB" (3 tracks)' in out
    assert "re-tagged: 3" in out


def test_already_aligned_left_alone(genre_list, album, capsys):
    d, tracks = album("Rock")
    assert run(genre_list, d) == 0
    assert os.stat(tracks[0]).st_mtime == 1_000_000_000
    assert "re-tagged:" not in capsys.readouterr().out


def test_unknown_kept_and_reported(genre_list, album, capsys):
    d, tracks = album("Shoegaze")
    assert run(genre_list, d) == 0
    assert tags.read_genres(str(tracks[0])) == ["Shoegaze"]
    out = capsys.readouterr().out
    assert 'UNKNOWN  Artiste/Album  "Shoegaze" (3 tracks)' in out and "unknown genre: 3" in out


def test_without_genre_reported(genre_list, album, capsys):
    d, _ = album()
    assert run(genre_list, d) == 0
    out = capsys.readouterr().out
    assert "NOGENRE  Artiste/Album  (3 tracks)" in out and "without genre: 3" in out


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


def test_second_pass_reads_from_cache(genre_list, album, monkeypatch, capsys):
    d, _ = album("rock")
    assert run(genre_list, d, dry=True) == 0
    monkeypatch.setattr(tags, "read_snapshot", lambda path: pytest.fail(f"{path} read again"))
    assert run(genre_list, d, dry=True) == 0
    assert capsys.readouterr().out.count('"rock" → "Rock" (3 tracks)') == 2


# ---------- iTunes lookup ----------

class FakeItunes:
    def __init__(self, genre=None, down=False):
        self.genre, self.down, self.calls = genre, down, []

    def album_genre(self, artist, album):
        self.calls.append((artist, album))
        if self.down:
            raise itunes.Unavailable("HTTP 403")
        return self.genre


@pytest.fixture
def lookup(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("MUSIC_LIBRARIAN_STATE", str(state))

    def use(fake):
        monkeypatch.setattr(itunes, "from_env", lambda: fake)
        return state / genres.LOOKUP_CACHE
    return use


def test_unknown_takes_itunes_genre(genre_list, album, lookup, capsys):
    fake = FakeItunes("Hip-Hop/Rap")
    lookup(fake)
    d, tracks = album("Shoegaze")
    assert run(genre_list, d) == 0
    assert all(tags.read_genres(str(t)) == ["Rap / Hip-hop / RnB"] for t in tracks)
    assert fake.calls == [("Artiste", "Album")]
    out = capsys.readouterr().out
    assert ('ITUNES   Artiste/Album  "Shoegaze" → "Rap / Hip-hop / RnB" '
            '(iTunes "Hip-Hop/Rap", 3 tracks)') in out
    assert "from iTunes: 3" in out


def test_missing_takes_itunes_genre(genre_list, album, lookup, capsys):
    lookup(FakeItunes("Rock"))
    d, tracks = album()
    assert run(genre_list, d) == 0
    assert tags.read_genres(str(tracks[0])) == ["Rock"]
    assert 'ITUNES   Artiste/Album  (no genre) → "Rock"' in capsys.readouterr().out


def test_aligned_album_not_searched(genre_list, album, lookup):
    fake = FakeItunes("Rock")
    lookup(fake)
    d, _ = album("hip hop")
    assert run(genre_list, d) == 0
    assert not fake.calls


def test_itunes_genre_unknown_to_the_list(genre_list, album, lookup, capsys):
    lookup(FakeItunes("Shoegaze Revival"))
    d, tracks = album("Shoegaze")
    assert run(genre_list, d) == 0
    assert tags.read_genres(str(tracks[0])) == ["Shoegaze"]
    assert 'UNKNOWN  Artiste/Album  "Shoegaze" (3 tracks, iTunes "Shoegaze Revival")' in \
        capsys.readouterr().out


def test_answers_cached_between_passes(genre_list, album, lookup):
    fake = FakeItunes(None)
    cache = lookup(fake)
    d, _ = album("Shoegaze")
    assert run(genre_list, d) == 0 and run(genre_list, d) == 0
    assert len(fake.calls) == 1 and cache.exists()


def test_dry_run_counts_the_albums_to_look_up(genre_list, album, lookup, capsys):
    fake = FakeItunes("Rock")
    cache = lookup(fake)
    d, tracks = album("Shoegaze")
    assert run(genre_list, d, dry=True) == 0
    assert not fake.calls and not cache.exists() and tags.read_genres(str(tracks[0])) == ["Shoegaze"]
    out = capsys.readouterr().out
    assert "albums to look up: 1" in out and f"estimated time: under a minute ({itunes.INTERVAL:.0f} s)" in out
    assert 'UNKNOWN  Artiste/Album  "Shoegaze" (3 tracks)' in out


def test_dry_run_takes_the_answers_kept(genre_list, album, lookup, capsys):
    fake = FakeItunes("Shoegaze Revival")
    lookup(fake)
    d, _ = album("Shoegaze")
    assert run(genre_list, d) == 0 and len(fake.calls) == 1
    capsys.readouterr()
    assert run(genre_list, d, dry=True) == 0
    out = capsys.readouterr().out
    assert len(fake.calls) == 1 and "albums to look up" not in out
    assert 'UNKNOWN  Artiste/Album  "Shoegaze" (3 tracks, iTunes "Shoegaze Revival")' in out


def test_itunes_down_is_not_a_failure(genre_list, album, lookup, capsys):
    fake = FakeItunes(down=True)
    lookup(fake)
    d, _ = album("Shoegaze")
    d2 = d.parent / "Autre"
    for t in d.iterdir():
        (d2 / t.name).parent.mkdir(exist_ok=True)
        (d2 / t.name).write_bytes(t.read_bytes())
    assert run(genre_list, d.parent) == 0
    out = capsys.readouterr().out
    assert out.count("OFFLINE  iTunes: HTTP 403") == 1 and len(fake.calls) == 1
    assert "unknown genre: 6" in out


def test_list_edited_and_taken_back(tmp_path):
    path = tmp_path / "genres.txt"
    path.write_text("# comment\nRock: hard rock\nJazz\n", encoding="utf-8")
    genres.add_to_list(path, "rock", "Rock, Pop")
    genres.add_to_list(path, "Jazz", "Bebop")
    genres.add_to_list(path, "Zydeco")
    assert path.read_text(encoding="utf-8") == "# comment\nRock: hard rock, Rock  Pop\nJazz: Bebop\nZydeco\n"
    assert genres.load(path)[genres.key("rock pop")] == "Rock"
    genres.remove_from_list(path, "Rock", "Rock, Pop")
    genres.remove_from_list(path, "Jazz", "Bebop")
    genres.remove_from_list(path, "Zydeco")
    assert path.read_text(encoding="utf-8") == "# comment\nRock: hard rock\nJazz\n"
