"""What playcounts reads and writes in Lyrion's persist.db, on a connection the caller opened.

tracks_persistent holds Lyrion's own counts, alternativeplaycount those of the Alternative Play
Count plugin. Neither follows a file renamed: its entries stay under the old URL, in one table or
both, and the track starts again from nothing.
"""
from dataclasses import dataclass

from music_librarian.integrations import lyrion
from music_librarian.playcounts import dpsv


@dataclass(frozen=True)
class Entry:
    """The history kept under one URL: Lyrion's plays, or the plugin's without an entry of
    Lyrion's."""
    key: str
    url: str
    plays: int
    last_played: int
    rating: int


def entries(con):
    """The entries of files with plays, skips or a rating, in either table, the most played
    first."""
    found = {}
    for key, url, plays, last, rating in con.execute(
            "SELECT urlmd5, url, playCount, lastPlayed, rating FROM tracks_persistent "
            "WHERE url LIKE 'file://%' AND (IFNULL(playCount, 0) > 0 OR IFNULL(rating, 0) > 0)"):
        found.setdefault(key, Entry(key, url, plays or 0, int(last or 0), rating or 0))
    for key, url, plays, last in con.execute(
            "SELECT urlmd5, url, playCount, lastPlayed FROM alternativeplaycount "
            "WHERE url LIKE 'file://%' AND (IFNULL(playCount, 0) > 0 OR IFNULL(skipCount, 0) > 0)"):
        found.setdefault(key, Entry(key, url, plays or 0, int(last or 0), 0))
    return sorted(found.values(), key=lambda entry: (-entry.plays, entry.url))


def play_counts(con):
    """{urlmd5: plays} of the entries Lyrion counts plays for."""
    return dict(con.execute("SELECT urlmd5, playCount FROM tracks_persistent WHERE playCount > 0"))


def adopt(con, orphan: Entry, target_url):
    """Give the orphan's history to the track at `target_url`, each table's entry renamed or
    merged into the track's. Without an entry of the plugin's, Lyrion's plays are added to the
    plugin's entry for the track, whose DPSV follows."""
    own, alt = (con.execute(f"SELECT * FROM {table} WHERE urlmd5 = ?", (orphan.key,)).fetchone()
                for table in ("tracks_persistent", "alternativeplaycount"))
    if alt is not None:
        lyrion.carry(con, "alternativeplaycount", alt, target_url)
    elif own is not None:
        key = lyrion.md5(target_url)
        track = con.execute("SELECT * FROM alternativeplaycount WHERE urlmd5 = ?", (key,)).fetchone()
        if track is not None:
            plays = (track["playCount"] or 0) + (own["playCount"] or 0)
            last = max(track["lastPlayed"] or 0, own["lastPlayed"] or 0) or None
            value = dpsv.compute(plays, track["skipCount"], last, track["lastSkipped"])
            con.execute("UPDATE alternativeplaycount SET playCount = ?, lastPlayed = ?, dynPSval = ? "
                        "WHERE urlmd5 = ?", (plays, last, value, key))
    if own is not None:
        lyrion.carry(con, "tracks_persistent", own, target_url)


ZEROED = ("FROM tracks_persistent tp JOIN alternativeplaycount ap ON ap.urlmd5 = tp.urlmd5 "
          "WHERE IFNULL(tp.playCount, 0) = 0 AND IFNULL(ap.playCount, 0) > 0")


def zeroed(con):
    """[(url, plays)] of the tracks the plugin counts plays for and Lyrion none."""
    return con.execute(f"SELECT tp.url, ap.playCount {ZEROED} ORDER BY tp.url").fetchall()


def backfill(con):
    """Copy the plugin's plays and last play to the entries of zeroed()."""
    con.executemany(
        "UPDATE tracks_persistent SET (playCount, lastPlayed) = (SELECT ap.playCount, "
        "CAST(ap.lastPlayed AS INTEGER) FROM alternativeplaycount ap WHERE ap.urlmd5 = ?1) "
        "WHERE urlmd5 = ?1", con.execute(f"SELECT tp.urlmd5 {ZEROED}").fetchall())


def dpsv_changes(con):
    """[(urlmd5, url, value kept, value computed)] of the plugin's entries whose DPSV differs
    from the one their counts give."""
    rows = con.execute("SELECT urlmd5, url, playCount, skipCount, lastPlayed, lastSkipped, dynPSval "
                       "FROM alternativeplaycount ORDER BY url")
    return [(key, url, kept or 0, value) for key, url, plays, skips, last, last_skipped, kept in rows
            if (value := dpsv.compute(plays, skips, last, last_skipped)) != (kept or 0)]


def write_dpsv(con, changes):
    con.executemany("UPDATE alternativeplaycount SET dynPSval = ? WHERE urlmd5 = ?",
                    [(value, key) for key, _, _, value in changes])
