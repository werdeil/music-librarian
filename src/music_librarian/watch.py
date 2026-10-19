"""
watch — watches the Inbox and runs the import, continuously (the container's process).

Every --interval seconds the Inbox root is read again (a plain listing, the
library is not walked). The import runs once its content has not changed
since the previous tick and has not already been handled as is: no run on
every tick during a copy, nor for an item left in place. A pass also runs
once a day, empty Inbox included, to purge _done/.

With --organize-at HH:MM, the filing of changed folders (organize --since)
runs every day from that time, unless a full pass already happened since (state/last_run
date): a container restart or a manual pass does not make it run twice.
Without a known last pass nothing runs (a full pass is long, it is done by
hand).

  music-librarian watch           # dry-run import on every new drop
  music-librarian watch --apply   # actually import
  music-librarian watch --apply --organize-at 02:00 --enrich-at "sun 03:00"

With --enrich-at "DAY HH:MM" (mon … sun, French names accepted), the enrichment of the whole
library (enrich: missing gain, covers, lyrics) runs every week from that
slot, unless it already ran since (state/enrich_last_run). Without a known
pass, the first one runs at the next slot, not at startup.

Other options are passed to the import (--settle, --no-enrich…); --root and
--state-dir are passed to organize too.
On SIGTERM or Ctrl-C, the current pass stops cleanly, then the loop.
"""
import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from music_librarian import enrich, importer, organize
from music_librarian.core.lock import EXIT_LOCKED, STOP, handle_stop_signals
from music_librarian.core.log import Log
from music_librarian.core.state import state_path
from music_librarian.organize import LAST_RUN


def signature(inbox_dir: Path):
    """Inbox content (names, sizes, dates); None if it is empty or missing."""
    try:
        items = importer.list_items(inbox_dir)
    except FileNotFoundError:
        return None
    return tuple((it.name, tuple(it.snapshot())) for it in items) or None


def hhmm(s):
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a time as HH:MM: {s!r}") from None


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FRENCH_DAYS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]  # accepted too


def weekly(s):
    """'sun 03:00' -> (6, time(3, 0))."""
    try:
        day, at = s.split()
        day = day.lower()[:3]
        return (DAYS.index(day) if day in DAYS else FRENCH_DAYS.index(day)), hhmm(at)
    except (ValueError, argparse.ArgumentTypeError):
        raise argparse.ArgumentTypeError(
            f"expected a slot as 'DAY HH:MM' (DAY among {', '.join(DAYS)}): {s!r}"
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
        log(f"ERROR    {what} interrupted:\n{traceback.format_exc()}")
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Watch the Inbox and run the import.")
    inbox_env = os.environ.get("MUSIC_LIBRARIAN_INBOX")
    ap.add_argument("--inbox", type=Path, default=inbox_env, required=not inbox_env,
                    help="drop folder (default: $MUSIC_LIBRARIAN_INBOX)")
    ap.add_argument("--interval", type=float, default=30,
                    help="seconds between two reads of the Inbox")
    ap.add_argument("--apply", action="store_true", help="import and file (otherwise dry run)")
    ap.add_argument("--organize-at", type=hhmm, metavar="HH:MM",
                    help="run organize --since every day from this time")
    ap.add_argument("--enrich-at", type=weekly, metavar="DAY HH:MM",
                    help="enrich the whole library every week from this slot (e.g. 'sun 03:00')")
    ap.add_argument("--root")
    ap.add_argument("--state-dir")
    args, rest = ap.parse_known_args(argv)
    common = [*(["--root", args.root] if args.root else []),
              *(["--state-dir", args.state_dir] if args.state_dir else []),
              *(["--apply"] if args.apply else [])]
    import_argv = ["--inbox", str(args.inbox), *rest, *common]
    organize_argv = ["--since", *common]
    enrich_argv = list(common)
    state_dir = state_path(args.state_dir)
    started = now().timestamp()

    log = Log()
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] watching {args.inbox} "
        f"every {args.interval:g} s"
        + (f", organize every day at {args.organize_at:%H:%M}" if args.organize_at else "")
        + (f", enrich every {DAYS[args.enrich_at[0]]} at {args.enrich_at[1]:%H:%M}"
           if args.enrich_at else ""))
    previous = handled = None
    last_day = organize_day = warned_day = enrich_slot = None
    while not STOP.is_set():
        current = signature(args.inbox)
        if date.today() != last_day or (current is not None and current == previous
                                        and current != handled):
            code = safe(importer.main, import_argv, log, "import")
            if code != EXIT_LOCKED:
                # an item left in place is only retried once it changes
                handled, last_day = signature(args.inbox), date.today()
            log("")
        previous = current

        if args.organize_at and not STOP.is_set():
            t = now()
            slot = datetime.combine(t.date(), args.organize_at)
            last = last_run(state_dir)
            if t < slot or organize_day == t.date():
                pass
            elif last is None:
                if warned_day != t.date():
                    log(f"WARNING  nightly organize skipped: no last pass in {state_dir} "
                        f"(run 'organize --apply' once by hand)")
                    warned_day = t.date()
            elif last >= slot.timestamp():
                organize_day = t.date()   # full pass already done since the scheduled time
            else:
                log(f"ORGANIZE nightly (folders changed since "
                    f"{datetime.fromtimestamp(last):%Y-%m-%d %H:%M})")
                code = safe(organize.main, organize_argv, log, "organize")
                if code != EXIT_LOCKED:   # lock held by an import: next tick
                    organize_day = t.date()
                log("")

        if args.enrich_at and not STOP.is_set():
            slot = last_slot(now(), *args.enrich_at)
            # no known pass: the first one runs at the next slot
            last = enrich.last_run(state_dir) or started
            if last < slot.timestamp() and enrich_slot != slot:
                log(f"ENRICH   weekly (slot of {slot:%Y-%m-%d %H:%M})")
                code = safe(enrich.main, enrich_argv, log, "enrich")
                if code != EXIT_LOCKED:
                    enrich_slot = slot
                log("")
        STOP.wait(args.interval)
    log("STOP     watch ended")
    return 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
