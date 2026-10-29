"""Putting moov ahead of mdat in existing MP4 files, on real files created by ffmpeg."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from music_librarian.media import atoms
from music_librarian.media import faststart as fs

JPEG = b"\xff\xd8\xff\xe0" + b"pochette" * 10 + b"\xff\xd9"


@pytest.fixture
def slow(tmp_path, make_slow_m4a):
    p = make_slow_m4a(tmp_path / "slow.m4a", title="Un", artist="A", album="B", track=1)
    f = MP4(p)
    f["covr"] = [MP4Cover(JPEG, imageformat=MP4Cover.FORMAT_JPEG)]
    f["----:com.apple.iTunes:MUSICBRAINZ_TRACKID"] = [MP4FreeForm(b"abc")]
    f.save()
    p.chmod(0o640)
    return p


def test_faststart_keeps_audio_tags_and_mode(slow):
    tags, md5 = dict(MP4(slow).tags), fs.packets_md5(slow)
    fs.faststart(slow)
    assert not atoms.leading_mdat(slow)
    assert dict(MP4(slow).tags) == tags and fs.packets_md5(slow) == md5
    assert slow.stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in slow.parent.iterdir()) == ["slow.m4a"]


def test_failed_check_leaves_the_file(slow, monkeypatch):
    before = slow.read_bytes()
    monkeypatch.setattr(fs, "packets_md5", str)
    with pytest.raises(fs.RewriteError, match="audio differs"):
        fs.faststart(slow)
    assert slow.read_bytes() == before
    assert sorted(p.name for p in slow.parent.iterdir()) == ["slow.m4a"]
