"""Mirror each album folder's cover file into the tags of its tracks.

Lyrion displays the artwork embedded in the files and ignores a folder's cover
file entirely, so the two drift apart silently. This makes the folder's file the
single source of truth: wherever a track's embedded artwork differs from it,
byte for byte, the file is written in. Lyrion picks the change up on its next
scan.

Usage:
    python -m music_librarian.enrich.covers /path/to/music [--dry-run] [--name folder.jpg]
                                            [--verbose]

Several targets are accepted, and shell-style wildcards work even when quoted
(or when the shell finds no match and passes the pattern through literally):
    python -m music_librarian.enrich.covers /path/to/music/A*
    python -m music_librarian.enrich.covers "/path/to/music/A*" /path/to/music/B*
Each target is a directory, scanned recursively for folders holding music, or a
single music file.

Nothing is written with --dry-run. Either way the run reports how much the
audio files grow: embedding a cover rewrites every track, so a 2 MB sleeve
across a twelve-track album adds 24 MB that then has to resync and back up.

The comparison is on bytes, not on dimensions, so replacing a folder.jpg with a
*smaller* one propagates too — which is the point: the folder decides. Only
tracks that actually differ are rewritten, so an interrupted run resumes
cheaply. A stop request lets the current album finish, then stops (exit code 4);
a failed album gives exit code 2.
"""

import argparse
import os
import sys

from music_librarian.core.lock import STOP, handle_stop_signals
from music_librarian.core.log import Log, count
from music_librarian.core.state import state_path
from music_librarian.enrich.common import album_folders, finish, label
from music_librarian.media import artwork, tags
from music_librarian.media.metadata_cache import MetadataCache


def read_folder_cover(folder, name):
    """Return (bytes, dimensions) for the folder's cover file, or None.

    The name is matched without regard to case, so a Folder.jpg written by a
    Windows tagger is found too. A file that doesn't read as an image is
    refused rather than embedded as artwork.
    """
    try:
        entries = {n.lower(): n for n in os.listdir(folder)}
    except OSError:
        return None
    real = entries.get(name.lower())
    if not real:
        return None
    try:
        with open(os.path.join(folder, real), "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    dims = artwork.image_size(data)
    return (data, dims) if dims else None


def stale(files, data, cache):
    """The tracks whose embedded artwork is not exactly `data`."""
    digest = tags.cover_hash(data)
    return [path for path in files if (cache.snapshot(path) or {}).get("cover") != digest]


def process(folder, files, data, dims, log):
    """Write `data` into every track given, returning the failures as text."""
    failures = []
    for path in files:
        try:
            tags.write_cover(path, data)
        except tags.CoverTagError as exc:
            failures.append(f"{os.path.basename(path)}: {exc}")
    rel = label(folder)
    if failures:
        log(f"FAILED   {rel}: {len(failures)} of {count(len(files), 'track')} failed")
        for line in failures[:3]:
            log(f"         {line}")
    else:
        log(f"COVER    {rel}  {dims[1]}x{dims[2]} into {count(len(files), 'track')}")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Embed each album folder's cover file into the tags of its tracks."
    )
    parser.add_argument(
        "targets", nargs="+",
        help="Directories to process. Shell-style wildcards (e.g. A*) are accepted, even quoted.",
    )
    parser.add_argument(
        "--name", default="folder.jpg",
        help="Name of the cover file to look for in each album folder (default folder.jpg).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report which albums would be re-tagged, without writing anything.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Log every album, including those skipped.",
    )
    args = parser.parse_args(argv)
    log = Log()

    counts = dict.fromkeys(("scanned", "re-tagged", "up to date", f"without {args.name}", "failed"), 0)
    added = 0
    stopped = False

    with MetadataCache(state_path()) as cache:
        for folder, files in album_folders(args.targets, log):
            if STOP.is_set():
                log("STOP     requested: the next albums are left for the next pass")
                stopped = True
                break
            counts["scanned"] += 1
            rel = label(folder)

            cover = read_folder_cover(folder, args.name)
            if not cover:
                counts[f"without {args.name}"] += 1
                if args.verbose:
                    log(f"SKIPPED  (no {args.name})  {rel}")
                continue

            data, dims = cover
            outdated = stale(files, data, cache)
            if not outdated:
                counts["up to date"] += 1
                if args.verbose:
                    log(f"SKIPPED  (up to date)  {rel}")
                continue

            added += len(data) * len(outdated)
            if args.dry_run:
                counts["re-tagged"] += 1
                log(f"WOULD    {rel}  {dims[1]}x{dims[2]} into {len(outdated)} of "
                    f"{count(len(files), 'track')}")
                continue

            counts["failed" if process(folder, outdated, data, dims, log) else "re-tagged"] += 1

    if added:
        counts["audio growth (MB)"] = round(added / 1024 / 1024)
    return finish("Covers", counts, stopped, log)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
