"""Whether a provider's answer is the requested recording: durations, titles, artists."""
import os
import re
import unicodedata

# Opt-in verification (batch CLI): how far a provider's track length may drift
# from the file's before it counts as another recording.
VERIFY_DURATION_TOLERANCE = int(os.getenv("LYRICS_VERIFY_DURATION_TOLERANCE", "3"))


def float_duration(duration):
    """Coerce a duration to seconds, fraction kept, or None."""
    if not duration:
        return None
    try:
        # Duration arrives as a string and may be fractional (e.g. "247.144").
        return float(duration)
    except (TypeError, ValueError):
        return None


def int_duration(duration):
    """Coerce a possibly-fractional string duration to whole seconds, or None."""
    seconds = float_duration(duration)
    return None if seconds is None else int(seconds)


def duration_delta(candidate, seconds):
    """Seconds between a candidate's own duration and the request's, fraction kept,
    or None when either is unknown."""
    got = float_duration(candidate.get("duration"))
    if seconds is None or got is None:
        return None
    return abs(got - seconds)


def duration_close(candidate, seconds):
    """True when a candidate's own duration allows it to be the same recording.

    A candidate that carries no duration, or a request that doesn't know its
    own, can't be told apart this way and is left to the caller's other checks.
    """
    delta = duration_delta(candidate, seconds)
    return delta is None or delta <= VERIFY_DURATION_TOLERANCE


def by_length(candidates, seconds):
    """Candidates ordered by nearness to the requested length, unknown lengths last.

    `sorted` is stable, so candidates the length cannot separate keep LRCLIB's
    own ordering.
    """
    def gap(candidate):
        delta = duration_delta(candidate, seconds)
        return float("inf") if delta is None else delta
    return sorted(candidates, key=gap)


# Qualifiers and credits that should not defeat a match: parenthetical/bracketed
# notes ("(Remastered 2011)", "[Live]") and everything from a "feat." onwards.
_PAREN_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT_RE = re.compile(r"\b(feat|ft|featuring)\b.*", re.IGNORECASE)
_NONALNUM_RE = re.compile(r"[\W_]+")
# A library and a catalogue routinely disagree on a leading article and the
# plural it carries ("Les Fatals Picards" tagged as "Fatal Picards").
_ARTICLE_RE = re.compile(r"^(?:the|an?|le|la|les|l|un|une|des|el|los|las|il|der|die|das)\b['\s]+",
                         re.IGNORECASE)


def normalize(text):
    """Fold a title/artist to a comparable core: accents stripped, lower-cased,
    parenthetical qualifiers and "feat." credits removed, punctuation collapsed."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = _PAREN_RE.sub(" ", text)
    text = _FEAT_RE.sub(" ", text)
    text = _NONALNUM_RE.sub(" ", text)
    return " ".join(text.split())


def drop_article(text):
    return _ARTICLE_RE.sub("", text) if text else text


def fold_name(text):
    words = drop_article(normalize(text)).split()
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def matches_request(meta, artist, title, duration):
    """True if a provider's matched candidate lines up with the requested track.

    Title and artist must be equal once folded (see fold_name), and both
    durations must be known and within VERIFY_DURATION_TOLERANCE seconds: a
    match never rests on title and artist alone.
    """
    if not meta:
        return False
    if fold_name(meta.get("title")) != fold_name(title):
        return False
    if fold_name(meta.get("artist")) != fold_name(artist):
        return False
    delta = duration_delta(meta, float_duration(duration))
    return delta is not None and delta <= VERIFY_DURATION_TOLERANCE
