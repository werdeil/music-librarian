"""What playcounts reads and writes in Lyrion's persist.db, on a connection the caller opened.

tracks_persistent holds Lyrion's own counts, alternativeplaycount those of the Alternative Play
Count plugin, which drops the entries of the files gone: an orphan is an entry of the first
without one in the second.
"""
from dataclasses import dataclass

from music_librarian.integrations import lyrion
from music_librarian.playcounts import dpsv


@dataclass(frozen=True)
class Orphan:
    key: str
    url: str
    plays: int
    last_played: int
    rating: int


def orphans(con):
    """The orphans with a history to give, the most played first."""
    rows = con.execute(
        "SELECT tp.urlmd5, tp.url, tp.playCount, tp.lastPlayed, tp.rating FROM tracks_persistent tp "
        "LEFT JOIN alternativeplaycount ap ON ap.urlmd5 = tp.urlmd5 WHERE ap.urlmd5 IS NULL "
        "AND (IFNULL(tp.playCount, 0) > 0 OR IFNULL(tp.rating, 0) > 0) "
        "ORDER BY tp.playCount DESC, tp.url")
    found = {}
    for key, url, plays, last, rating in rows:
        found.setdefault(key, Orphan(key, url or "", plays or 0, last or 0, rating or 0))
    return list(found.values())


def play_counts(con):
    """{urlmd5: plays} of the entries Lyrion counts plays for."""
    return dict(con.execute("SELECT urlmd5, playCount FROM tracks_persistent WHERE playCount > 0"))


def adopt(con, orphan: Orphan, target_url):
    """Give the orphan's history to the track at `target_url`: its entry of tracks_persistent
    renamed or merged, its plays added to the plugin's entry for the track, whose DPSV follows."""
    row = con.execute("SELECT * FROM tracks_persistent WHERE urlmd5 = ?", (orphan.key,)).fetchone()
    if row is None:
        return
    lyrion.carry(con, "tracks_persistent", row, target_url)
    key = lyrion.md5(target_url)
    alt = con.execute("SELECT * FROM alternativeplaycount WHERE urlmd5 = ?", (key,)).fetchone()
    if alt is None:
        return
    plays = (alt["playCount"] or 0) + (row["playCount"] or 0)
    last = max(alt["lastPlayed"] or 0, row["lastPlayed"] or 0) or None
    value = dpsv.compute(plays, alt["skipCount"], last, alt["lastSkipped"])
    con.execute("UPDATE alternativeplaycount SET playCount = ?, lastPlayed = ?, dynPSval = ? "
                "WHERE urlmd5 = ?", (plays, last, value, key))


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
