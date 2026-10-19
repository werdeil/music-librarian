"""embed_lyrics: lyrics written into the tags, on real files, with mocked providers."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import pytest

import embed_lyrics
import tags

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

    monkeypatch.setattr(embed_lyrics, "fetch_lyrics", fetch)
    return calls, answer


def run(*argv):
    return embed_lyrics.main([*map(str, argv), "--delay", "0"])


def test_synced_written_as_plain(album, web):
    d, tracks = album
    assert run(d) == 0
    calls, _ = web
    assert len(calls) == 3                              # the file without a title is not looked up
    assert all(c["verify"] for c in calls)
    assert all(tags.has_lyrics(str(t)) for t in tracks)


def test_dry_run_writes_nothing(album, web):
    d, tracks = album
    assert run(d, "--dry-run") == 0
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
    assert embed_lyrics.resolve_targets([str(album)]) == ([str(album)], [])
    assert embed_lyrics.resolve_targets([str(tmp_path / "MTV*")]) == ([str(album)], [])
    missing = str(tmp_path / "Nope*")
    assert embed_lyrics.resolve_targets([missing]) == ([], [missing])
