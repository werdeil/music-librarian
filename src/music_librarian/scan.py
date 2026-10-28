"""
scan — read the metadata of the whole library into the metadata cache.

Only the files whose size or modification time changed since they were last
read are opened, and the files gone from the library are dropped, so after a
first full read a scan takes as long as listing the library. Every command that
checks tags (enrich and its tools, import) reads through the same cache. Nothing
in the library is changed.

  music-librarian scan                # refresh state/metadata.db, then the summary

Exit codes: 0 = OK, 4 = stopped before the end.
"""
import argparse
import os
import sys
from collections import Counter

from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.core.log import Log
from music_librarian.core.state import state_path
from music_librarian.enrich.common import album_folders
from music_librarian.media import metadata_cache
from music_librarian.media.metadata_cache import MetadataCache

PROGRESS_EVERY = 1000


def run(root, state_dir, log):
    """Refresh the cache for every music file under `root`; return the exit code."""
    genres = Counter()
    seen, reported, unreadable = [], 0, 0
    with MetadataCache(state_dir) as cache:
        for _folder, files in album_folders([str(root)], log):
            if STOP.is_set():
                log("STOP     requested: the files left are read on the next scan")
                break
            for path in files:
                snap = cache.snapshot(path)
                seen.append(path)
                if snap is None:
                    log(f"ERROR    {path}: no readable tags")
                    unreadable += 1
                genres.update(metadata_cache.genres(snap)[:1] or ["(none)"])
                if cache.read_from_disk >= reported + PROGRESS_EVERY:
                    reported = cache.read_from_disk
                    log(f"SCAN     {reported} files read, {len(seen)} seen")
        stopped = STOP.is_set()
        removed = 0 if stopped else cache.prune(root, seen)
        read, cached = cache.read_from_disk, cache.from_cache

    log(f"\n--- Scan summary{' (stopped)' if stopped else ''} ---")
    for key, value in (("files", len(seen)), ("read", read), ("from cache", cached),
                       ("removed", removed), ("unreadable", unreadable)):
        log(f"{key:>20}: {value}")
    log("\n--- Tracks per genre ---")
    for genre, n in sorted(genres.items(), key=lambda kv: (-kv[1], kv[0])):
        log(f"{n:>8}  {genre}")
    return EXIT_STOPPED if stopped else 0


def main(argv=None):
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap = argparse.ArgumentParser(description="Read the library's metadata into the metadata cache.")
    ap.add_argument("--root", default=root_env, required=not root_env)
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"))
    args = ap.parse_args(argv)
    log = Log()
    log(f"[SCAN] {args.root}")
    return run(args.root, state_path(args.state_dir), log)


if __name__ == "__main__":
    sys.exit(main())
