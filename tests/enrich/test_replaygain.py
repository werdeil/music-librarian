"""enrich.replaygain: loudness measured by ffmpeg, written as ReplayGain tags."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import os
import subprocess

import mutagen
import pytest
from mutagen.id3 import TXXX
from mutagen.mp4 import MP4FreeForm

from music_librarian.core.lock import EXIT_STOPPED, STOP
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
    loudness, peak = replaygain.measure(str(m4a))
    assert loudness == pytest.approx(-21.1, abs=0.1)
    assert peak == pytest.approx(0.088379, abs=1e-4)


def test_tagged_with_mtime_kept(album, capsys):
    d, (m4a, mp3, flac) = album
    assert replaygain.main([str(d)]) == 0
    assert all(tags.has_replaygain(str(t)) for t in (m4a, mp3, flac))
    for track, decimals in ((m4a, 6), (flac, 8)):
        peak, gain = fields(track)
        assert float(gain.removesuffix(" dB")) == pytest.approx(3.1, abs=0.1)
        assert float(peak) == pytest.approx(0.088379, abs=1e-4) and len(peak.split(".")[1]) == decimals
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
    assert replaygain.main([str(d)]) == 2
    out = capsys.readouterr().out
    assert "ERROR    " in out and "04 Cassé.mp3" in out
    assert "tagged: 3" in out and "failed: 1" in out


def test_other_case_counts_and_is_replaced(album):
    _, (m4a, mp3, _flac) = album
    f = mutagen.File(mp3)
    f.tags.add(TXXX(encoding=3, desc="replaygain_track_gain", text="-2.00 dB"))
    f.save()
    assert tags.has_replaygain(str(mp3))
    f = mutagen.File(m4a)
    f.tags["----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN"] = MP4FreeForm(b"-2.00 dB")
    f.save()
    tags.write_replaygain(str(m4a), -1, 0.5)
    assert fields(m4a) == ["-1.00 dB", "0.500000"]


def test_unsupported_formats_left_alone(album, capsys):
    d, _ = album
    (d / "04 Titre.ogg").write_bytes(b"OggS")
    assert replaygain.main([str(d), "--dry-run"]) == 0
    assert ".ogg" not in capsys.readouterr().out


def test_mutagen_error_reported(album, capsys, monkeypatch):
    d, _ = album

    def broken(path, gain, peak):
        raise mutagen.MutagenError("damaged header")

    monkeypatch.setattr(tags, "write_replaygain", broken)
    assert replaygain.main([str(d)]) == 2
    out = capsys.readouterr().out
    assert out.count("damaged header") == 3 and "failed: 3" in out


def test_stop_finishes_current_files(album, capsys, monkeypatch):
    d, _ = album
    real = replaygain.tag

    def tag_then_stop(path):
        result = real(path)
        STOP.set()
        return result

    monkeypatch.setattr(replaygain, "tag", tag_then_stop)
    assert replaygain.main([str(d), "--jobs", "1"]) == EXIT_STOPPED
    out = capsys.readouterr().out
    assert "tagged: 1" in out and "2 files left" in out
