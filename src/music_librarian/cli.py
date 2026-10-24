"""
music-librarian — single entry point.

  music-librarian organize [options]          file the library (organize/)
  music-librarian import [options]            import the Inbox (importer.py)
  music-librarian undo JOURNAL [options]      undo a pass
  music-librarian watch [options]             watch the Inbox (watch.py)
  music-librarian enrich [options]            add missing gain, covers, lyrics (enrich/)
  music-librarian split [options]             split credits holding several names (split/)
  music-librarian dedupe [options]            unify one artist spelled several ways (dedupe/)

Everything is a dry run without --apply. `music-librarian <command> --help` for the options.
"""
import sys

from music_librarian import dedupe, enrich, importer, organize, split, watch
from music_librarian.core.lock import handle_stop_signals


COMMANDS = {"organize": organize, "import": importer, "watch": watch, "enrich": enrich,
            "split": split, "dedupe": dedupe}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "undo":
        if not rest or rest[0].startswith("-"):
            print("usage: music-librarian undo JOURNAL [--apply] [options]", file=sys.stderr)
            return 2
        return organize.main(["--undo", rest[0], *rest[1:]])
    if cmd in COMMANDS:
        return COMMANDS[cmd].main(rest)
    print(f"unknown command: {cmd}\n\n{__doc__.strip()}", file=sys.stderr)
    return 2


def run():
    """Console entry point (`music-librarian`, `python -m music_librarian`)."""
    handle_stop_signals()
    return main()
