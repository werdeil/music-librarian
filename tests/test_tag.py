"""tag: tag fields rewritten, then the tracks filed again (tag.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import struct

import pytest

from music_librarian import cli, tag
from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.media import fields, tags


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


def test_replaygain_removed_then_undone(lib, run, capsys):
    track = lib / "Beyonce/4/01 Run the World.m4a"
    tags.write_replaygain(track, -7.12, 0.98)
    held = fields.replaygain(track)
    assert run(lib / "Beyonce/4", "--set", "replaygain=", "--apply") == 0
    out = capsys.readouterr().out
    assert 'TAG      replaygain: "track gain -7.12 dB" → "(none)"' in out
    assert "written: 1" in out and "unchanged: 1" in out
    assert fields.replaygain(track) == []
    [journal] = (run.state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(run.state), "--apply"]) == 0
    assert fields.replaygain(track) == held


def test_replaygain_takes_no_value(lib, run):
    with pytest.raises(SystemExit):
        run(lib / "Beyonce/4", "--set", "replaygain=-7 dB")


@pytest.mark.parametrize("setting", ["title=Movement 1; Largo", "album=4; 5"])
def test_a_separator_in_a_title_or_an_album_refused(lib, run, setting, capsys):
    with pytest.raises(SystemExit):
        run(lib / "Beyonce/4", "--set", setting)
    assert 'takes one value, without ";"' in capsys.readouterr().err


def test_a_separator_refused_from_the_dashboard(lib, tmp_path, capsys):
    track = lib / "Beyonce/4/01 Run the World.m4a"
    opts = tag.Options(root=lib, state_dir=tmp_path / "state", apply=True, edits=[
        tag.Edit(track, "title", "Run; the World", seen=["Run the World"])])
    assert tag.run(opts, Log()) == 2
    assert fields.read(track)["title"] == ["Run the World"]
    assert 'ERROR    (title takes one value, without ";")' in capsys.readouterr().out


def test_preview_lists_the_writes_moves_and_problems_and_writes_nothing(lib, tmp_path, capsys):
    one, two = lib / "Beyonce/4/01 Run the World.m4a", lib / "Beyonce/4/02 Best Thing.m4a"
    before = files(lib)
    shown = tag.preview(tag.Options(root=lib, state_dir=tmp_path / "state", edits=[
        tag.Edit(one, "albumartist", "Beyoncé"), tag.Edit(two, "albumartist", "Beyoncé"),
        tag.Edit(two, "title", "Best", seen=["Other"]),
    ]))
    assert [(p.name, name, old, new) for p, name, old, new in shown.writes] == [
        ("01 Run the World.m4a", "albumartist", ["Beyonce"], ["Beyoncé"]),
        ("02 Best Thing.m4a", "albumartist", ["Beyonce"], ["Beyoncé"])]
    assert sorted(dst.relative_to(lib).as_posix() for _, dst in shown.moves) == [
        "Beyoncé/4/01 Run the World.m4a", "Beyoncé/4/02 Best Thing.m4a", "Beyoncé/4/folder.jpg"]
    assert shown.problems == ['REFUSED  (title changed since: "Best Thing")  Beyonce/4/02 Best Thing.m4a']
    assert files(lib) == before and not (tmp_path / "state/journal").exists()
    assert capsys.readouterr().out == ""


def test_problem_lines_keep_their_continuation():
    lines = ["TAG      x", "CONFLICT a\n         → b", "         (taken)", "MOVE     c", "         → d",
             "ERROR    e"]
    assert tag.problem_lines(lines) == ["CONFLICT a\n         → b\n         (taken)", "ERROR    e"]


@pytest.fixture
def arcadium(lib, make_track):
    folder = lib / "Red Hot Chili Peppers/Stadium Arcadium"
    for disc, grouping in ((1, "Jupiter"), (2, "Mars")):
        for n in (1, 2):
            p = make_track(folder / f"{disc}-0{n} Song {disc}{n}.m4a", title=f"Song {disc}{n}",
                           artist="Red Hot Chili Peppers", albumartist="Red Hot Chili Peppers",
                           album="Stadium Arcadium", track=n, disc=disc, disctotal=2)
            fields.write(p, "grouping", [grouping])
    return folder


def test_disc_names_copied_from_the_grouping_then_undone(lib, run, arcadium, capsys):
    fields.write(lib / "Beyonce/4/01 Run the World.m4a", "grouping", ["Single"])
    assert run(lib, "--disc-names") == 0
    out = capsys.readouterr().out
    assert 'TAG      discsubtitle: "(none)" → "Mars"' in out
    assert "KEPT     (grouping not one per disc)  Beyonce/4" in out
    assert fields.read(arcadium / "2-01 Song 21.m4a")["discsubtitle"] == []
    assert run(lib, "--disc-names", "--apply") == 0
    values = fields.read(arcadium / "2-01 Song 21.m4a")
    assert values["discsubtitle"] == ["Mars"] and values["grouping"] == ["Mars"]
    assert fields.read(arcadium / "1-02 Song 12.m4a")["discsubtitle"] == ["Jupiter"]
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["discsubtitle"] == []
    [journal] = (run.state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(run.state), "--apply"]) == 0
    assert fields.read(arcadium / "2-01 Song 21.m4a")["discsubtitle"] == []


@pytest.mark.parametrize("groupings", [("Jupiter", "Jupiter"), ("Jupiter", None)])
def test_disc_names_left_when_the_grouping_is_not_one_per_disc(lib, run, arcadium, groupings, capsys):
    for n, grouping in zip((1, 2), groupings):
        fields.write(arcadium / f"2-0{n} Song 2{n}.m4a", "grouping", [grouping] if grouping else [])
    assert run(lib, "--disc-names", "--apply") == 0
    out = capsys.readouterr().out
    assert "KEPT     (grouping not one per disc)  Red Hot Chili Peppers/Stadium Arcadium" in out
    assert fields.read(arcadium / "1-01 Song 11.m4a")["discsubtitle"] == []


def test_disc_names_keep_a_disc_name_already_there(lib, run, arcadium):
    fields.write(arcadium / "1-01 Song 11.m4a", "discsubtitle", ["Jupiter (Disc 1)"])
    assert run(lib, "--disc-names", "--apply") == 0
    assert fields.read(arcadium / "1-01 Song 11.m4a")["discsubtitle"] == ["Jupiter (Disc 1)"]
    assert fields.read(arcadium / "1-02 Song 12.m4a")["discsubtitle"] == ["Jupiter"]


def test_disc_names_report_a_path_that_is_no_track_of_the_library(lib, run, tmp_path, capsys):
    (lib / "Beyonce/4/notes.txt").write_text("")
    for path in (tmp_path / "Library", lib / "Beyonce/9", lib / "Beyonce/4/notes.txt"):
        assert run(path, "--disc-names") == 2
        out = capsys.readouterr().out
        assert "SKIPPED  (not a track of the library)" in out and "skipped: 1" in out


@pytest.mark.usefixtures("arcadium")
def test_disc_names_read_only_the_albums_the_cache_finds_a_grouping_in(lib, run, monkeypatch):
    read = []
    real = fields.read
    monkeypatch.setattr(fields, "read", lambda path: read.append(path.parent.name) or real(path))
    assert run(lib, "--disc-names") == 0
    assert set(read) == {"Stadium Arcadium"}


def test_cover_put_with_the_tags_then_filed_and_undone(lib, tmp_path, capsys):
    new = b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 17, 8, 600, 600, 3) + b"\x01\x11\x00" * 3
    image = tmp_path / "new.jpg"
    image.write_bytes(new)
    state = tmp_path / "state"

    def opts(apply):
        album = lib / "Beyonce/4"
        edits = [tag.Edit(path, "albumartist", "Beyoncé") for path in sorted(album.glob("*.m4a"))]
        return tag.Options(root=lib, edits=edits, apply=apply, state_dir=state, cover=(album, image))

    assert tag.run(opts(False), Log()) == 0
    out = capsys.readouterr().out
    assert "COVER    folder.jpg  600x600, replacing an unreadable image  Beyonce/4" in out
    assert "WOULD    embed 600x600 into 2 of 2 tracks" in out and "to embed: 2" in out
    assert (lib / "Beyonce/4/folder.jpg").read_bytes() == b"jpg"

    assert tag.run(opts(True), Log()) == 0
    assert "embedded: 2" in capsys.readouterr().out
    assert (lib / "Beyoncé/4/folder.jpg").read_bytes() == new
    assert tags.read_cover(lib / "Beyoncé/4/01 Run the World.m4a") == new
    [journal] = (state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(state), "--apply"]) == 0
    assert (lib / "Beyonce/4/folder.jpg").read_bytes() == b"jpg"


def test_cover_outside_the_library_refused(lib, tmp_path, capsys):
    opts = tag.Options(root=lib, state_dir=tmp_path / "state", cover=(tmp_path, tmp_path / "x.jpg"))
    assert tag.run(opts, Log()) == 2
    assert "REFUSED  (not an album folder of the library)" in capsys.readouterr().out
