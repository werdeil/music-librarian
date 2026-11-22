"""The tags and cover a downloaded track is given, the ones deezer-music-download wrote."""
import re

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TCOM, TCON, TCOP, TDRC, TIT2, TPE1, TPE2, TPOS, \
    TRCK, TSRC

from music_librarian.deezer.api import disc_total
from music_librarian.media import tags as media_tags

COVER_URL = "https://cdn-images.dzcdn.net/images/cover/{}/1000x1000-000000-80-0-0.jpg"
ID3_FRAMES = {"title": TIT2, "album": TALB, "artist": TPE1, "albumartist": TPE2, "composer": TCOM,
              "genre": TCON, "copyright": TCOP, "date": TDRC, "isrc": TSRC}


class TagWriteError(Exception):
    """The tags or the cover could not be written; the track itself is still usable."""


def title(track):
    return f"{track.title} {track.version}" if track.version else track.title


def artist(track):
    """Every artist of the track, sorted, joined by ", " (split later separates the known names)."""
    return ", ".join(sorted(track.artists))


def year(album, track):
    """The year of the album's release date, else of the track's physical release; "" without one.
    Deezer gives 0000 for a date it does not know."""
    for date in (album.release_date, track.physical_release_date):
        found = re.match(r"\s*(\d{4})", date or "")
        if found and found.group(1) != "0000":
            return found.group(1)
    return ""


def cover_url(album, track):
    """The album's cover: the API's largest, else one built from the page's picture hash; "" without."""
    if album.cover_xl:
        return album.cover_xl
    return COVER_URL.format(track.album_picture) if track.album_picture else ""


def fields(track, album, tracks=()):
    """{field: value} of the tags to write, the empty ones left out; `tracks` (the album's, when
    known) give the number of discs when the API does not."""
    values = {
        "title": title(track), "album": track.album or album.title, "artist": artist(track),
        "albumartist": album.artist, "composer": ", ".join(track.composers),
        "tracknumber": track.track_number, "tracktotal": str(album.nb_tracks or ""),
        "discnumber": track.disc_number, "disctotal": str(disc_total(album, tracks) or ""),
        "date": year(album, track), "isrc": track.isrc, "copyright": track.copyright,
        "genre": ", ".join(album.genres),
    }
    return {k: v for k, v in values.items() if v}


def write(path, values):
    """Write `values` (fields()) into the FLAC or MP3 file at `path`. Raises TagWriteError."""
    try:
        if str(path).lower().endswith(".flac"):
            audio = FLAC(path)
            for name, value in values.items():
                audio[name.upper()] = value
            audio.save()
        else:
            _write_id3(path, values)
    except Exception as exc:
        raise TagWriteError(str(exc)) from exc


def _write_id3(path, values):
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for name, frame in ID3_FRAMES.items():
        if values.get(name):
            tags.setall(frame.__name__, [frame(encoding=3, text=values[name])])
    for name, total, frame in (("tracknumber", "tracktotal", TRCK), ("discnumber", "disctotal", TPOS)):
        number = "/".join(v for v in (values.get(name), values.get(total)) if v)
        if number:
            tags.setall(frame.__name__, [frame(encoding=3, text=number)])
    tags.save(path)


def embed_cover(path, image):
    """Embed `image` (JPEG bytes) as the front cover of `path`. Raises TagWriteError."""
    try:
        media_tags.write_cover(path, image, "image/jpeg")
    except Exception as exc:
        raise TagWriteError(str(exc)) from exc
