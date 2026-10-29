"""media.metadata_cache: snapshots kept between passes, re-read when a file changes."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import os

import pytest

from music_librarian.media import credit_tags, metadata_cache, tags
from music_librarian.media.metadata_cache import MetadataCache

COVER = b"\xff\xd8\xff\xe0 not really a jpeg"


@pytest.fixture
def reads(monkeypatch):
    """The paths read from disk, in order."""
    calls = []
    real = tags.read_snapshot

    def counting(path):
        calls.append(os.path.basename(os.fsdecode(path)))
        return real(path)

    monkeypatch.setattr(tags, "read_snapshot", counting)
    return calls


@pytest.mark.parametrize("ext", [".m4a", ".mp3", ".flac"])
def test_snapshot(tmp_path, make_track, ext):
    p = str(make_track(tmp_path / f"01 Titre{ext}", title="Titre", artist="Artiste", album="Album"))
    tags.write_genre(p, "Rock")
    tags.write_lyrics(p, "la la la")
    tags.write_replaygain(p, -3.5, 0.5)
    tags.write_cover(p, COVER)
    snap = tags.read_snapshot(p)
    assert metadata_cache.genres(snap) == ["Rock"]
    assert metadata_cache.metadata(snap)["artist"] == "Artiste"
    assert snap["duration"] == pytest.approx(0.1, abs=0.05)
    assert snap["lyrics"] and snap["replaygain"] and snap["cover"] == tags.cover_hash(COVER)
    assert "lyrics" not in snap["tags"]


def test_bare_and_unreadable(tmp_path, make_track):
    bare = tags.read_snapshot(str(make_track(tmp_path / "01.flac")))
    assert "genre" not in bare["tags"] and bare["cover"] is None
    assert not bare["lyrics"] and not bare["replaygain"]
    junk = tmp_path / "02.m4a"
    junk.write_bytes(b"not audio")
    assert tags.read_snapshot(str(junk)) is None
    assert metadata_cache.genres(None) == [] and metadata_cache.metadata(None) is None


def test_read_once_until_changed(tmp_path, make_track, reads):
    p = make_track(tmp_path / "Album" / "01.m4a", title="Titre")
    for _ in range(2):
        with MetadataCache(tmp_path / "state") as cache:
            assert metadata_cache.metadata(cache.snapshot(p))["title"] == "Titre"
    assert reads == ["01.m4a"]
    tags.write_genre(str(p), "Rock")
    with MetadataCache(tmp_path / "state") as cache:
        assert metadata_cache.genres(cache.snapshot(p)) == ["Rock"]
    assert reads == ["01.m4a", "01.m4a"]


def test_restored_mtime_still_re_read(tmp_path, make_track, reads):
    p = make_track(tmp_path / "01.flac", title="Titre")
    st = os.stat(p)
    with MetadataCache(tmp_path / "state") as cache:
        cache.snapshot(p)
        tags.write_genre(str(p), "Rock")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert os.stat(p).st_size == st.st_size
        assert metadata_cache.genres(cache.snapshot(p)) == ["Rock"]


def test_version_bump_re_reads(tmp_path, make_track, reads, monkeypatch):
    p = make_track(tmp_path / "01.m4a")
    with MetadataCache(tmp_path / "state") as cache:
        cache.snapshot(p)
    monkeypatch.setattr(metadata_cache, "VERSION", metadata_cache.VERSION + 1)
    with MetadataCache(tmp_path / "state") as cache:
        cache.snapshot(p)
    assert len(reads) == 2


def test_non_utf8_name(tmp_path, make_track, reads):
    p = make_track(os.fsencode(tmp_path) + b"/Caf\xe9.m4a", title="Titre")
    for _ in range(2):
        with MetadataCache(tmp_path / "state") as cache:
            assert metadata_cache.metadata(cache.snapshot(p))["title"] == "Titre"
    assert len(reads) == 1


def test_prune(tmp_path, make_track, reads):
    root = tmp_path / "Musique"
    kept, gone = make_track(root / "A" / "01.m4a"), make_track(root / "B" / "01.m4a")
    outside = make_track(tmp_path / "Other" / "01.m4a")
    with MetadataCache(tmp_path / "state") as cache:
        for p in (kept, gone, outside):
            cache.snapshot(p)
        assert cache.prune(root, [kept]) == 1
    with MetadataCache(tmp_path / "state") as cache:
        for p in (kept, gone, outside):
            cache.snapshot(p)
    assert len(reads) == 4


def test_credits_of_a_snapshot(make_track, tmp_path):
    for ext in (".m4a", ".mp3", ".flac"):
        p = make_track(tmp_path / f"t{ext}", title="T", artist="A", albumartist="AA", composer="C")
        assert credit_tags.from_snapshot(tags.read_snapshot(p)) == credit_tags.read(p)
