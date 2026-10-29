"""Read music metadata and embed lyrics, cover art or ReplayGain into a file's tags.

Works on the audio files via mutagen; Lyrion picks changes up on its next scan.
Lyrics are stored as plain text (LRC timestamps stripped), cover art as the bytes
handed in, never re-encoded.
"""

import hashlib
import os
import re

import mutagen
from mutagen.id3 import APIC, TXXX, USLT
from mutagen.flac import FLAC, Picture
from mutagen.easymp4 import EasyMP4Tags
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.mp3 import MP3
from mutagen.aiff import AIFF
from mutagen.wave import WAVE
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus

from music_librarian.core.files import AUDIO_EXT
from music_librarian.media.atoms import leading_mdat

# the library's formats, plus the other ones these functions can tag
MUSIC_EXTENSIONS = AUDIO_EXT | {".ogg", ".opus", ".mp4", ".aiff", ".aif", ".wav"}

# LRC timing tags: line timestamps [mm:ss.xx], enhanced word timestamps
# <mm:ss.xx>, and metadata lines like [ar:...] / [length:...].
_LINE_TS = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
_WORD_TS = re.compile(r"<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>")
_META_LINE = re.compile(r"^\[[a-zA-Z#]+:.*\]$")


class LyricsTagError(Exception):
    """A user-facing failure while reading or writing tags."""


class CoverTagError(Exception):
    """A user-facing failure while reading or writing cover art."""


class ReplayGainTagError(Exception):
    """A user-facing failure while reading or writing ReplayGain."""


class GenreTagError(Exception):
    """A user-facing failure while writing the genre."""


def lrc_to_plain(text):
    """Strip LRC line/word timestamps and metadata lines, leaving plain text."""
    lines = []
    for line in text.splitlines():
        if _META_LINE.match(line.strip()):
            continue
        line = _WORD_TS.sub("", _LINE_TS.sub("", line)).strip()
        lines.append(line)
    return "\n".join(lines).strip()


def is_music_file(path):
    """True if the path's extension is one we know how to tag."""
    return os.path.splitext(path)[1].lower() in MUSIC_EXTENSIONS


def read_genres(path):
    """The non-empty values of the file's genre tag, or [] if it has none or is unreadable."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return []
    if audio is None:
        return []
    return [v.strip() for v in audio.get("genre", []) if v.strip()]


def write_genre(path, genre):
    """Replace every genre of `path` with `genre`. Raises GenreTagError on failure."""
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            raise GenreTagError("unrecognised file format")
        if audio.tags is None:
            audio.add_tags()
        audio["genre"] = [genre]
        audio.save()
    except GenreTagError:
        raise
    except Exception as exc:
        raise GenreTagError(f"write failed: {exc}") from exc


def _read_lyrics(audio):
    """Return the existing lyrics tag of an opened mutagen file, or None."""
    if isinstance(audio, (MP3, AIFF, WAVE)):
        if audio.tags is None:
            return None
        frames = audio.tags.getall("USLT")
        return str(frames[0].text) if frames else None
    if isinstance(audio, MP4):
        values = audio.get("\xa9lyr")
        return values[0] if values else None
    if isinstance(audio, (FLAC, OggVorbis, OggOpus)):
        values = audio.get("LYRICS") or audio.get("UNSYNCEDLYRICS")
        return values[0] if values else None
    return None


def has_lyrics(path):
    """True if the file already carries a non-empty lyrics tag."""
    audio = mutagen.File(path)
    if audio is None:
        return False
    return bool(_read_lyrics(audio))


def write_lyrics(path, text):
    """Write plain-text `text` into the lyrics tag of `path`.

    Raises LyricsTagError on unrecognised/unsupported format or write failure.
    """
    if not text:
        raise LyricsTagError("empty lyrics")

    audio = mutagen.File(path)
    if audio is None:
        raise LyricsTagError("unrecognised file format")

    if isinstance(audio, (MP3, AIFF, WAVE)):
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("USLT")
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
    elif isinstance(audio, MP4):
        audio["\xa9lyr"] = text
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)):
        audio["LYRICS"] = text
    else:
        raise LyricsTagError("unsupported format for lyrics")

    try:
        audio.save()
    except Exception as exc:
        raise LyricsTagError(f"write failed: {exc}") from exc


def clear_lyrics(path):
    """Remove the lyrics tag from `path`, returning True if one was removed.

    A no-op (returns False) when the file carries no lyrics tag. Raises
    LyricsTagError on unrecognised/unsupported format or save failure.
    """
    audio = mutagen.File(path)
    if audio is None:
        raise LyricsTagError("unrecognised file format")

    if not _read_lyrics(audio):
        return False

    if isinstance(audio, (MP3, AIFF, WAVE)):
        audio.tags.delall("USLT")
    elif isinstance(audio, MP4):
        audio.pop("\xa9lyr", None)
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)):
        audio.pop("LYRICS", None)
        audio.pop("UNSYNCEDLYRICS", None)
    else:
        raise LyricsTagError("unsupported format for lyrics")

    try:
        audio.save()
    except Exception as exc:
        raise LyricsTagError(f"clear failed: {exc}") from exc
    return True


def read_cover(path):
    """Return the raw bytes of the file's embedded cover art, or None.

    The first picture found wins, which is what players show. Returns None for
    an unreadable file or a format that carries no artwork.
    """
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    return _cover(audio)


def _cover(audio):
    if audio is None or audio.tags is None:
        return None
    if isinstance(audio, (MP3, AIFF, WAVE)):
        frames = audio.tags.getall("APIC")
        return bytes(frames[0].data) if frames else None
    if isinstance(audio, MP4):
        covers = audio.tags.get("covr")
        return bytes(covers[0]) if covers else None
    if isinstance(audio, FLAC):
        return bytes(audio.pictures[0].data) if audio.pictures else None
    return None


def write_cover(path, data, mime="image/jpeg"):
    """Replace the cover art of `path` with `data`, the raw image bytes.

    Any existing artwork is dropped first, so a file ends up with exactly one
    picture. Raises CoverTagError on unrecognised/unsupported format or write
    failure. Embedding usually rewrites the whole file.
    """
    if not data:
        raise CoverTagError("empty cover")

    audio = mutagen.File(path)
    if audio is None:
        raise CoverTagError("unrecognised file format")
    if audio.tags is None:
        audio.add_tags()

    if isinstance(audio, (MP3, AIFF, WAVE)):
        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=0, mime=mime, type=3, desc="", data=data))
    elif isinstance(audio, MP4):
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
    elif isinstance(audio, FLAC):
        picture = Picture()
        picture.type = 3
        picture.mime = mime
        picture.data = data
        audio.clear_pictures()
        audio.add_picture(picture)
    else:
        raise CoverTagError("unsupported format for cover art")

    try:
        audio.save()
    except Exception as exc:
        raise CoverTagError(f"write failed: {exc}") from exc


# Same fields and value formats as the tags r128gain wrote across the library.
# Other taggers vary the case of the names, so they are matched without it.
def _rg_keys(audio):
    """(gain key, peak key) for the file's tag format, or None if unsupported."""
    if isinstance(audio, MP3):
        return "TXXX:REPLAYGAIN_TRACK_GAIN", "TXXX:REPLAYGAIN_TRACK_PEAK"
    if isinstance(audio, MP4):
        return "----:com.apple.iTunes:replaygain_track_gain", "----:com.apple.iTunes:replaygain_track_peak"
    if isinstance(audio, FLAC):
        return "REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"
    return None


