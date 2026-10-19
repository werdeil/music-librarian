"""
music-librarian — point d'entrée unique.

  music-librarian range  [options]            rangement (librarian.py)
  music-librarian import [options]            import de l'Inbox (inbox.py)
  music-librarian undo JOURNAL [options]      annulation d'un passage
  music-librarian watch  [options]            surveillance de l'Inbox (watch.py)
  music-librarian enrich [options]            gain, pochettes, paroles manquants (enrich.py)

Tout est en dry-run sans --apply. `music-librarian <commande> --help` pour les options.
"""
import sys

from . import enrich, inbox, librarian, watch


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "range":
        return librarian.main(rest)
    if cmd == "import":
        return inbox.main(rest)
    if cmd == "undo":
        if not rest or rest[0].startswith("-"):
            print("usage : music-librarian undo JOURNAL [--apply] [options]", file=sys.stderr)
            return 2
        return librarian.main(["--undo", rest[0], *rest[1:]])
    if cmd == "watch":
        return watch.main(rest)
    if cmd == "enrich":
        return enrich.main(rest)
    print(f"commande inconnue : {cmd}\n\n{__doc__.strip()}", file=sys.stderr)
    return 2


def run():
    """Console entry point (`music-librarian`, `python -m music_librarian`)."""
    librarian.handle_stop_signals()
    return main()
