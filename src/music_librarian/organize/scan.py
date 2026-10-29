"""Which files a pass looks at, and the tags it files them by."""
import os
from pathlib import Path

from mutagen import File as MFile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4


def first(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def to_int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_pair(s):
    """'3/12' -> (3, 12) ; '3' -> (3, None)"""
    if not s:
        return None, None
    parts = str(s).split("/")
    return to_int(parts[0]), (to_int(parts[1]) if len(parts) > 1 else None)


def read_tags(path: Path):
    f = MFile(path)
    if f is None or f.tags is None:
        return None
    t = f.tags

    if isinstance(f, MP4):
        g = lambda k: first(t.get(k))
        trk = (t.get("trkn") or [(0, 0)])[0]
        dsk = (t.get("disk") or [(0, 0)])[0]
        cpil = t.get("cpil")
        if isinstance(cpil, list):
            cpil = cpil[0] if cpil else False
        return dict(
            albumartist=g("aART"), artist=g("\xa9ART"), album=g("\xa9alb"),
            title=g("\xa9nam"), track=trk[0] or None, disc=dsk[0] or None,
            disctotal=dsk[1] or None, compilation=bool(cpil),
        )

    if isinstance(f, MP3):
        g = lambda k: first(t[k].text) if k in t else None
        tr, _ = parse_pair(g("TRCK"))
        d, dt = parse_pair(g("TPOS"))
        return dict(
            albumartist=g("TPE2"), artist=g("TPE1"), album=g("TALB"),
            title=g("TIT2"), track=tr, disc=d, disctotal=dt,
            compilation=(g("TCMP") == "1"),
        )

    if isinstance(f, FLAC):
        g = lambda k: first(t.get(k))
        tr, _ = parse_pair(g("tracknumber"))
        d, dt = parse_pair(g("discnumber"))
        dt = dt or to_int(g("disctotal") or g("totaldiscs"))
        return dict(
            albumartist=g("albumartist") or g("album artist"), artist=g("artist"),
            album=g("album"), title=g("title"), track=tr, disc=d, disctotal=dt,
            compilation=(g("compilation") == "1"),
        )

    return None


def tags_from_snapshot(snap):
    """read_tags() taken from a metadata cache snapshot (media/tags.read_snapshot)."""
    easy = {k.lower(): v for k, v in snap["tags"].items()}
    g = lambda *keys: next((first(easy[k]) for k in keys if first(easy.get(k))), None)
    tr, _ = parse_pair(g("tracknumber"))
    d, dt = parse_pair(g("discnumber"))
    return dict(
        albumartist=g("albumartist", "album artist"), artist=g("artist"), album=g("album"),
        title=g("title"), track=tr, disc=d, disctotal=dt or to_int(g("disctotal", "totaldiscs")),
        compilation=g("compilation") == "1",
    )


def changed_since(p: Path, since):
    try:
        return os.lstat(p).st_ctime > since
    except OSError:
        return False


def collect(scan, since=None):
    """Files to examine. With `since`, only the folders where at least one file
    changed since then (ctime: a rewritten tag or a rename updates it), but then
    the whole folder, so side files never leave with only some of the tracks."""
    for base in scan:
        if base.is_file():           # single file passed as an argument
            yield base
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            paths = [Path(dirpath) / n for n in sorted(filenames)]
            if since is not None and not any(changed_since(p, since) for p in paths):
                continue
            yield from paths
