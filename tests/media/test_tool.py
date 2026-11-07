import subprocess

import pytest

from music_librarian.media import convert, tool


def test_a_tool_past_its_time_limit_is_a_failed_run():
    done = tool.run(["sleep", "5"], 0.1)
    assert done.returncode == tool.TIMED_OUT
    assert done.stderr == "sleep timed out after 0.1 s"


def test_a_checked_tool_past_its_time_limit_raises_as_a_failed_one():
    with pytest.raises(subprocess.CalledProcessError, match="sleep timed out after 0.1 s"):
        tool.run(["sleep", "5"], 0.1, check=True)


def test_a_conversion_past_its_time_limit_is_refused(make_track, tmp_path, monkeypatch):
    src = make_track(tmp_path / "a.flac", title="A")
    monkeypatch.setattr(tool, "FILE_SECONDS", 1e-6)
    with pytest.raises(convert.ConversionError, match="ffmpeg timed out"):
        convert.convert(src, tmp_path / "out" / "a.m4a")


def test_a_probe_past_its_time_limit_raises_as_a_failed_one(make_track, tmp_path, monkeypatch):
    src = make_track(tmp_path / "a.flac", title="A")
    monkeypatch.setattr(tool, "PROBE_SECONDS", 1e-6)
    with pytest.raises(subprocess.CalledProcessError, match="ffprobe timed out"):
        convert.probe(src)
