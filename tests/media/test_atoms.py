"""Where moov sits among the top-level atoms of an MP4 file."""
from music_librarian.media.atoms import leading_mdat


def test_leading_mdat(make_slow_m4a, make_track, tmp_path):
    assert leading_mdat(make_slow_m4a(tmp_path / "slow.m4a", title="T"))
    assert not leading_mdat(make_track(tmp_path / "x.m4a", title="T"))
    (tmp_path / "short").write_bytes(b"\x00\x00")
    assert not leading_mdat(tmp_path / "short")
