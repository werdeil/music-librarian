"""Import from the Inbox, on real files created by ffmpeg."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
# pylint: disable=attribute-defined-outside-init  # fixture environment objects
import subprocess
import sys
from datetime import date

import pytest
from mutagen.mp4 import MP4

from music_librarian import enrich, importer
from music_librarian.media import convert

TODAY = date.today().isoformat()
JPEG = b"\xff\xd8\xff\xe0" + b"pochette" * 10 + b"\xff\xd9"


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox_dir, lib, state = tmp_path / "Inbox_Musique", tmp_path / "Musique", tmp_path / "state"
    inbox_dir.mkdir()
    lib.mkdir()
    calls = tmp_path / "enrich.log"
    script = f"import sys; open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')"
    monkeypatch.setattr(enrich, "enrichers",
                        lambda: [("fake", [sys.executable, "-c", script])])

    class Env:
        pass

    e = Env()
    e.inbox, e.lib, e.calls = inbox_dir, lib, calls
    e.run = lambda *a: importer.main(["--inbox", str(inbox_dir), "--root", str(lib),
                                          "--state-dir", str(state), "--settle", "0", *a])
    return e


def files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def flac_album(env, make_track, name="Album", **kw):
    d = env.inbox / name
    base = dict(artist="A", albumartist="A", album="B")
    base.update(kw)
    make_track(d / "x1.flac", title="Un", track=1, cover=JPEG,
               extra={"tracktotal": "2", "genre": "Rock", "lyrics": "la la",
                      "musicbrainz_trackid": "abc", "replaygain_track_gain": "-3 dB"}, **base)
    make_track(d / "x2.flac", title="Deux", track=2, cover=JPEG, **base)
    (d / "folder.jpg").write_bytes(JPEG)
    return d


def test_dry_run_changes_nothing(env, make_track, capsys):
    flac_album(env, make_track)
    before = files(env.inbox)
    assert env.run() == 0
    out = capsys.readouterr().out
    assert files(env.inbox) == before and files(env.lib) == []
    assert "CONVERT  x1.flac" in out
    assert "A/B/01 Un.m4a" in out
    assert not env.calls.exists()


def test_flac_album_imported(env, make_track):
    flac_album(env, make_track)
    assert env.run("--apply") == 0

    assert files(env.lib) == ["A/B/01 Un.m4a", "A/B/02 Deux.m4a", "A/B/folder.jpg"]
    t = MP4(env.lib / "A/B/01 Un.m4a").tags
    assert t["\xa9nam"] == ["Un"] and t["aART"] == ["A"] and t["trkn"] == [(1, 2)]
    assert t["\xa9gen"] == ["Rock"] and t["\xa9lyr"] == ["la la"]
    assert bytes(t["covr"][0]) == JPEG
    assert bytes(t["----:com.apple.iTunes:MUSICBRAINZ_TRACKID"][0]) == b"abc"
    assert not any("REPLAYGAIN" in k.upper() for k in t.keys())
    assert importer.probe(env.lib / "A/B/01 Un.m4a")[0] == "alac"

    # the original, FLAC included, is kept in _done/; the Inbox stays
    assert files(env.inbox) == [f"_done/{TODAY}/Album/folder.jpg",
                                f"_done/{TODAY}/Album/x1.flac",
                                f"_done/{TODAY}/Album/x2.flac"]
    assert env.calls.read_text().split() == [str(env.lib / "A/B")]


def test_cover_renamed_and_useless_annexes_left(env, make_track, capsys):
    # real case: Salut C'est Cool - Mon premier EP
    d = flac_album(env, make_track)
    (d / "folder.jpg").rename(d / "cover.jpg")
    (d / "info.txt").write_text("source")
    (d / "Booklet.pdf").write_bytes(b"pdf")
    (d / "1.lrc").write_text("[00:01]la")

    assert env.run() == 0                           # dry run: same result announced
    out = capsys.readouterr().out
    assert "COVER    cover.jpg → folder.jpg" in out and "KEPT     info.txt" in out
    assert "A/B/folder.jpg" in out and "A/B/info.txt" not in out

    assert env.run("--apply") == 0
    assert files(env.lib) == ["A/B/01 Un.m4a", "A/B/02 Deux.m4a", "A/B/1.lrc",
                              "A/B/Booklet.pdf", "A/B/folder.jpg"]
    assert (env.lib / "A/B/folder.jpg").read_bytes() == JPEG
    # the original stays whole in _done/, info.txt included
    assert f"_done/{TODAY}/Album/info.txt" in files(env.inbox)
    assert f"_done/{TODAY}/Album/cover.jpg" in files(env.inbox)


def test_existing_folder_jpg_wins_over_cover(env, make_track):
    d = flac_album(env, make_track)
    (d / "cover.jpg").write_bytes(b"autre")
    assert env.run("--apply") == 0
    assert (env.lib / "A/B/folder.jpg").read_bytes() == JPEG
    assert (env.lib / "A/B/cover.jpg").read_bytes() == b"autre"


def test_24bit_flac_stays_24bit(env, tmp_path):
    src = env.inbox / "hd.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:r=96000",
                    "-t", "0.2", "-sample_fmt", "s32", "-c:a", "flac",
                    "-metadata", "title=HD", "-metadata", "artist=A", str(src)], check=True)
    assert env.run("--apply") == 0
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=sample_rate,bits_per_raw_sample", "-of", "csv=p=0",
                          str(env.lib / "A/Unknown Album/HD.m4a")],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "96000,24"


def test_alac_and_loose_mp3_moved(env, make_track):
    make_track(env.inbox / "Album/1.m4a", title="Un", artist="A", album="B", track=1)
    make_track(env.inbox / "chanson.mp3", title="Seule", artist="C", album="D", track=3)
    (env.inbox / ".DS_Store").write_bytes(b"x")
    assert env.run("--apply") == 0
    assert files(env.lib) == ["A/B/01 Un.m4a", "C/D/03 Seule.mp3"]
    assert files(env.inbox) == [f"_done/{TODAY}/Album/1.m4a", f"_done/{TODAY}/chanson.mp3"]


@pytest.mark.parametrize("name, maker", [
    ("aac", lambda env, mk: subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc", "-t", "0.1", "-c:a", "aac",
         str(env.inbox / "aac/1.m4a")], check=True)),
    ("wav", lambda env, mk: (env.inbox / "wav/1.wav").write_bytes(b"RIFF")),
    ("sans titre", lambda env, mk: mk(env.inbox / "sans titre/1.flac", artist="A", album="B")),
    ("cpil", lambda env, mk: (
        mk(env.inbox / "cpil/1.flac", title="Un", artist="A", album="B", compilation=True),
        mk(env.inbox / "cpil/2.flac", title="Deux", artist="C", album="B"))),
])
def test_rejected_whole_to_errors(env, make_track, name, maker):
    (env.inbox / name).mkdir()
    maker(env, make_track)
    make_track(env.inbox / name / "bon.flac", title="Bon", artist="Z", album="Y", track=9)
    before = sorted(p.name for p in (env.inbox / name).iterdir())
    assert env.run("--apply") == 2
    assert files(env.lib) == []
    assert sorted(p.name for p in (env.inbox / "_failed" / name).iterdir()) == before
    assert not (env.inbox / "_processing").exists()


def test_no_audio_rejected(env):
    (env.inbox / "notes").mkdir()
    (env.inbox / "notes/lisez-moi.txt").write_text("x")
    (env.inbox / "cover.jpg").write_bytes(JPEG)
    assert env.run("--apply") == 2
    assert files(env.inbox) == ["_failed/cover.jpg", "_failed/notes/lisez-moi.txt"]


def test_already_in_library_rejected(env, make_track):
    make_track(env.lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    make_track(env.inbox / "Album/1.flac", title="Un", artist="A", album="B", track=1)
    assert env.run("--apply") == 2
    assert files(env.lib) == ["A/B/01 Un.m4a"]
    assert (env.inbox / "_failed/Album/1.flac").exists()


def test_duration_mismatch_rejected(env, make_track, monkeypatch):
    make_track(env.inbox / "Album/1.flac", title="Un", artist="A", album="B", track=1)
    real = convert.probe
    monkeypatch.setattr(convert, "probe", lambda p: (
        real(p)[0], 1.0 if p.suffix == ".flac" else 2.0))
    assert env.run("--apply") == 2
    assert files(env.lib) == []
    assert (env.inbox / "_failed/Album/1.flac").exists()


def test_errors_name_collision(env, make_track):
    (env.inbox / "_failed/Album").mkdir(parents=True)
    (env.inbox / "Album").mkdir()
    (env.inbox / "Album/1.wav").write_bytes(b"x")
    assert env.run("--apply") == 2
    assert (env.inbox / "_failed/Album (2)/1.wav").exists()


def test_busy_item_waits(env, make_track, monkeypatch):
    p = make_track(env.inbox / "Album/1.flac", title="Un", artist="A", album="B", track=1)
    # the copy goes on during the wait
    monkeypatch.setattr(importer.time, "sleep", lambda s: p.open("ab").write(b"x"))
    assert env.run("--settle", "1", "--apply") == 0
    assert p.exists() and files(env.lib) == []


def test_purge_old_done(env, capsys):
    old = env.inbox / "_done/2020-01-01/Album"
    old.mkdir(parents=True)
    (env.inbox / f"_done/{TODAY}").mkdir()
    assert env.run() == 0
    assert old.exists()
    assert env.run("--apply") == 0
    assert not (env.inbox / "_done/2020-01-01").exists()
    assert (env.inbox / f"_done/{TODAY}").exists()


def test_enrich_failure_reported(env, make_track, monkeypatch):
    monkeypatch.setattr(enrich, "enrichers",
                        lambda: [("broken", [sys.executable, "-c", "raise SystemExit(1)"])])
    make_track(env.inbox / "Album/1.flac", title="Un", artist="A", album="B", track=1)
    assert env.run("--apply") == 2
    assert files(env.lib) == ["A/B/01 Un.m4a"]


def test_enrich_stopped_not_a_failure(env, make_track, monkeypatch, capsys):
    monkeypatch.setattr(enrich, "enrichers",
                        lambda: [("stopped", [sys.executable, "-c", "raise SystemExit(4)"])])
    make_track(env.inbox / "Album/1.flac", title="Un", artist="A", album="B", track=1)
    assert env.run("--apply") == 0
    out = capsys.readouterr().out
    assert "STOP     enrichment stopped" in out and "FAILED" not in out


def test_empty_inbox_kept(env):
    assert env.run("--apply") == 0
    assert env.inbox.is_dir()
    assert sorted(p.name for p in env.inbox.iterdir()) == ["_done", "_failed"]


def test_refused_by_organize_shows_why(env, make_track, capsys):
    make_track(env.lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    make_track(env.inbox / "Album/1.m4a", title="Un", artist="A", album="B", track=1)
    assert env.run() == 2
    out = capsys.readouterr().out
    assert "CONFLICT" in out and "would go to _failed/" in out
    assert env.run("--apply") == 2
    out = capsys.readouterr().out
    assert "CONFLICT" in out and "refused by organize" in out
    assert files(env.inbox) == ["_failed/Album/1.m4a"]
    assert files(env.lib) == ["A/B/01 Un.m4a"]
