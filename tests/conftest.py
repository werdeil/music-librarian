# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TALB, TCMP, TIT2, TPE1, TPE2, TPOS, TRCK
from mutagen.mp4 import MP4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CODECS = {".m4a": ["-c:a", "alac"], ".mp3": ["-c:a", "libmp3lame"], ".flac": ["-c:a", "flac"]}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not inherit paths from the Docker image or the host."""
    for var in ("MUSIC_LIBRARIAN_ROOT", "MUSIC_LIBRARIAN_INBOX", "MUSIC_LIBRARIAN_STATE",
                "LYRION_PERSIST_DB", "LYRION_MUSIC_ROOT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_stop():
    """The stop request is process-wide: reset between tests."""
    import librarian  # pylint: disable=import-outside-toplevel  # after sys.path
    librarian.STOP.clear()
    yield
    librarian.STOP.clear()


@pytest.fixture
def ticks(monkeypatch):
    """Drive the watch loop: each wait runs the next action, then stops."""
    # pylint: disable-next=import-outside-toplevel  # after sys.path
    import watch
    from librarian import STOP  # pylint: disable=import-outside-toplevel

    def run(inbox_dir, actions, *argv):
        todo = list(actions)

        def wait(timeout=None):
            if todo:
                todo.pop(0)()
            else:
                STOP.set()
            return STOP.is_set()

        monkeypatch.setattr(STOP, "wait", wait)
        return watch.main(["--inbox", str(inbox_dir), "--interval", "0", *argv])
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
             cover=None):
        """extra: additional FLAC tags; cover: bytes of a JPEG cover (FLAC)."""
        p = os.fsdecode(path)
        ext = os.path.splitext(p)[1].lower()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy(templates[ext], p)
        if ext == ".m4a":
            f = MP4(p)
            for k, v in (("\xa9nam", title), ("\xa9ART", artist), ("aART", albumartist),
                         ("\xa9alb", album)):
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
            for frame, v in ((TIT2, title), (TPE1, artist), (TPE2, albumartist), (TALB, album)):
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
                         ("album", album)):
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
