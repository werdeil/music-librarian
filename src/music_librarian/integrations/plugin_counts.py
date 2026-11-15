"""The counts of the Alternative Play Count plugin kept for the tracks a carry-over moved.

Some 20 seconds after a scan, the plugin deletes its entries of files Lyrion's library does not
hold and adds an empty one for each new file: an entry carried over to a path Lyrion has not
scanned yet is lost when a scan that missed the file ends in between. The counts each moved track
ended with are kept in the state folder, keyed by urlmd5, and given back once the plugin's entry
is there again with fewer plays and skips than kept.
"""
import json
import os
from pathlib import Path

FILE = "lyrion-plugin-counts.json"
TABLE = "alternativeplaycount"
SUMMED, LATEST = ("playCount", "skipCount"), ("lastPlayed", "lastSkipped")
KEEP = 30 * 86400  # seconds the counts of a track are kept


def load(path: Path):
    """{urlmd5: {"url", "kept" (when), playCount, skipCount, lastPlayed, lastSkipped, dynPSval}}."""
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def save(path: Path, kept):
    if not kept:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
    os.replace(temp, path)


def events(counts):
    return sum(counts[c] or 0 for c in SUMMED)


def entry(con, key):
    return con.execute(f"SELECT * FROM {TABLE} WHERE urlmd5 = ?", (key,)).fetchone()


def lost(con, kept):
    """[(urlmd5, the counts kept)] of the entries back with fewer plays and skips than kept."""
    return [(key, counts) for key, counts in kept.items()
            if (row := entry(con, key)) is not None and events(row) < events(counts)]


def give_back(con, key, counts):
    """Add the counts kept to the entry of `key`, the plays and skips made since staying."""
    row = entry(con, key)
    vals = {c: (row[c] or 0) + (counts[c] or 0) or None for c in SUMMED}
    vals |= {c: max(row[c] or 0, counts[c] or 0) or None for c in LATEST}
    vals["dynPSval"] = counts["dynPSval"]
    sets = ", ".join(f"{c} = ?" for c in vals)
    con.execute(f"UPDATE {TABLE} SET {sets} WHERE urlmd5 = ?", (*vals.values(), key))


def settle(con, kept, now):
    """Give back the counts of lost(), and forget those and the ones kept for KEEP; return how
    many were given back. `kept` is changed in place, to save() once the transaction is over."""
    back = lost(con, kept)
    for key, counts in back:
        give_back(con, key, counts)
        del kept[key]
    for key in [key for key, counts in kept.items() if now - counts["kept"] > KEEP]:
        del kept[key]
    return len(back)


def follow(con, kept, old, new, url, now):
    """After the carry-over of the entry `old` to `new` (urlmd5s), `url` being the new one: keep
    the counts the track now has, those kept under `old` given back first to an entry without
    them, or kept on for a track with no entry."""
    counts = kept.pop(old, None)
    row = entry(con, new)
    if counts and row is not None and events(row) < events(counts):
        give_back(con, new, counts)
        row = entry(con, new)
    if row is not None and events(row):
        kept[new] = dict(url=url, kept=now, **{c: row[c] for c in (*SUMMED, *LATEST, "dynPSval")})
    elif counts:
        kept[new] = dict(counts, url=url)
