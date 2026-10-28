"""Batch-embed lyrics into the tags of every music file in a folder.

Walks a directory, reads each track's metadata, fetches plain-text lyrics from
the configured web providers (lyrics/) and writes them into the file's
lyrics tag (media/tags.py). Lyrion is never involved: run it whenever, then let
Lyrion pick the changes up on its next scan.

Usage:
    python -m music_librarian.enrich.lyrics /path/to/music [--dry-run] [--force]
                                            [--clear] [--no-verify] [--delay 0.5]
                                            [--verbose]

Because tags are written permanently (and then propagate to Lyrion, other
players and backups), a result is only accepted when the provider's own
metadata matches the file's title/artist/duration — a mismatch is worse than no
lyrics, and a miss can always be retried later. Pass --no-verify to turn that
check off and take whatever a provider returns.

--clear drops the existing lyrics tag when nothing is found online, so the file
matches what providers actually carry. Like --force it processes already-tagged
files too (it must look them up to know whether to clear), so a --clear run
hits the web for every file; combine with --force to also overwrite when found.
A result that fails verification counts as "nothing usable found", so --clear
treats it like a miss and still drops the tag — verification decides what gets
written, not what gets cleared. A file whose lookup reached no provider at all
is left untouched instead: that says nothing about what the providers carry.

Several targets are accepted, and shell-style wildcards work even when quoted
(or when the shell finds no match and passes the pattern through literally):
    python -m music_librarian.enrich.lyrics /path/to/music/A*
    python -m music_librarian.enrich.lyrics "/path/to/music/A*" /path/to/music/B*
Each target may be a directory (scanned recursively) or a single music file.

A file looked up in vain is not looked up again for LYRICS_RETRY_DAYS (default 30),
unless its artist, title or album changes or --force is given (lyrics/misses.py).

A stop request lets the current file finish, then stops (exit code 4); a tag that
could not be written gives exit code 2. A miss, a rejected match or an unreachable
provider is not a failure: the next pass looks the file up again.

Config comes from the environment. Provider order comes from LYRICS_PROVIDERS
(defaults to lrclib,netease; add musixmatch to use it); LRCLIB_TIMEOUT (seconds, default 15)
is honored too — raise it for big batches when LRCLIB is slow under load.
"""

import argparse
import os
import sys
import time

from music_librarian.core.lock import STOP, handle_stop_signals
from music_librarian.core.log import Log
from music_librarian.core.state import state_path
from music_librarian.enrich.common import album_folders, finish, label
from music_librarian.lyrics import fetch_lyrics
from music_librarian.lyrics.misses import Misses
from music_librarian.media import metadata_cache, tags
from music_librarian.media.metadata_cache import MetadataCache


def music_files(targets, log):
    """(path, name shown in the log) for every music file under `targets`, in order."""
    for folder, files in album_folders(targets, log):
        for path in files:
            yield path, os.path.join(label(folder), os.path.basename(path))


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Embed web lyrics into the tags of music files in a folder."
    )
    parser.add_argument(
        "targets", nargs="+",
        help="Directories or files to process. Shell-style wildcards (e.g. "
             "A*) are accepted, even quoted.",
    )
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="Seconds to wait between web lookups, to stay polite (default 0.5).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite the lyrics tag even when one is already present, and look up "
             "the files found in vain recently too.",
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Remove an existing lyrics tag when nothing is found online "
             "(processes already-tagged files too, so it queries the web for "
             "every file).",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Accept a provider's lyrics even when its own title/artist/duration "
             "don't match the file. Off by default: since tags are written "
             "permanently, a mismatched result is worse than no lyrics.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen without writing any tag.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Log every file, including those skipped.",
    )
    return parser.parse_args(argv)


def lookup(meta, args):
    """The plain lyrics found online for a track and the provider's answer."""
    result = fetch_lyrics(
        artist=meta["artist"],
        title=meta["title"],
        album=meta.get("album"),
        duration=meta.get("duration"),
        verify=not args.no_verify,
    )
    time.sleep(args.delay)
    plain = result.get("lyrics")
    if not plain and result.get("synced"):
        plain = tags.lrc_to_plain(result["synced"])
    return plain, result


def write(path, rel, plain, args, log):
    """Write `plain` (or clear the tag when it is None); the outcome counted."""
    verb, done = ("clear", "CLEARED ") if plain is None else ("write", "LYRICS  ")
    if args.dry_run:
        log(f"WOULD    {verb} {rel}")
        return "cleared" if plain is None else "written"
    try:
        if plain is None:
            tags.clear_lyrics(path)
        else:
            tags.write_lyrics(path, plain)
    except tags.LyricsTagError as exc:
        log(f"FAILED   {rel}: {exc}")
        return "failed"
    log(f"{done} {rel}")
    return "cleared" if plain is None else "written"


MISSES = {"unavailable": ("unreachable", "OFFLINE  {}  (lyrics services unreachable)"),
          "rejected": ("rejected", "REJECTED {}  (no confident match)")}


def handle(path, rel, args, log, cache, misses):
    """Look one file up and write what was found; the outcome counted."""
    snap = cache.snapshot(path)
    meta = metadata_cache.metadata(snap)
    if not meta or not meta.get("artist") or not meta.get("title"):
        if args.verbose:
            log(f"SKIPPED  (no artist or title)  {rel}")
        return "no metadata"
    already = snap["lyrics"]
    # --force overwrites and --clear may drop the tag: both have to look the file up
    if already and not args.force and not args.clear:
        if args.verbose:
            log(f"SKIPPED  (has lyrics)  {rel}")
        return "already"
    if not already and not args.force and misses.recent(path, meta):
        if args.verbose:
            log(f"SKIPPED  (looked up in vain recently)  {rel}")
        return "searched recently"

    plain, result = lookup(meta, args)
    source = result.get("source")
    if plain:
        misses.forget(path)
        if already and not args.force:
            if args.verbose:
                log(f"SKIPPED  (found online, kept the existing lyrics)  {rel}")
            return "already"
        return write(path, rel, plain, args, log)
    # an unreachable provider says nothing about what the providers carry: never cleared
    if already and args.clear and source != "unavailable":
        return write(path, rel, None, args, log)
    key, line = MISSES.get(source, ("not found", "NONE     {}"))
    log(line.format(rel))
    if key != "unreachable":
        misses.record(path, meta)
    return key


def main(argv=None):
    args = parse_args(argv)
    log = Log()
    counts = dict.fromkeys(("scanned", "written", "cleared", "already", "not found", "rejected",
                            "unreachable", "searched recently", "no metadata", "failed"), 0)
    stopped = False
    with MetadataCache(state_path()) as cache, Misses(state_path()) as misses:
        for path, rel in music_files(args.targets, log):
            if STOP.is_set():
                log("STOP     requested: the next files are left for the next pass")
                stopped = True
                break
            counts["scanned"] += 1
            counts[handle(path, rel, args, log, cache, misses)] += 1

    return finish("Lyrics", counts, args.dry_run, stopped, log)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