def has_replaygain(path):
    """True if the file carries a track gain, whatever the case of its name."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return False
    return _has_replaygain(audio)


def _has_replaygain(audio):
    if audio is None or audio.tags is None or not _rg_keys(audio):
        return False
    gain = _rg_keys(audio)[0].lower()
    return any(k.lower() == gain for k in audio.tags.keys())


def write_replaygain(path, gain, peak):
    """Write the track gain (dB) and sample peak (0 to 1) into `path`.

    Fields of the same name in another case are replaced. Raises
    ReplayGainTagError on unreadable/unsupported format or write failure.
    """
    try:
        audio = mutagen.File(path)
        if audio is None or not _rg_keys(audio):
            raise ReplayGainTagError("unsupported format for ReplayGain")
        if audio.tags is None:
            audio.add_tags()
        gain_key, peak_key = _rg_keys(audio)
        for key in list(audio.tags.keys()):
            if key.lower() in (gain_key.lower(), peak_key.lower()):
                del audio.tags[key]
        gain_text = f"{gain:.2f} dB"
        if isinstance(audio, MP3):
            for key, text in ((gain_key, gain_text), (peak_key, f"{peak:.6f}")):
                audio.tags.add(TXXX(encoding=0, desc=key[len("TXXX:"):], text=text))
        elif isinstance(audio, MP4):
            audio.tags[gain_key] = MP4FreeForm(gain_text.encode())
            audio.tags[peak_key] = MP4FreeForm(f"{peak:.6f}".encode())
        else:
            audio[gain_key] = gain_text
            audio[peak_key] = f"{peak:.8f}"
        audio.save()
    except ReplayGainTagError:
        raise
    except Exception as exc:
        raise ReplayGainTagError(f"write failed: {exc}") from exc


# lyrics and embedded pictures are kept as a flag and a hash, never as text
def _mp4_compilation(tags, key):
    cpil = tags.get("cpil")
    if isinstance(cpil, list):
        cpil = cpil[0] if cpil else False
    if not cpil:
        raise KeyError(key)
    return ["1"]


# mutagen's easy MP4 view has no compilation flag, which organize files by
if "compilation" not in EasyMP4Tags.Get:
    EasyMP4Tags.RegisterKey("compilation", getter=_mp4_compilation)

_BULKY = {"lyrics", "unsyncedlyrics", "metadata_block_picture", "coverart"}


def read_snapshot(path):
    """Everything the tools check in one read, or None if the file is unreadable.

    {"tags": {easy key: [str]}, "duration": seconds or None, "lyrics": bool,
     "replaygain": bool, "cover": SHA-1 hex of the embedded cover or None,
     "leading_mdat": bool, true for an MP4 file whose moov atom comes after mdat}
    """
    try:
        audio = mutagen.File(path)
        easy = mutagen.File(path, easy=True)
    except Exception:
        return None
    if audio is None or easy is None:
        return None
    cover = _cover(audio)
    return {
        "tags": {k: [str(v) for v in easy[k]] for k in (easy.keys() if easy.tags else ())
                 if k.lower() not in _BULKY},
        "duration": getattr(getattr(audio, "info", None), "length", None),
        "lyrics": bool(_read_lyrics(audio)),
        "replaygain": _has_replaygain(audio),
        "cover": cover_hash(cover) if cover else None,
        "leading_mdat": isinstance(audio, MP4) and leading_mdat(path),
    }


def cover_hash(data):
    return hashlib.sha1(data).hexdigest()
