#!/usr/bin/env python3
"""
watch — watches the Inbox and runs the import, continuously (the container's process).

Every --interval seconds the Inbox root is read again (a plain listing, the
library is not walked). The import runs once its content has not changed
since the previous tick and has not already been handled as is: no run on
every tick during a copy, nor for an item left in place. A pass also runs
once a day, empty Inbox included, to purge _traités/.

With --range-at HH:MM, the range of changed folders (range --since) runs every
day from that time, unless a full pass already happened since (state/last_run
date): a container restart or a manual pass does not make it run twice.
Without a known last pass nothing runs (a full pass is long, it is done by
hand).

  watch.py                        # dry-run import on every new drop
  watch.py --apply                # actually import
  watch.py --apply --range-at 02:00 --enrich-at "dim 03:00"

With --enrich-at "DAY HH:MM" (French day names), the enrichment of the whole
library (enrich.py: missing gain, covers, lyrics) runs every week from that
slot, unless it already ran since (state/enrich_last_run). Without a known
pass, the first one runs at the next slot, not at startup.

Other options are passed to the import (--settle, --no-enrich…); --root and
--state-dir are passed to range too.
On SIGTERM or Ctrl-C, the current pass stops cleanly, then the loop.
"""
import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import enrich
import inbox
import librarian
from librarian import EXIT_LOCKED, LAST_RUN, STOP, Log


def signature(inbox_dir: Path):
    """Inbox content (names, sizes, dates); None if it is empty or missing."""
    try:
        items = inbox.list_items(inbox_dir)
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
    """Last weekly slot passed (or in progress) at time t."""
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
    inbox_env = os.environ.get("MUSIC_LIBRARIAN_INBOX")
    ap.add_argument("--inbox", type=Path, default=inbox_env, required=not inbox_env,
                    help="dossier de dépôt (défaut : $MUSIC_LIBRARIAN_INBOX)")
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
    state_dir = librarian.state_path(args.state_dir)
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
            code = safe(inbox.main, import_argv, log, "import")
            if code != EXIT_LOCKED:
                # an item left in place is only retried once it changes
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
                range_day = t.date()      # full pass already done since the scheduled time
            else:
                log(f"RANGEMENT de nuit (dossiers modifiés depuis le "
                    f"{datetime.fromtimestamp(last):%Y-%m-%d %H:%M})")
                code = safe(librarian.main, range_argv, log, "rangement")
                if code != EXIT_LOCKED:   # lock held by an import: next tick
                    range_day = t.date()
                log("")

        if args.enrich_at and not STOP.is_set():
            slot = last_slot(now(), *args.enrich_at)
            # no known pass: the first one runs at the next slot
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
    librarian.handle_stop_signals()
    sys.exit(main())
