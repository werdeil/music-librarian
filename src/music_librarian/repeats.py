"""
repeats — the text fields holding a value twice, or several values: a track has one title, one
artist tag, and names no artist twice in it.

A field holding the same value several times, or a credit (album artist, artist, composer) naming
someone twice ("A; A"), is rewritten with the value once, the names once each. Values that differ
("Toujours pizza" and "Toujours pizza (Remix)", or "Walk Off The Earth" and "Walk Off the
Earth"), or names that differ only in case, are reported as MULTIPLE and left for the album's page
or `tag`: which one is right is not for the tool to guess.

The title and the album take one value, without ";" (SINGLE): organize refuses a track whose
title or album holds values that differ or a ";", and so import an item holding one, and tag and
the dashboard refuse to write one (refusal).

enrich runs it before split, which leaves a field with several values alone, and import on the
folders it files (--no-repeats on enrich to leave them). organize and the dashboard report the
fields it leaves.

Every tag change goes through the journal: `music-librarian undo` restores it.
"""
import sys
import time
import unicodedata
from collections import defaultdict

from music_librarian.core.files import AUDIO_EXT, is_junk, relative
from music_librarian.core.journal import Journal, finish
from music_librarian.media import credit_tags
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.organize.scan import collect
from music_librarian.split.rules import LYRION_SEPARATOR, key, names_of

FIELDS = ("title", "artist", "albumartist", "album", "year", "genre", "composer")
CREDITS = ("artist", "albumartist", "composer")
SINGLE = ("title", "album")
SEPARATOR = ";"
SNAPSHOT_KEYS = {"title": ("title",), "artist": ("artist",), "albumartist": ("albumartist", "album artist"),
                 "album": ("album",), "year": ("date", "year"), "genre": ("genre",),
                 "composer": ("composer",)}


def same(text):
    """The text as two repeats of it compare: Unicode form and extra spaces aside, case kept."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def once(items):
    kept = {}
    for item in items:
        kept.setdefault(same(item), item)
    return list(kept.values())


def refusal(field, values):
    """Why `values` cannot be written in `field`, or None: a field of SINGLE takes one value,
    without SEPARATOR."""
    values = [v for v in values if v.strip()]
    if field in SINGLE and (len(values) > 1 or any(SEPARATOR in v for v in values)):
        return f'{field} takes one value, without "{SEPARATOR}"'
    return None


def check(field, values):
    """(the one value to write in place of `values`, or None when they hold no repeat; whether
    they still hold values, or names of a credit, that differ, or a field of SINGLE a ";", for
    someone to decide)."""
    values = [v.strip() for v in values if v.strip()]
    distinct = once(values)
    if len(distinct) != 1:
        return None, len(distinct) > 1
    value = distinct[0]
    differ = refusal(field, [value]) is not None
    if field in CREDITS:
        names = once(names_of(value))
        differ = len({key(n) for n in names}) < len(names)
        if len(names) < len(names_of(value)):
            value = LYRION_SEPARATOR.join(names)
    return (value if [value] != values else None), differ


def findings(values):
    """[(field, values, value to write or None, whether they differ)] of the fields of FIELDS in
    `values` ({field: [values]}) holding a repeat or values that differ."""
    out = []
    for field in FIELDS:
        new, differ = check(field, values.get(field, []))
        if new is not None or differ:
            out.append((field, [v.strip() for v in values[field] if v.strip()], new, differ))
    return out


def from_snapshot(snap):
    """{field: [values]} of FIELDS taken from a metadata cache snapshot; None for an unreadable file."""
    if snap is None:
        return None
    easy = {k.lower(): v for k, v in snap["tags"].items()}
    return {field: [v for k in keys for v in easy.get(k, [])] for field, keys in SNAPSHOT_KEYS.items()}


def to_decide(values):
    """[(field, values)] of the fields of `values` ({field: [values]}) whose values differ, left for
    someone to decide."""
    return [(field, old) for field, old, _, differ in findings(values) if differ]


def shown(values):
    return "; ".join(values)


def multiple_line(path, root, field, values):
    """The MULTIPLE line of the log for a field whose values differ."""
    return f"MULTIPLE {field}: {shown(values)!r}  {relative(path, root)}"


def plan(scan, root, state_dir, log, stats):
    """[(path, field, values, value to write)] under `scan`, through the metadata cache; the fields
    whose values differ are logged as MULTIPLE."""
    writes = []
    with MetadataCache(state_dir) as cache:
        for p in collect(scan):
            if is_junk(p) or p.suffix.lower() not in AUDIO_EXT:
                continue
            values = from_snapshot(cache.snapshot(p))
            if values is None:
                log(f"SKIPPED  (unreadable)  {relative(p, root)}")
                stats["skipped"] += 1
                continue
            stats["scanned"] += 1
            for field, old, new, differ in findings(values):
                if new is not None:
                    log(f"REPEAT   {field}: {shown(old)!r} → {new!r}  {relative(p, root)}")
                    writes.append((p, field, old, new))
                if differ:
                    log(multiple_line(p, root, field, [new] if new is not None else old))
                    stats["to decide"] += 1
    return writes


def repeats_pass(opts, log):
    """A repeats pass over `opts` (a split.library.CreditOptions), without taking the lock: the
    caller holds it when `opts.apply`."""
    started = time.time()
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] repeated values: {', '.join(map(str, opts.scan))}")
    stats = defaultdict(int)
    writes = plan(opts.scan, opts.root, opts.state_dir, log, stats)
    journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    stopped = opts.apply and credit_tags.rewrite_tags(writes, journal, log, stats)
    journal.close()

    stats["tags to rewrite"] = len(writes)
    log("\n--- Repeats summary ---")
    for k in ("scanned", "tags to rewrite", "rewritten", "to decide", "skipped", "errors"):
        if stats[k]:
            log(f"{k:>20}: {stats[k]}")
    return finish(journal, opts.root, log, stats, stopped)
