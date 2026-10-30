# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TALB, TCMP, TCOM, TIT2, TPE1, TPE2, TPOS, TRCK
from mutagen.mp4 import MP4

from music_librarian import watch
from music_librarian.core.lock import ONE_PASS, SIGNALLED, STOP

CODECS = {".m4a": ["-c:a", "alac", "-movflags", "+faststart"], ".mp3": ["-c:a", "libmp3lame"],
          ".flac": ["-c:a", "flac"]}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Tests must not inherit paths from the Docker image or the host, nor write to its state."""
    for var in ("MUSIC_LIBRARIAN_ROOT", "MUSIC_LIBRARIAN_INBOX", "MUSIC_LIBRARIAN_STATE", "GENRE_LOOKUP",
                "LYRION_PERSIST_DB", "LYRION_MUSIC_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture(autouse=True)
def no_stop():
    """The stop request is process-wide: reset between tests."""
    for event in (STOP, SIGNALLED, ONE_PASS):
        event.clear()
    yield
    for event in (STOP, SIGNALLED, ONE_PASS):
        event.clear()


@pytest.fixture
def ticks(monkeypatch):
    """Drive the watch loop: each wait runs the next action, then stops."""

    def run(inbox_dir, actions, *argv):
        todo = list(actions)

        def wait(timeout=None):
            if todo:
                todo.pop(0)()
            else:
                STOP.set()
            return STOP.is_set()

        monkeypatch.setattr(STOP, "wait", wait)
        return watch.main(["--inbox", str(inbox_dir), "--root", str(inbox_dir.parent / "Musique"),
                           "--interval", "0", *argv])
    return run


@pytest.fixture(scope="session")
def templates(tmp_path_factory):
    """One empty audio file (0.1 s of silence) per format, created once with ffmpeg."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    d = tmp_path_factory.mktemp("templates")
    out = {}
    for ext, codec in CODECS.items():
        p = d / f"silence{ext}"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=44100:cl=stereo", "-t", "0.1", *codec, str(p)],
                       check=True)
        out[ext] = p
    return out


@pytest.fixture
def make_track(templates):
    """Create a tagged audio file. `path` may be a str or bytes (non-UTF-8 name)."""

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def make(path, title=None, artist=None, albumartist=None, album=None,
             track=None, disc=None, disctotal=None, compilation=False, extra=None,
             cover=None, composer=None):
        """extra: additional FLAC tags; cover: bytes of a JPEG cover (FLAC)."""
        p = os.fsdecode(path)
        ext = os.path.splitext(p)[1].lower()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy(templates[ext], p)
        if ext == ".m4a":
            f = MP4(p)
            for k, v in (("\xa9nam", title), ("\xa9ART", artist), ("aART", albumartist),
                         ("\xa9alb", album), ("\xa9wrt", composer)):
                if v:
                    f[k] = [v]
            if track:
                f["trkn"] = [(track, 0)]
            if disc:
                f["disk"] = [(disc, disctotal or 0)]
            if compilation:
                f["cpil"] = True
            f.save()
        elif ext == ".mp3":
            f = ID3()
            for frame, v in ((TIT2, title), (TPE1, artist), (TPE2, albumartist), (TALB, album),
                             (TCOM, composer)):
                if v:
                    f.add(frame(encoding=3, text=v))
            if track:
                f.add(TRCK(encoding=3, text=str(track)))
            if disc:
                f.add(TPOS(encoding=3, text=f"{disc}/{disctotal}" if disctotal else str(disc)))
            if compilation:
                f.add(TCMP(encoding=3, text="1"))
            f.save(p)
        else:
            f = FLAC(p)
            for k, v in (("title", title), ("artist", artist), ("albumartist", albumartist),
                         ("album", album), ("composer", composer)):
                if v:
                    f[k] = v
            if track:
                f["tracknumber"] = str(track)
            if disc:
                f["discnumber"] = str(disc)
            if disctotal:
                f["disctotal"] = str(disctotal)
            if compilation:
                f["compilation"] = "1"
            for k, v in (extra or {}).items():
                f[k] = v
            if cover:
                pic = Picture()
                pic.type, pic.mime, pic.data = 3, "image/jpeg", cover
                f.add_picture(pic)
            f.save()
        return Path(p)

    return make


@pytest.fixture
def make_slow_m4a(make_track):
    """make_track for an .m4a with mdat ahead of moov, as ffmpeg writes it by default."""

    def make(path, **tags):
        good = make_track(Path(path).with_name("good.m4a"), **tags)
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(good), "-c", "copy", "-map_metadata", "-1",
                        str(path)], check=True)
        f = MP4(path)
        f.tags.clear()
        f.tags.update(MP4(good).tags)
        f.save()
        good.unlink()
        return Path(path)

    return make


@pytest.fixture
def answers(monkeypatch):
    """Feed the replies of a --review, then end the input as Ctrl-D does."""
    def feed(*replies):
        todo = list(replies)

        def ask(_prompt=""):
            if not todo:
                raise EOFError
            return todo.pop(0)
        monkeypatch.setattr("builtins.input", ask)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    return feed
