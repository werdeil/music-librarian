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
slot, unless it already ran since (state/enrich_last_run). It first reads every
file again into the metadata cache (enrich --rescan). Without a known
pass, the first one runs at the next slot, not at startup.

The import options (--settle, --no-enrich…) apply to every import; --root,
--state-dir and the Lyrion ones to organize too.
On SIGTERM or Ctrl-C, the current pass stops cleanly, then the loop.
"""
import argparse
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from music_librarian import enrich, importer, organize
from music_librarian.core.lock import EXIT_LOCKED, STOP, handle_stop_signals
from music_librarian.core.log import Log


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


def safe(fn, log, what):
    try:
        return fn()
    except Exception:
        log(f"ERROR    {what} interrupted:\n{traceback.format_exc()}")
        return 2


class Imports:
    """The import, once the Inbox content has settled and was not handled as is, and once a day."""

    def __init__(self, inbox: Path, run):
        self.inbox, self.run = inbox, run
        self.previous = self.handled = self.day = None

    def tick(self, log):
        current = signature(self.inbox)
        if date.today() != self.day or (current is not None and current == self.previous
                                        and current != self.handled):
            if safe(self.run, log, "import") != EXIT_LOCKED:
                # an item left in place is only retried once it changes
                self.handled, self.day = signature(self.inbox), date.today()
            log("")
        self.previous = current


class NightlyOrganize:
    """organize --since every day from `at`, unless a full pass already ran since."""

    def __init__(self, at, opts):
        self.at, self.opts = at, opts
        self.day = self.warned = None

    def tick(self, log):
        t = now()
        slot = datetime.combine(t.date(), self.at)
        last = organize.last_run(self.opts.state_dir)
        if t < slot or self.day == t.date():
            return
        if last is None:
            if self.warned != t.date():
                log(f"WARNING  nightly organize skipped: no last pass in {self.opts.state_dir} "
                    f"(run 'organize --apply' once by hand)")
                self.warned = t.date()
        elif last >= slot.timestamp():
            self.day = t.date()
        else:
            log(f"ORGANIZE nightly (folders changed since "
                f"{datetime.fromtimestamp(last):%Y-%m-%d %H:%M})")
            if safe(lambda: organize.run(self.opts, log).code, log, "organize") != EXIT_LOCKED:
                self.day = t.date()
            log("")


class WeeklyEnrich:
    """enrich every week from the (day, time) slot `at`, unless it already ran since."""

    def __init__(self, at, opts, started):
        self.at, self.opts, self.started = at, opts, started
        self.slot = None

    def tick(self, log):
        slot = last_slot(now(), *self.at)
        # no known pass: the first one runs at the next slot
        last = enrich.last_run(self.opts.state_dir) or self.started
        if last < slot.timestamp() and self.slot != slot:
            log(f"ENRICH   weekly (slot of {slot:%Y-%m-%d %H:%M})")
            if safe(lambda: enrich.run(self.opts, log), log, "enrich") != EXIT_LOCKED:
                self.slot = slot
            log("")


def main(argv=None):
    ap = importer.parser()
    ap.description = "Watch the Inbox and run the import."
    ap.add_argument("--interval", type=float, default=30,
                    help="seconds between two reads of the Inbox")
    ap.add_argument("--organize-at", type=hhmm, metavar="HH:MM",
                    help="run organize --since every day from this time")
    ap.add_argument("--enrich-at", type=weekly, metavar="DAY HH:MM",
                    help="enrich the whole library every week from this slot (e.g. 'sun 03:00')")
    args = ap.parse_args(argv)
    import_opts = importer.options(args)
    state_dir = import_opts.state_dir
    organize_opts = organize.Options(root=import_opts.root, since=True, apply=args.apply,
                                     state_dir=state_dir, lyrion=import_opts.lyrion)
    enrich_opts = enrich.Options(root=import_opts.root, apply=args.apply, state_dir=state_dir,
                                 rescan=True)
    import_log = importer.new_log(import_opts)
    jobs = [Imports(args.inbox, lambda: importer.run(import_opts, import_log))]
    if args.organize_at:
        jobs.append(NightlyOrganize(args.organize_at, organize_opts))
    if args.enrich_at:
        jobs.append(WeeklyEnrich(args.enrich_at, enrich_opts, now().timestamp()))

    log = Log()
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] watching {args.inbox} "
        f"every {args.interval:g} s"
        + (f", organize every day at {args.organize_at:%H:%M}" if args.organize_at else "")
        + (f", enrich every {DAYS[args.enrich_at[0]]} at {args.enrich_at[1]:%H:%M}"
           if args.enrich_at else ""))
    while not STOP.is_set():
        for job in jobs:
            if STOP.is_set():
                break
            job.tick(log)
        STOP.wait(args.interval)
    log("STOP     watch ended")
    return 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
