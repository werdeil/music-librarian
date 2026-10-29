"""Full passes over a small library: dry run, apply, undo, --since."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import json
import os
import time

import pytest

from music_librarian.media import atoms, faststart, tags
from music_librarian.organize import organize


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Musique"
    root.mkdir()
    return root


@pytest.fixture
def run(tmp_path, lib):
    state = tmp_path / "state"

    def run(*args):
        return organize.main(["--root", str(lib), "--state-dir", str(state), *args])

    run.state = state
    return run


def journals(run):
    return sorted((run.state / "journal").glob("*.jsonl"))


def files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_dry_run_changes_nothing(lib, run, make_track, capsys):
    make_track(lib / "mauvais/endroit.m4a", title="T", artist="A", album="B", track=1)
    (lib / "mauvais/.DS_Store").write_bytes(b"x")
    before = files(lib)
    assert run() == 0
    assert files(lib) == before
    assert not [p for p in run.state.iterdir() if not p.name.startswith("metadata.db")]
    assert "MOVE" in capsys.readouterr().out


def test_apply_then_undo(lib, run, make_track, capsys):
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "vrac/2.m4a", title="Deux", artist="A", album="B", track=2)
    (lib / "vrac/folder.jpg").write_bytes(b"jpg")
    (lib / "vrac/._1.m4a").write_bytes(b"x")
    make_track(lib / "C/D/01 Rien.m4a", title="Rien", artist="C", album="D", track=1)

    assert run("--apply") == 0
    assert files(lib) == ["A/B/01 Un.m4a", "A/B/02 Deux.m4a", "A/B/folder.jpg", "C/D/01 Rien.m4a"]
    assert not (lib / "vrac").exists()

    [j] = journals(run)
    # undo command ready to copy, valid in the container
    assert f"music-librarian undo {j} --root {lib} --apply" in capsys.readouterr().out
    recs = [json.loads(line) for line in j.read_text().splitlines()]
    assert recs[0]["type"] == "meta"
    assert len([r for r in recs if r["type"] == "move"]) == 3

    # dry-run undo: nothing moves
    assert run("--undo", str(j)) == 0
    assert "A/B/01 Un.m4a" in files(lib)

    assert run("--undo", str(j), "--apply") == 0
    # the deleted junk file is not restored
    assert files(lib) == ["C/D/01 Rien.m4a", "vrac/1.m4a", "vrac/2.m4a", "vrac/folder.jpg"]
    assert not (lib / "A").exists()
    # the undo has its own journal
    assert len(journals(run)) == 2


def test_undo_refuses_when_place_taken(lib, run, make_track):
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert run("--apply") == 0
    [j] = journals(run)
    (lib / "vrac").mkdir()
    (lib / "vrac/1.m4a").write_bytes(b"autre")
    assert run("--undo", str(j), "--apply") == 2
    assert (lib / "A/B/01 Un.m4a").exists()


def test_x92_file_renamed_and_undone(lib, run, make_track):
    d = lib / "Queens of the Stone Age/Villains"
    bad = make_track(os.fsencode(d) + b"/03 Feet Don\x92t Fail Me.m4a",
                     title="Feet Don’t Fail Me", albumartist="Queens of the Stone Age",
                     album="Villains", track=3)
    assert run("--apply") == 0
    assert files(lib) == ["Queens of the Stone Age/Villains/03 Feet Don_t Fail Me.m4a"]
    [j] = journals(run)
    assert run("--undo", str(j), "--apply") == 0
    assert bad.exists()


def test_u0092_file_renamed_and_shown(lib, run, make_track, capsys):
    d = lib / "Queens of the Stone Age/Villains"
    make_track(d / "01 Feet Don\u0092't Fail Me.m4a", title="Feet Don't Fail Me",
               albumartist="Queens of the Stone Age", album="Villains", track=1)
    assert run("--apply") == 0
    assert files(lib) == ["Queens of the Stone Age/Villains/01 Feet Don't Fail Me.m4a"]
    assert "Don\\x92't" in capsys.readouterr().out


def test_compilation_conflict_keeps_album_together(lib, run, make_track, capsys):
    d = lib / "Divers/Best Of"
    make_track(d / "1.m4a", title="Un", artist="X", album="Best Of", track=1, compilation=True)
    make_track(d / "2.m4a", title="Deux", artist="Y", album="Best Of", track=2)
    (d / "folder.jpg").write_bytes(b"jpg")
    before = files(lib)
    assert run("--apply") == 2
    assert files(lib) == before
    assert "inconsistent cpil: 1/2" in capsys.readouterr().out


def test_coherent_compilation(lib, run, make_track):
    for i, artist in enumerate(("X", "Y"), 1):
        make_track(lib / f"vrac/{i}.mp3", title=f"T{i}", artist=artist, album="Best Of",
                   track=i, compilation=True)
    assert run("--apply") == 0
    assert files(lib) == ["Compilations/Best Of/01 T1.mp3", "Compilations/Best Of/02 T2.mp3"]


def test_annexes_stay_when_a_track_is_left_behind(lib, run, make_track):
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "vrac/2.m4a", artist="A", album="B", track=2)   # no title
    (lib / "vrac/folder.jpg").write_bytes(b"jpg")
    assert run("--apply") == 2
    assert files(lib) == ["A/B/01 Un.m4a", "vrac/2.m4a", "vrac/folder.jpg"]


def test_multidisc_flac_from_inbox(tmp_path, lib, run, make_track):
    inbox_dir = tmp_path / "Inbox/Album"
    make_track(inbox_dir / "a.flac", title="Un", artist="A", album="B", track=1, disc=1, disctotal=2)
    make_track(inbox_dir / "b.flac", title="Deux", artist="A", album="B", track=1, disc=2, disctotal=2)
    assert run(str(inbox_dir), "--apply") == 0
    assert files(lib) == ["A/B/1-01 Un.flac", "A/B/2-01 Deux.flac"]
    assert inbox_dir.exists()                 # folder passed as an argument: never removed
    assert not (run.state / "last_run").exists()   # partial pass: no last pass


def test_target_conflict(lib, run, make_track):
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "vrac/autre.m4a", title="Un", artist="A", album="B", track=1)
    assert run("--apply") == 2
    assert (lib / "vrac/autre.m4a").exists()


def test_since_only_rescans_changed_folders(lib, run, make_track, capsys, monkeypatch):
    monkeypatch.setattr(organize, "SINCE_MARGIN", 0.05)  # passes close together
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "A/C/01 Deux.m4a", title="Deux", artist="A", album="C", track=1)
    time.sleep(0.2)  # older than the last_run margin
    # first pass: no state, everything is processed
    assert run("--since", "--apply") == 0
    assert "no known last pass" in capsys.readouterr().out
    assert (run.state / "last_run").exists()

    time.sleep(0.05)
    # tag changed in A/B (as from the Mac): the album changes
    from mutagen.mp4 import MP4  # pylint: disable=import-outside-toplevel
    f = MP4(lib / "A/B/01 Un.m4a")
    f["\xa9alb"] = ["Nouveau"]
    f.save()

    assert run("--since", "--apply") == 0
    out = capsys.readouterr().out
    assert "scanned: 1" in out          # A/C was not read again
    assert files(lib) == ["A/C/01 Deux.m4a", "A/Nouveau/01 Un.m4a"]

    # the move updated the ctime: the file is read once more, in place (even when
    # the ctime, from a coarser clock, is a few ms before the pass start: seen on CI)
    assert run("--since", "--apply") == 0
    assert "in place: 1" in capsys.readouterr().out

    # nothing changed since: nothing is read again
    time.sleep(0.2)
    assert run("--since", "--apply") == 0
    capsys.readouterr()
    assert run("--since") == 0
    assert "scanned" not in capsys.readouterr().out


def test_last_run_keeps_a_margin(lib, run, make_track, monkeypatch):
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    monkeypatch.setattr(organize.time, "time", lambda: 1000.0)
    assert run("--apply") == 0
    assert float((run.state / "last_run").read_text()) == 1000.0 - organize.SINCE_MARGIN


def test_since_dry_run_does_not_advance(lib, run, make_track):
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    assert run("--since") == 0
    assert not (run.state / "last_run").exists()


def test_autoadd_folder_is_harmless(lib, run):
    d = lib / "Automatically Add to Music.localized/.localized"
    d.mkdir(parents=True)
    (d / "fr.strings").write_text("x")
    assert run("--apply") == 0
    assert (d / "fr.strings").exists()


def test_moov_moved_ahead_of_mdat(lib, run, make_slow_m4a, capsys):
    placed = make_slow_m4a(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    stray = make_slow_m4a(lib / "vrac/2.m4a", title="Deux", artist="A", album="B", track=2)
    before = placed.read_bytes()
    assert run() == 0
    assert "REWRITE  (moov after mdat)" in capsys.readouterr().out
    assert placed.read_bytes() == before

    assert run("--apply") == 0
    out = capsys.readouterr().out
    assert "rewritten: 2" in out and "moved: 1" in out
    assert not any(atoms.leading_mdat(p) for p in (placed, lib / "A/B/02 Deux.m4a"))
    assert not stray.exists()
    assert run() == 0 and "REWRITE" not in capsys.readouterr().out


def test_failed_rewrite_reported(lib, run, make_slow_m4a, monkeypatch, capsys):
    p = make_slow_m4a(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)

    def fail(_):
        raise faststart.RewriteError("tags differ after rewriting 01 Un.m4a")

    monkeypatch.setattr(organize, "faststart", fail)
    assert run("--apply") == 2
    assert "ERROR    tags differ" in capsys.readouterr().out
    assert atoms.leading_mdat(p)


def test_unchanged_files_come_from_the_metadata_cache(lib, run, make_track, monkeypatch):
    make_track(lib / "A/B/01 T.m4a", title="T", artist="A", album="B", track=1)
    moved = make_track(lib / "wrong/place.m4a", title="U", artist="A", album="B", track=2)
    assert run() == 0
    read = []
    real = tags.read_snapshot
    monkeypatch.setattr(tags, "read_snapshot", lambda p: read.append(p) or real(p))
    monkeypatch.setattr(organize, "leading_mdat", read.append)
    assert run("--apply") == 0
    assert not read
    assert not moved.exists() and (lib / "A/B/02 U.m4a").exists()


def test_files_outside_the_library_are_not_cached(tmp_path, lib, run, make_track, monkeypatch):
    inbox = make_track(tmp_path / "Inbox/x.m4a", title="T", artist="A", album="B", track=1)
    cached = []
    monkeypatch.setattr(tags, "read_snapshot", cached.append)
    assert run(str(inbox.parent), "--apply") == 0
    assert not cached and (lib / "A/B/01 T.m4a").exists()


def test_case_variant_folder_joins_its_exact_twin(lib, run, make_track):
    make_track(lib / "Walk of the Earth/A/01 T.m4a", title="T", albumartist="Walk of the Earth", album="A",
               track=1)
    make_track(lib / "Walk Of The Earth/B/01 U.m4a", title="U", albumartist="Walk of the Earth", album="B",
               track=1)
    (lib / "Walk Of The Earth/B/folder.jpg").write_bytes(b"jpg")
    assert run("--apply") == 0
    assert files(lib) == ["Walk of the Earth/A/01 T.m4a", "Walk of the Earth/B/01 U.m4a",
                          "Walk of the Earth/B/folder.jpg"]


def test_case_variant_folder_alone_stays(lib, run, make_track):
    make_track(lib / "Abd al Malik/Gibraltar/01 T.m4a", title="T", albumartist="Abd Al Malik",
               album="Gibraltar", track=1)
    make_track(lib / "Alicia Keys/The Element of Freedom/01 T.m4a", title="T", albumartist="Alicia Keys",
               album="The Element Of Freedom", track=1)
    assert run("--apply") == 0
    assert files(lib) == ["Abd al Malik/Gibraltar/01 T.m4a", "Alicia Keys/The Element of Freedom/01 T.m4a"]
