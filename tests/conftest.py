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
    """Les tests ne doivent pas hériter des chemins de l'image Docker ou de l'hôte."""
    for var in ("MUSIC_RANGER_ROOT", "MUSIC_RANGER_INBOX", "MUSIC_RANGER_STATE",
                "LMS_PERSIST_DB", "LMS_MUSIC_ROOT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_stop():
    """L'arrêt demandé est global au processus : remis à zéro entre les tests."""
    import music_ranger
    music_ranger.STOP.clear()
    yield
    music_ranger.STOP.clear()


@pytest.fixture(scope="session")
def templates(tmp_path_factory):
    """Un fichier audio vide (0,1 s de silence) par format, créé une fois avec ffmpeg."""
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
    """Crée un fichier audio taggé. `path` peut être un str ou des bytes (nom non UTF-8)."""

    def make(path, title=None, artist=None, albumartist=None, album=None,
             track=None, disc=None, disctotal=None, compilation=False, extra=None,
             cover=None):
        """extra : tags FLAC supplémentaires ; cover : octets d'une pochette JPEG (FLAC)."""
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
