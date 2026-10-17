#!/usr/bin/env python3
"""
watch — surveille l'Inbox et lance l'import, en continu (processus du conteneur).

Toutes les --interval secondes, la racine de l'Inbox est relue (simple listage,
la bibliothèque n'est pas parcourue). L'import est lancé quand son contenu n'a
pas bougé depuis le tour précédent et n'a pas déjà été traité tel quel : pas de
lancement à chaque tour pendant une copie, ni pour un élément laissé en place.
Un passage a lieu aussi une fois par jour, Inbox vide comprise, pour purger
_traités/.

  watch.py                        # import en dry-run à chaque nouveau dépôt
  watch.py --apply                # importer réellement

Les autres options sont passées à l'import (--root, --settle, --no-enrich…).
Sur SIGTERM ou Ctrl-C, l'import en cours s'arrête proprement, puis la boucle.
"""
import argparse
import os
import sys
import traceback
from datetime import date
from pathlib import Path

import import_inbox
import music_ranger
from music_ranger import EXIT_LOCKED, STOP, Log


def signature(inbox: Path):
    """Contenu de l'Inbox (noms, tailles, dates) ; None si elle est vide ou absente."""
    try:
        items = import_inbox.list_items(inbox)
    except FileNotFoundError:
        return None
    return tuple((it.name, tuple(it.snapshot())) for it in items) or None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Surveille l'Inbox et lance l'import.")
    ap.add_argument("--inbox", type=Path, default=os.environ.get(
        "MUSIC_RANGER_INBOX", "/media/werdeil/internal_drive/Inbox_Musique"))
    ap.add_argument("--interval", type=float, default=30,
                    help="secondes entre deux lectures de l'Inbox")
    ap.add_argument("--apply", action="store_true", help="importer (sinon dry-run)")
    args, rest = ap.parse_known_args(argv)
    import_argv = ["--inbox", str(args.inbox), *rest, *(["--apply"] if args.apply else [])]

    log = Log()
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] surveillance de {args.inbox}, "
        f"toutes les {args.interval:g} s")
    previous = handled = None
    last_day = None
    while not STOP.is_set():
        current = signature(args.inbox)
        new_day = date.today() != last_day
        if new_day or (current is not None and current == previous and current != handled):
            try:
                code = import_inbox.main(import_argv)
            except Exception:
                log(f"ERREUR   import interrompu :\n{traceback.format_exc()}")
                code = 2
            if code != EXIT_LOCKED:
                # un élément resté en place n'est repris que s'il change
                handled, last_day = signature(args.inbox), date.today()
            log("")
        previous = current
        STOP.wait(args.interval)
    log("ARRÊT    surveillance terminée")
    return 0


if __name__ == "__main__":
    music_ranger.handle_stop_signals()
    sys.exit(main())
