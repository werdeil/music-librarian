"""
music-librarian — single entry point.

  music-librarian organize [options]          file the library (organize/)
  music-librarian import [options]            import the Inbox (importer.py)
  music-librarian undo JOURNAL [options]      undo a pass
  music-librarian watch [options]             watch the Inbox (watch.py)
  music-librarian enrich [options]            add missing gain, covers, lyrics (enrich/)

Everything is a dry run without --apply. `music-librarian <command> --help` for the options.
`range` is the former name of `organize`.
"""
import sys

from music_librarian import enrich, importer, organize, watch
from music_librarian.core.lock import handle_stop_signals


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "range":
        print("music-librarian: 'range' is now 'organize', the old name will be removed",
              file=sys.stderr)
        cmd = "organize"
    if cmd == "organize":
        return organize.main(rest)
    if cmd == "import":
        return importer.main(rest)
    if cmd == "undo":
        if not rest or rest[0].startswith("-"):
            print("usage: music-librarian undo JOURNAL [--apply] [options]", file=sys.stderr)
            return 2
        return organize.main(["--undo", rest[0], *rest[1:]])
    if cmd == "watch":
        return watch.main(rest)
    if cmd == "enrich":
        return enrich.main(rest)
    print(f"unknown command: {cmd}\n\n{__doc__.strip()}", file=sys.stderr)
    return 2


def run():
    """Console entry point (`music-librarian`, `python -m music_librarian`)."""
    handle_stop_signals()
    return main()
