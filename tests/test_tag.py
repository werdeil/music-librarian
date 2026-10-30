"""tag: tag fields rewritten, then the tracks filed again (tag.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import cli, tag
from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.media import fields


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for n, title in ((1, "Run the World"), (2, "Best Thing")):
        make_track(root / f"Beyonce/4/0{n} {title}.m4a", title=title, artist="Beyonce",
                   albumartist="Beyonce", album="4", track=n)
    (root / "Beyonce/4/folder.jpg").write_bytes(b"jpg")
    return root


@pytest.fixture
def run(lib, tmp_path):
    state = tmp_path / "state"

    def run_tag(*args):
        return tag.main(["--root", str(lib), "--state-dir", str(state), *map(str, args)])

    run_tag.state = state
    return run_tag


def files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_dry_run_shows_tags_and_moves(lib, run, capsys):
    before = files(lib)
    assert run(lib / "Beyonce/4", "--set", "albumartist=Beyoncé", "--set", "year=2011") == 0
    out = capsys.readouterr().out
    assert files(lib) == before and fields.read(lib / "Beyonce/4/01 Run the World.m4a")["year"] == []
    assert 'TAG      albumartist: "Beyonce" → "Beyoncé"' in out and 'TAG      year: "(none)" → "2011"' in out
    assert "MOVE     Beyonce/4/01 Run the World.m4a\n         → Beyoncé/4/01 Run the World.m4a" in out
    assert "→ Beyoncé/4/folder.jpg" in out
    assert "tags to write: 4" in out and "to move: 3" in out


def test_apply_writes_and_refiles(lib, run, capsys):
    assert run(lib / "Beyonce/4", "--set", "albumartist=Beyoncé", "--apply") == 0
    assert files(lib) == ["Beyoncé/4/01 Run the World.m4a", "Beyoncé/4/02 Best Thing.m4a",
                          "Beyoncé/4/folder.jpg"]
    assert fields.read(lib / "Beyoncé/4/01 Run the World.m4a")["albumartist"] == ["Beyoncé"]
    out = capsys.readouterr().out
    assert "written: 2" in out and "moved: 3" in out
    [journal] = (run.state / "journal").glob("*.jsonl")
    assert f"journal: {journal}" in out


def test_undo_restores_tags_and_paths(lib, run):
    assert run(lib / "Beyonce/4/01 Run the World.m4a", "--set", "title=Run the World (Girls)",
               "--set", "track=7", "--apply") == 0
    assert files(lib)[0] == "Beyonce/4/02 Best Thing.m4a"
    assert "Beyonce/4/07 Run the World (Girls).m4a" in files(lib)
    [journal] = (run.state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(run.state), "--apply"]) == 0
    track = lib / "Beyonce/4/01 Run the World.m4a"
    assert track.exists() and "Beyonce/4/07 Run the World (Girls).m4a" not in files(lib)
    assert (fields.read(track)["title"], fields.read(track)["track"]) == (["Run the World"], ["1"])


def test_undo_refuses_a_tag_changed_since(lib, run, capsys):
    run(lib / "Beyonce/4", "--set", "year=2011", "--apply")
    fields.write(lib / "Beyonce/4/02 Best Thing.m4a", "year", ["2012"])
    [journal] = (run.state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(run.state), "--apply"]) == 2
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["year"] == []
    assert fields.read(lib / "Beyonce/4/02 Best Thing.m4a")["year"] == ["2012"]
    assert "REFUSED  (year changed since)" in capsys.readouterr().out


def test_edit_refused_when_the_track_changed_since_shown(lib, tmp_path, capsys):
    track = lib / "Beyonce/4/01 Run the World.m4a"
    opts = tag.Options(root=lib, state_dir=tmp_path / "state", apply=True, edits=[
        tag.Edit(track, "albumartist", "Beyoncé", seen=["Beyoncé Knowles"]),
        tag.Edit(track, "year", "2011", seen=[]),
    ])
    assert tag.run(opts, Log()) == 2
    assert fields.read(track)["albumartist"] == ["Beyonce"] and fields.read(track)["year"] == ["2011"]
    assert 'REFUSED  (albumartist changed since: "Beyonce")' in capsys.readouterr().out


def test_genre_takes_the_validated_spelling(lib, run, capsys):
    run.state.mkdir()
    (run.state / "genres.txt").write_text("R&B: rnb, rhythm and blues\n", encoding="utf-8")
    assert run(lib / "Beyonce/4/01 Run the World.m4a", "--set", "genre=RnB", "--apply") == 0
    assert run(lib / "Beyonce/4/02 Best Thing.m4a", "--set", "genre=Soul", "--apply") == 0
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["genre"] == ["R&B"]
    assert fields.read(lib / "Beyonce/4/02 Best Thing.m4a")["genre"] == ["Soul"]
    assert 'UNKNOWN  genre "Soul" is not in the validated list' in capsys.readouterr().out


def test_unchanged_and_cleared_fields(lib, run, capsys):
    assert run(lib / "Beyonce/4", "--set", "album=4", "--set", "artist=", "--apply") == 0
    out = capsys.readouterr().out
    assert "unchanged: 2" in out and 'TAG      artist: "Beyonce" → "(none)"' in out
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["artist"] == []


@pytest.mark.parametrize("setting", ["track=zero", "rating=5", "title"])
def test_bad_setting_refused_before_anything(lib, run, setting):
    with pytest.raises(SystemExit):
        run(lib / "Beyonce/4", "--set", setting)


def test_track_outside_the_library_skipped(run, tmp_path, make_track, capsys):
    outside = make_track(tmp_path / "Inbox/x.m4a", title="X", artist="A", album="B", track=1)
    assert run(outside, "--set", "title=Y", "--apply") == 2
    assert fields.read(outside)["title"] == ["X"]
    assert "SKIPPED  (not a track of the library)" in capsys.readouterr().out


def test_no_refile(lib, run):
    assert run(lib / "Beyonce/4", "--set", "albumartist=Beyoncé", "--no-refile", "--apply") == 0
    assert files(lib)[0] == "Beyonce/4/01 Run the World.m4a"
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["albumartist"] == ["Beyoncé"]


def test_recorded_in_the_history(lib, run):
    run(lib / "Beyonce/4", "--set", "year=2011")
    [record] = runs.recent(run.state)
    assert (record["command"], record["apply"]) == ("tag", False)
    assert "tags to write: 2" in record["summary"]


def test_undo_restores_the_tag_where_a_refused_move_left_it(lib, run, capsys):
    run(lib / "Beyonce/4/01 Run the World.m4a", "--set", "track=7", "--apply")
    moved = lib / "Beyonce/4/07 Run the World.m4a"
    blocker = lib / "Beyonce/4/01 Run the World.m4a"
    blocker.write_bytes(b"another file")
    [journal] = (run.state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(run.state), "--apply"]) == 2
    assert fields.read(moved)["track"] == ["1"] and blocker.read_bytes() == b"another file"
    assert "REFUSED  (original place taken)" in capsys.readouterr().out
