"""FLAC → ALAC conversion: ffmpeg for the audio, mutagen for the tags and artwork, then checks."""
import json
import subprocess
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from music_librarian.organize.scan import parse_pair, read_tags

MAX_DRIFT = 0.1  # duration drift allowed between FLAC and ALAC, in seconds


class ConversionError(Exception):
    """The conversion failed or its result does not match the original."""


def probe(p: Path):
    """(audio codec, duration in seconds)"""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,codec_name", "-of", "json", str(p)],
                         capture_output=True, text=True, check=True)
    j = json.loads(out.stdout)
    codec = next((s.get("codec_name") for s in j.get("streams", [])
                  if s.get("codec_type") == "audio"), None)
    return codec, float(j.get("format", {}).get("duration") or 0)


# Vorbis (FLAC) → MP4. The rest is kept as iTunes freeform fields, except
# REPLAYGAIN_*: enrich.replaygain computes the gain as for the rest of the library.
MP4_TEXT = {
    "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album artist": "aART",
    "album": "\xa9alb", "date": "\xa9day", "genre": "\xa9gen", "composer": "\xa9wrt",
    "comment": "\xa9cmt", "description": "\xa9cmt", "lyrics": "\xa9lyr",
    "unsyncedlyrics": "\xa9lyr", "grouping": "\xa9grp", "copyright": "cprt",
    "titlesort": "sonm", "artistsort": "soar", "albumsort": "soal",
    "albumartistsort": "soaa", "composersort": "soco",
}
NUMBERS = {"tracknumber", "tracktotal", "totaltracks", "discnumber", "disctotal",
           "totaldiscs", "compilation", "bpm"}


def copy_tags(flac: FLAC, mp4: MP4):
    t = flac.tags or {}
    keys = {k.lower(): t[k] for k in t.keys()}
    for k, v in keys.items():
        if k in MP4_TEXT:
            mp4.tags[MP4_TEXT[k]] = list(mp4.tags.get(MP4_TEXT[k], [])) + list(v)
        elif k not in NUMBERS and not k.startswith("replaygain_"):
            mp4.tags[f"----:com.apple.iTunes:{k.upper()}"] = [
                MP4FreeForm(x.encode("utf-8")) for x in v]

    num = lambda *ks: next((parse_pair(keys[k][0]) for k in ks if k in keys),
                           (None, None))
    trk, trk_total = num("tracknumber")
    disc, disc_total = num("discnumber")
    trk_total = trk_total or num("tracktotal", "totaltracks")[0]
    disc_total = disc_total or num("disctotal", "totaldiscs")[0]
    if trk:
        mp4.tags["trkn"] = [(trk, trk_total or 0)]
    if disc:
        mp4.tags["disk"] = [(disc, disc_total or 0)]
    if keys.get("compilation", ["0"])[0].strip() == "1":
        mp4.tags["cpil"] = True
    bpm = num("bpm")[0]
    if bpm:
        mp4.tags["tmpo"] = [bpm]

    pics = sorted(flac.pictures, key=lambda p: p.type != 3)   # front cover first
    if pics:
        fmt = MP4Cover.FORMAT_PNG if pics[0].mime == "image/png" else MP4Cover.FORMAT_JPEG
        mp4.tags["covr"] = [MP4Cover(pics[0].data, imageformat=fmt)]


CHECKED = ("albumartist", "artist", "album", "title", "track", "disc", "disctotal",
           "compilation")


def convert(src: Path, dst: Path):
    """FLAC → ALAC, then checks. Raises ConversionError if anything is off."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    # moov ahead of mdat: Lyrion types an ALAC file with a leading mdat as alcx and transcodes it.
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src),
                        "-map", "0:a:0", "-c:a", "alac", "-map_metadata", "-1",
                        "-fflags", "+bitexact", "-movflags", "+faststart", str(dst)],
                       capture_output=True, text=True, check=False)
    if r.returncode:
        raise ConversionError(f"ffmpeg failed on {src.name}: {r.stderr.strip()[-300:]}")

    flac = FLAC(src)
    mp4 = MP4(dst)
    if mp4.tags is None:
        mp4.add_tags()
    copy_tags(flac, mp4)
    mp4.save()

    _, d_src = probe(src)
    codec, d_dst = probe(dst)
    if codec != "alac" or abs(d_src - d_dst) > MAX_DRIFT:
        raise ConversionError(f"check failed on {src.name}: {codec}, "
                     f"duration {d_src:.2f}s → {d_dst:.2f}s")
    a, b = read_tags(src) or {}, read_tags(dst) or {}
    diff = [k for k in CHECKED if a.get(k) != b.get(k)]
    if diff:
        raise ConversionError(f"tags differ after converting {src.name}: {', '.join(diff)}")
    if flac.pictures and "covr" not in MP4(dst).tags:
        raise ConversionError(f"cover lost converting {src.name}")
