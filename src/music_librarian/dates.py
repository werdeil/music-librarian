"""
dates — the year of the edition owned, the one date a track holds.

A track holds the year its edition came out, alone (YYYY), in the tag Apple Music and Lyrion
read as the year: ©day in MP4, TDRC in MP3, DATE in FLAC. Every track of an album holds the same.
"""
import re

YEAR = re.compile(r"\d{4}")
UNKNOWN = "0000"
MISSING, FORMAT = "missing", "format"   # what is wrong with the year of a track, in this order


def is_year(value):
    """True for a year alone, YYYY."""
    return bool(YEAR.fullmatch(value)) and value != UNKNOWN


def problems(years):
    """What is wrong with the years of an album's tracks, `years` holding one value per track ("" for
    none): MISSING when a track has none, FORMAT when one is not a year alone, in that order."""
    out = ()
    if any(not y.strip() for y in years):
        out += (MISSING,)
    if any(y.strip() and not is_year(y.strip()) for y in years):
        out += (FORMAT,)
    return out
