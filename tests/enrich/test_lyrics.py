"""enrich.lyrics: lyrics written into the tags, on real files, with mocked providers."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import pytest

from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.core.state import state_path
from music_librarian.enrich import common, lyrics
from music_librarian.lyrics import awaited
from music_librarian.media import metadata_cache, tags

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
    assert calls == [] and "to look up: 3" in out and "WOULD" not in out
    assert "estimated time: under a minute (3 s)" in out
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


def test_miss_looked_up_on_every_pass(album, web, capsys):
    d, tracks = album
    calls, answer = web
    answer.update(synced=None, source="none")
    awaited.mark(state_path(), [metadata_cache.metadata(tags.read_snapshot(str(tracks[0])))])
    assert run(d) == 0
    assert run(d, "--dry-run") == 0
    assert len(calls) == 3 and "to look up: 3" in capsys.readouterr().out
    assert run(d) == 0
    assert len(calls) == 6


def test_lyrics_found_no_longer_awaited(album, web):
    _, tracks = album
    meta = metadata_cache.metadata(tags.read_snapshot(str(tracks[0])))
    awaited.mark(state_path(), [meta])
    assert run(tracks[0], "--dry-run", "--look-up") == 0
    assert awaited.Awaited(state_path()).holds(meta)
    assert run(tracks[0]) == 0
    assert not awaited.Awaited(state_path()).holds(meta)


def test_no_provider_enabled_looks_nothing_up(album, web, monkeypatch):
    monkeypatch.setenv("LYRICS_PROVIDERS", "none")
    d, _ = album
    assert run(d) == 0
    calls, _ = web
    assert calls == []


def test_instrumental_answer_writes_the_mark(album, web, capsys):
    d, tracks = album
    calls, answer = web
    answer.update(synced=None, instrumental=True)
    assert run(d, "--dry-run", "--look-up") == 0
    assert "WOULD    write" in capsys.readouterr().out and not tags.has_lyrics(str(tracks[0]))
    assert run(d) == 0
    out = capsys.readouterr().out
    assert "(instrumental)" in out and "instrumental: 3" in out
    assert all(tags.read_lyrics(str(t)) == "[Instrumental]" for t in tracks)
    assert run(d) == 0 and len(calls) == 6


def test_the_mark_is_lyrics_to_clear_and_force(album, web):
    _, tracks = album
    calls, answer = web
    tags.write_lyrics(str(tracks[0]), "[Instrumental]")
    answer.update(synced=None, instrumental=True)
    assert run(tracks[0], "--clear") == 0 and len(calls) == 1
    assert tags.read_lyrics(str(tracks[0])) == "[Instrumental]"
    answer.update(synced=SYNCED, instrumental=False)
    assert run(tracks[0], "--force") == 0
    assert tags.read_lyrics(str(tracks[0])) == "la la\nlo lo"
    tags.write_lyrics(str(tracks[0]), "[Instrumental]")
    answer.update(synced=None, source="none")
    assert run(tracks[0], "--clear") == 0
    assert not tags.has_lyrics(str(tracks[0]))


def test_existing_lyrics_replaced_by_the_mark_under_clear_only(album, web):
    _, tracks = album
    tags.write_lyrics(str(tracks[0]), "déjà là")
    _, answer = web
    answer.update(synced=None, instrumental=True)
    run(tracks[0], "--force")
    assert tags.read_lyrics(str(tracks[0])) == "déjà là"
    run(tracks[0], "--clear")
    assert tags.read_lyrics(str(tracks[0])) == "[Instrumental]"
