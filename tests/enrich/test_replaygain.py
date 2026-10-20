"""enrich.replaygain: loudness measured by ffmpeg, written as ReplayGain tags."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import os
import subprocess

import mutagen
import pytest

from music_librarian.enrich import replaygain
from music_librarian.media import tags

CODECS = {".m4a": "alac", ".mp3": "libmp3lame", ".flac": "flac"}


@pytest.fixture
def album(tmp_path, templates):
    """A 1 kHz tone in each format: -21.1 LUFS, sample peak 0.088379 once lossless."""
    d = tmp_path / "Musique" / "Artiste" / "Album"
    d.mkdir(parents=True)
    tracks = []
    for i, (ext, codec) in enumerate(CODECS.items(), 1):
        p = d / f"0{i} Titre{ext}"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
                        "-c:a", codec, str(p)], check=True)
        os.utime(p, (1_000_000_000, 1_000_000_000))
        tracks.append(p)
    return d, tracks


def fields(path):
    audio = mutagen.File(path)
    values = [v[0] if isinstance(v, list) else v for k, v in audio.tags.items() if "replaygain" in k.lower()]
    return sorted(bytes(v).decode() if isinstance(v, bytes) else str(v) for v in values)


def test_measure(album):
    _, (m4a, _mp3, _flac) = album
    assert replaygain.measure(str(m4a)) == (-21.1, 0.088379)


def test_tagged_with_mtime_kept(album, capsys):
    d, (m4a, mp3, flac) = album
    assert replaygain.main([str(d)]) == 0
    assert all(tags.has_replaygain(str(t)) for t in (m4a, mp3, flac))
    assert fields(m4a) == ["0.088379", "3.10 dB"]
    assert fields(flac) == ["0.08837900", "3.10 dB"]
    assert fields(mp3)[1].endswith(" dB")
    assert os.stat(m4a).st_mtime == 1_000_000_001
    assert "tagged: 3" in capsys.readouterr().out


def test_tagged_files_skipped(album, capsys, monkeypatch):
    d, (m4a, *_) = album
    tags.write_replaygain(str(m4a), -1, 0.5)
    measured = []
    real = replaygain.measure
    monkeypatch.setattr(replaygain, "measure", lambda p: measured.append(p) or real(p))
    assert replaygain.main([str(d), "--jobs", "1"]) == 0
    assert str(m4a) not in measured and len(measured) == 2
    assert fields(m4a) == ["-1.00 dB", "0.500000"]


def test_dry_run_writes_nothing(album, capsys):
    d, tracks = album
    assert replaygain.main([str(d), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("WOULD    ") == 3 and "without gain: 3" in out
    assert not any(tags.has_replaygain(str(t)) for t in tracks)


def test_unreadable_file_reported(album, capsys):
    d, _ = album
    (d / "04 Cassé.mp3").write_bytes(b"not audio")
    assert replaygain.main([str(d)]) == 1
    out = capsys.readouterr().out
    assert "ERROR    " in out and "04 Cassé.mp3" in out
    assert "tagged: 3" in out and "failed: 1" in out
