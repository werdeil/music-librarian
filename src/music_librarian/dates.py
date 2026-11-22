"""
dates — the year of the edition owned, the one date a track holds.

A track holds the year its edition came out, alone (YYYY), in the tag Apple Music and Lyrion
read as the year: ©day in MP4, TDRC in MP3, DATE in FLAC. Every track of an album holds the same.
No other date: Lyrion shows the original date of an MP3 or a FLAC that holds one instead.

enrich, and import on the folders it files, run it before repeats (--no-dates on enrich to leave
them): a whole date (2011-05-02, or Apple's 2011-05-02T07:00:00Z) is cut down to its year, and the
original and recording dates are removed (TDOR, TORY, TRDA, ORIGINALDATE, ORIGINALYEAR,
RECORDINGDATE, as a FLAC tag, an MP4 freeform or a TXXX, and a FLAC YEAR beside a DATE). A track
without a year, or with one that is no date, is counted, and listed by the dashboard: which year is
right is not for the tool to guess.

Every tag change goes through the journal: `music-librarian undo` restores it, but a TRDA, which
ID3v2.4 has no frame for.
"""
import re

from music_librarian import repeats
from music_librarian.core.files import relative
from music_librarian.media import fields

YEAR = re.compile(r"\d{4}")
WHOLE_DATE = re.compile(r"(\d{4})-\d{2}(?:-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?)?")
UNKNOWN = "0000"
MISSING, FORMAT = "missing", "format"   # what is wrong with the year of a track, in this order
OTHER = ("originaldate", "originalyear", "recordingdate", "trda")   # their names in a snapshot


def is_year(value):
    """True for a year alone, YYYY."""
    return bool(YEAR.fullmatch(value)) and value != UNKNOWN


def cut(value):
    """The year of `value`, a year alone or a whole date; None for anything else."""
    value = value.strip()
    if is_year(value):
        return value
    whole = WHOLE_DATE.fullmatch(value)
    return whole.group(1) if whole and whole.group(1) != UNKNOWN else None


def problems(years):
    """What is wrong with the years of an album's tracks, `years` holding one value per track ("" for
    none): MISSING when a track has none, FORMAT when one is not a year alone, in that order."""
    out = ()
    if any(not y.strip() for y in years):
        out += (MISSING,)
    if any(y.strip() and not is_year(y.strip()) for y in years):
        out += (FORMAT,)
    return out


def _easy(snap):
    return {k.lower(): [v.strip() for v in vs if v.strip()] for k, vs in snap["tags"].items()}


def years_of(snap):
    """The values of a snapshot's year: its date tag's, else its year tag's."""
    easy = _easy(snap)
    return easy.get("date") or easy.get("year", [])


def findings(snap):
    """(the year to write in place of the track's, or None; whether it holds other dates; what is
    wrong with its year that the tool leaves: MISSING, FORMAT or None) of a metadata cache snapshot."""
    values, others = years_of(snap), other_dates_of(snap)
    if not values:
        return None, bool(others), MISSING
    years = {cut(v) for v in values}
    if None in years:
        return None, bool(others), FORMAT
    if len(years) > 1:   # repeats reports them as MULTIPLE
        return None, bool(others), None
    year = years.pop()
    return (year if values != [year] else None), bool(others), None


def other_dates_of(snap):
    """The dates of a snapshot other than the year, a year tag beside a date tag included, as a log
    line shows them; "" without any."""
    easy = _easy(snap)
    names = OTHER + (("year",) if easy.get("date") else ())
    return "; ".join(f"{k} {v}" for k in names for v in easy.get(k, []))


def plan(scan, root, state_dir, log, stats):
    """[(path, field, None, values to write)] under `scan`, through the metadata cache, the old values
    to be read from the file (credit_tags.rewrite_tags)."""
    writes = []
    for p, snap in repeats.snapshots(scan, root, state_dir, log, stats):
        year, others, left = findings(snap)
        if others:   # before the year: writing it removes a FLAC YEAR, which only DATES journals as it was
            log(f"DATE     removed: {other_dates_of(snap)}  {relative(p, root)}")
            writes.append((p, fields.DATES, None, []))
            stats["dates to remove"] += 1
        if year is not None:
            log(f"DATE     year → {year!r}  {relative(p, root)}")
            writes.append((p, "year", None, [year]))
            stats["years to cut"] += 1
        if left == FORMAT:
            log(f"INVALID  year: {'; '.join(years_of(snap))!r}  {relative(p, root)}")
            stats["not a year"] += 1
        elif left == MISSING:
            stats["without a year"] += 1
    return writes


def dates_pass(opts, log):
    """A dates pass over `opts` (a split.library.CreditOptions), as repeats.tag_pass runs it."""
    return repeats.tag_pass(opts, log, "dates", plan,
                            ("Dates", ("scanned", "years to cut", "dates to remove", "rewritten",
                                       "without a year", "not a year", "skipped", "errors")))
