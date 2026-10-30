"""
music-librarian — single entry point.

  music-librarian organize [options]          file the library (organize/)
  music-librarian import [options]            import the Inbox (importer.py)
  music-librarian undo JOURNAL [options]      undo a pass
  music-librarian watch [options]             watch the Inbox (watch.py)
  music-librarian enrich [options]            align genres, add missing gain, covers, lyrics (enrich/)
  music-librarian split [options]             split credits holding several names (split/)
  music-librarian dedupe [options]            unify one artist spelled several ways (dedupe/)
  music-librarian scan [options]              read the library's metadata into the cache (scan.py)
  music-librarian tag PATH... --set F=V       rewrite tags, then file the tracks again (tag.py)

Everything is a dry run without --apply. `music-librarian <command> --help` for the options,
`music-librarian --version` for the version.
"""
import sys

from music_librarian import __version__, dedupe, enrich, importer, organize, scan, split, tag, watch
from music_librarian.core.lock import handle_stop_signals


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    if argv[0] == "--version":
        print(f"music-librarian {__version__}")
        return 0
    cmd, rest = argv[0], argv[1:]
    commands = {"organize": organize.main, "import": importer.main, "watch": watch.main,
                "enrich": enrich.main, "scan": scan.main, "split": split.main, "dedupe": dedupe.main,
                "tag": tag.main, "undo": organize.undo_main}
    if cmd in commands:
        return commands[cmd](rest)
    print(f"unknown command: {cmd}\n\n{__doc__.strip()}", file=sys.stderr)
    return 2


def run():
    """Console entry point (`music-librarian`, `python -m music_librarian`)."""
    handle_stop_signals()
    return main()
