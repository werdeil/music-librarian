#!/usr/bin/env python3
"""
watch — surveille l'Inbox et lance l'import, en continu (processus du conteneur).

Toutes les --interval secondes, la racine de l'Inbox est relue (simple listage,
la bibliothèque n'est pas parcourue). L'import est lancé quand son contenu n'a
pas bougé depuis le tour précédent et n'a pas déjà été traité tel quel : pas de
lancement à chaque tour pendant une copie, ni pour un élément laissé en place.
Un passage a lieu aussi une fois par jour, Inbox vide comprise, pour purger
_traités/.

Avec --range-at HH:MM, le rangement des dossiers modifiés (range --since) est
lancé chaque jour à partir de cette heure, sauf si un passage complet a déjà eu
lieu depuis (date de state/last_run) : un redémarrage du conteneur ou un
passage manuel ne le font pas tourner deux fois. Sans dernier passage connu,
rien n'est lancé (un passage complet est long, il se fait à la main).

  watch.py                        # import en dry-run à chaque nouveau dépôt
  watch.py --apply                # importer réellement
  watch.py --apply --range-at 02:00 --enrich-at "dim 03:00"

Avec --enrich-at "JOUR HH:MM", l'enrichissement de toute la bibliothèque
(enrich.py : gain, pochettes, paroles manquants) est lancé chaque semaine à
partir de ce créneau, sauf s'il a déjà eu lieu depuis (state/enrich_last_run).
Sans passage connu, le premier a lieu au créneau suivant, pas au démarrage.

Les autres options sont passées à l'import (--settle, --no-enrich…) ; --root et
--state-dir le sont aussi au rangement.
Sur SIGTERM ou Ctrl-C, le passage en cours s'arrête proprement, puis la boucle.
"""
import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import enrich
import import_inbox
import music_ranger
from music_ranger import EXIT_LOCKED, LAST_RUN, STOP, Log


def signature(inbox: Path):
    """Contenu de l'Inbox (noms, tailles, dates) ; None si elle est vide ou absente."""
    try:
        items = import_inbox.list_items(inbox)
    except FileNotFoundError:
        return None
    return tuple((it.name, tuple(it.snapshot())) for it in items) or None


def hhmm(s):
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        raise argparse.ArgumentTypeError(f"heure attendue au format HH:MM : {s!r}") from None


DAYS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


def weekly(s):
    """« dim 03:00 » → (6, time(3, 0))."""
    try:
        day, at = s.split()
        return DAYS.index(day.lower()[:3]), hhmm(at)
    except (ValueError, argparse.ArgumentTypeError):
        raise argparse.ArgumentTypeError(
            f"créneau attendu au format « JOUR HH:MM » (jour parmi {', '.join(DAYS)}) : {s!r}"
        ) from None


def last_slot(t: datetime, day, at):
    """Dernier créneau hebdomadaire passé (ou en cours) à l'instant t."""
    slot = datetime.combine(t.date() - timedelta(days=(t.weekday() - day) % 7), at)
    return slot if slot <= t else slot - timedelta(days=7)


def now():
    return datetime.now()


def last_run(state_dir: Path):
    try:
        return float((state_dir / LAST_RUN).read_text().strip())
    except (OSError, ValueError):
        return None


def safe(fn, argv, log, what):
    try:
        return fn(argv)
    except Exception:
        log(f"ERREUR   {what} interrompu :\n{traceback.format_exc()}")
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Surveille l'Inbox et lance l'import.")
    ap.add_argument("--inbox", type=Path, default=os.environ.get(
        "MUSIC_RANGER_INBOX", "/media/werdeil/internal_drive/Inbox_Musique"))
    ap.add_argument("--interval", type=float, default=30,
                    help="secondes entre deux lectures de l'Inbox")
    ap.add_argument("--apply", action="store_true", help="importer et ranger (sinon dry-run)")
    ap.add_argument("--range-at", type=hhmm, metavar="HH:MM",
                    help="lancer chaque jour, à partir de cette heure, range --since")
    ap.add_argument("--enrich-at", type=weekly, metavar="JOUR HH:MM",
                    help="lancer chaque semaine, à partir de ce créneau, l'enrichissement "
                         "de toute la bibliothèque (ex. « dim 03:00 »)")
    ap.add_argument("--root")
    ap.add_argument("--state-dir")
    args, rest = ap.parse_known_args(argv)
    common = [*(["--root", args.root] if args.root else []),
              *(["--state-dir", args.state_dir] if args.state_dir else []),
              *(["--apply"] if args.apply else [])]
    import_argv = ["--inbox", str(args.inbox), *rest, *common]
    range_argv = ["--since", *common]
    enrich_argv = list(common)
    state_dir = music_ranger.state_path(args.state_dir)
    started = now().timestamp()

    log = Log()
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] surveillance de {args.inbox}, "
        f"toutes les {args.interval:g} s"
        + (f", rangement chaque jour à {args.range_at:%H:%M}" if args.range_at else "")
        + (f", enrichissement le {DAYS[args.enrich_at[0]]} à {args.enrich_at[1]:%H:%M}"
           if args.enrich_at else ""))
    previous = handled = None
    last_day = range_day = warned_day = enrich_slot = None
    while not STOP.is_set():
        current = signature(args.inbox)
        if date.today() != last_day or (current is not None and current == previous
                                        and current != handled):
            code = safe(import_inbox.main, import_argv, log, "import")
            if code != EXIT_LOCKED:
                # un élément resté en place n'est repris que s'il change
                handled, last_day = signature(args.inbox), date.today()
            log("")
        previous = current

        if args.range_at and not STOP.is_set():
            t = now()
            slot = datetime.combine(t.date(), args.range_at)
            last = last_run(state_dir)
            if t < slot or range_day == t.date():
                pass
            elif last is None:
                if warned_day != t.date():
                    log(f"ATTENTION rangement de nuit sauté : pas de dernier passage dans "
                        f"{state_dir} (lancer une fois « range --apply » à la main)")
                    warned_day = t.date()
            elif last >= slot.timestamp():
                range_day = t.date()      # passage complet déjà fait depuis l'heure prévue
            else:
                log(f"RANGEMENT de nuit (dossiers modifiés depuis le "
                    f"{datetime.fromtimestamp(last):%Y-%m-%d %H:%M})")
                code = safe(music_ranger.main, range_argv, log, "rangement")
                if code != EXIT_LOCKED:   # verrou pris par un import : tour suivant
                    range_day = t.date()
                log("")

        if args.enrich_at and not STOP.is_set():
            slot = last_slot(now(), *args.enrich_at)
            # sans passage connu : le premier a lieu au créneau suivant
            last = enrich.last_run(state_dir) or started
            if last < slot.timestamp() and enrich_slot != slot:
                log(f"ENRICHISSEMENT hebdomadaire (créneau du {slot:%Y-%m-%d %H:%M})")
                code = safe(enrich.main, enrich_argv, log, "enrichissement")
                if code != EXIT_LOCKED:
                    enrich_slot = slot
                log("")
        STOP.wait(args.interval)
    log("ARRÊT    surveillance terminée")
    return 0


if __name__ == "__main__":
    music_ranger.handle_stop_signals()
    sys.exit(main())
