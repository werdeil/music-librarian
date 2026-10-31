"""enrich.lyrics: lyrics written into the tags, on real files, with mocked providers."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import mutagen
import pytest

from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.enrich import common, lyrics
from music_librarian.media import tags

SYNCED = "[00:01.00] la la\n[00:02.00] lo lo"


@pytest.fixture
def album(tmp_path, make_track):
    d = tmp_path / "Musique" / "Nirvana" / "MTV Unplugged [Live]"
    tracks = [make_track(d / f"0{i} Titre{ext}", title="Titre", artist="Nirvana")
              for i, ext in enumerate((".m4a", ".mp3", ".flac"), 1)]
    make_track(d / "04 Sans titre.m4a", artist="Nirvana")
    return d, tracks


@pytest.fixture
def web(monkeypatch):
    calls = []
    answer = {"lyrics": None, "synced": SYNCED, "source": "lrclib"}

    def fetch(**kw):
        calls.append(kw)
        return dict(answer)

    monkeypatch.setattr(lyrics, "fetch_lyrics", fetch)
    return calls, answer


def run(*argv):
    return lyrics.main([*map(str, argv), "--delay", "0"])


def test_synced_written_as_plain(album, web):
    d, tracks = album
    assert run(d) == 0
    calls, _ = web
    assert len(calls) == 3                              # the file without a title is not looked up
    assert all(c["verify"] for c in calls)
    assert all(tags.has_lyrics(str(t)) for t in tracks)


def test_dry_run_lists_without_looking_up(album, web, capsys):
    d, tracks = album
    assert run(d, "--dry-run") == 0
    calls, _ = web
    out = capsys.readouterr().out
    assert calls == [] and "to look up: 3" in out and "WOULD    look up" in out
    assert not any(tags.has_lyrics(str(t)) for t in tracks)


def test_dry_run_looks_up_when_asked(album, web, capsys):
    d, tracks = album
    assert run(d, "--dry-run", "--look-up") == 0
    calls, _ = web
    assert len(calls) == 3 and "WOULD    write" in capsys.readouterr().out
    assert not any(tags.has_lyrics(str(t)) for t in tracks)


def test_already_tagged_not_searched(album, web):
    d, tracks = album
    tags.write_lyrics(str(tracks[0]), "déjà là")
    assert run(d) == 0
    calls, _ = web
    assert len(calls) == 2


@pytest.mark.parametrize("source", ["none", "rejected", "unavailable"])
def test_nothing_usable_writes_nothing(album, web, source):
    d, tracks = album
    _, answer = web
    answer.update(synced=None, source=source)
    assert run(d) == 0
    assert not any(tags.has_lyrics(str(t)) for t in tracks)


def test_clear_keeps_tag_when_unreachable(album, web):
    d, tracks = album
    tags.write_lyrics(str(tracks[0]), "déjà là")
    _, answer = web
    answer.update(synced=None, source="unavailable")
    run(d, "--clear")
    assert tags.has_lyrics(str(tracks[0]))
    answer.update(source="none")
    run(d, "--clear")
    assert not tags.has_lyrics(str(tracks[0]))


def test_targets(tmp_path):
    album = tmp_path / "MTV Unplugged [Live]"
    album.mkdir()
    assert common.expand([str(album)]) == ([str(album)], [])
    assert common.expand([str(tmp_path / "MTV*")]) == ([str(album)], [])
    missing = str(tmp_path / "Nope*")
    assert common.expand([missing]) == ([], [missing])


def test_write_failure_is_exit_2(album, web, monkeypatch, capsys):
    d, _ = album

    def broken(path, text):
        raise tags.LyricsTagError("read-only file")

    monkeypatch.setattr(tags, "write_lyrics", broken)
    assert run(d) == 2
    out = capsys.readouterr().out
    assert out.count("FAILED   ") == 3 and "failed: 3" in out


def test_stop_after_current_file(album, web, monkeypatch):
    d, tracks = album
    calls, answer = web

    def fetch_then_stop(**kw):
        calls.append(kw)
        STOP.set()
        return dict(answer)

    monkeypatch.setattr(lyrics, "fetch_lyrics", fetch_then_stop)
    assert run(d) == EXIT_STOPPED
    assert len(calls) == 1
    assert [tags.has_lyrics(str(t)) for t in tracks] == [True, False, False]


def test_single_file_and_hidden_folder(album, web, make_track):
    d, tracks = album
    make_track(d / ".cache/01 Titre.m4a", title="Titre", artist="Nirvana")
    assert [p for _, files in common.album_folders([str(d)]) for p in files] == \
        sorted(str(p) for p in d.iterdir() if p.is_file())
    assert run(tracks[1]) == 0
    calls, _ = web
    assert len(calls) == 1 and tags.has_lyrics(str(tracks[1]))


def test_miss_not_looked_up_again(album, web, capsys):
    d, _ = album
    calls, answer = web
    answer.update(synced=None, source="none")
    assert run(d) == 0
    assert run(d, "--dry-run") == 0
    assert len(calls) == 3 and "searched recently: 3" in capsys.readouterr().out
    assert run(d, "--force") == 0
    assert len(calls) == 6


def test_miss_retried_once_tags_change(album, web):
    d, tracks = album
    calls, answer = web
    answer.update(synced=None, source="rejected")
    run(d)
    tags.write_genre(str(tracks[0]), "Rock")
    run(d)
    assert len(calls) == 3
    t = mutagen.File(str(tracks[0]), easy=True)
    t["title"] = "Autre titre"
    t.save()
    run(d)
    assert len(calls) == 4


def test_unreachable_not_recorded(album, web, capsys):
    d, _ = album
    calls, answer = web
    answer.update(synced=None, source="unavailable")
    run(d)
    run(d)
    assert len(calls) == 6
    assert "searched recently:" not in capsys.readouterr().out


def test_retry_looks_up_a_recent_miss_without_overwriting(album, web):
    d, _ = album
    calls, answer = web
    answer.update(synced=None, source="none")
    run(d)
    assert run(d, "--retry") == 0
    assert len(calls) == 6


def test_no_provider_enabled_records_no_miss(album, web, monkeypatch, tmp_path):
    monkeypatch.setenv("LYRICS_PROVIDERS", "none")
    monkeypatch.setenv("MUSIC_LIBRARIAN_STATE", str(tmp_path / "state"))
    d, _ = album
    assert run(d) == 0
    calls, _ = web
    assert calls == [] and not (tmp_path / "state" / "lyrics-misses.db").exists()
