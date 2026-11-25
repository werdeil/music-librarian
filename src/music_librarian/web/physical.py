"""The physical albums on the dashboard: an album's mark set by hand from its page, written at once
through tag, and the releases of the collection only close to an album, one question per page.
Both read the collection the last physical pass kept, never Discogs; the answers go to
physical.json, for the next pass."""
import json
import os

from flask import abort, redirect, render_template, request, url_for

from music_librarian.physical import collection, marks
from music_librarian.physical.matching import LibraryAlbum, album_key, match, pair_key
from music_librarian.web import library
from music_librarian.web.common import place, submit

DISCOGS_RELEASE = "https://www.discogs.com/release/"
CHOICES = {"auto": None, "yes": True, "no": False}


def first(tags, *names):
    return next((v for n in names for v in tags.get(n, []) if v.strip()), "")


def albums_of(catalog):
    """The LibraryAlbum of each folder of a library.Catalog."""
    held = {}
    for t in catalog.tracks:
        album = held.get(t.folder)
        if album is None:
            album = held[t.folder] = LibraryAlbum(t.folder, "", "")
        album.artist = album.artist or first(t.tags, "albumartist", "album artist", "artist")
        album.title = album.title or first(t.tags, "album")
        album.ids |= {v for v in t.tags.get("discogs_release_id", []) if v.strip()}
    return list(held.values())


def collection_stamp(state_dir):
    try:
        st = os.stat(state_dir / collection.FILE)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class Doubts:
    """The releases of the kept collection only close to an album, not answered yet."""

    def __init__(self, ctx, index, later):
        self.ctx, self.index, self.later = ctx, index, later
        self.built = None   # (catalog, collection stamp, [(release, album)])

    def queue(self):
        """[(release, LibraryAlbum)] before any answer, None until the album list is built,
        [] without a collection kept."""
        catalog = self.index.catalog()
        if catalog is None:
            return None
        stamp = collection_stamp(self.ctx.state_dir)
        if self.built is None or self.built[0] is not catalog or self.built[1] != stamp:
            kept = collection.kept(self.ctx.state_dir)
            found = match(kept["releases"], albums_of(catalog), {}).doubtful if kept else []
            self.built = catalog, stamp, found
        return self.built[2]

    def item(self, start=0):
        """The question `start` places after the first still awaiting an answer, as a dict for the
        page, or None; and the count of those left, None until the album list is built."""
        queue = self.queue()
        if queue is None:
            return None, None
        answers = marks.load(self.ctx.state_dir)[1]
        keys = [pair_key(r["id"], a.key) for r, a in queue]
        left = [i for i, k in enumerate(keys) if k not in answers]
        i, number = self.later.pick("physical", left, lambda i: keys[i], start, len(queue))
        if i is None:
            return None, len(left)
        release, album = queue[i]
        return dict(number=number, total=len(queue), later=keys[i], key=keys[i], release=release,
                    url=DISCOGS_RELEASE + release["id"], album=album), len(left)


def album_state(state_dir, tracks):
    """What an album's page shows of its mark, from its tracks as library.tracks_of() reads them."""
    readable = [values for _, values in tracks if values]
    artist = next((v for values in readable for v in values["albumartist"] or values["artist"]), "")
    title = next((v for values in readable for v in values["album"]), "")
    key = album_key(artist, title)
    ids = sorted({v for values in readable for v in values["discogs_release"]})
    kept = collection.kept(state_dir)
    release = next((r for r in kept["releases"] if r["id"] in ids), None) if kept else None
    return dict(key=key, manual=marks.load(state_dir)[0].get(key), tracks=len(readable),
                marked=sum(marks.marked(values["comment"]) for values in readable),
                ids=ids, release=release, url=DISCOGS_RELEASE + release["id"] if release else None)


def marker_edits(rel, tracks, wanted):
    """tag's dashboard edits that give every readable track the marker, or take it away."""
    out = []
    for name, values in tracks:
        if values and marks.marked(values["comment"]) != wanted:
            new = marks.with_marker(values["comment"]) if wanted else marks.without_marker(values["comment"])
            out.append([f"{rel}/{name}", "comment", "\n".join(new), values["comment"]])
    return out


def register(app, ctx, index, later):
    """Add the album's mark and the review of the doubtful releases to the app; return the Doubts,
    `later` the reviews.Postponed the questions put off are kept in."""
    doubts = Doubts(ctx, index, later)

    @app.post("/library/album/physical")
    def album_physical():
        """Mark the album by hand, written at once through tag, or give it back to Discogs."""
        rel = request.form.get("folder", "")
        folder = library.album_folder(ctx.root, rel)
        choice = request.form.get("choice")
        if folder is None or choice not in CHOICES:
            abort(404)
        if ctx.requests is None:
            abort(403)
        tracks = library.tracks_of(folder)
        marks.set_manual(ctx.state_dir, album_state(ctx.state_dir, tracks)["key"], CHOICES[choice])
        edits = marker_edits(rel, tracks, CHOICES[choice]) if CHOICES[choice] is not None else []
        if not edits:
            return redirect(url_for("album_page", folder=rel, physical=choice, **place()))
        return submit(ctx, "tag", {"edits": edits, "cover": None}, ctx.apply, rel)

    @app.get("/reviews/physical")
    def review_physical():
        item, left = doubts.item(request.args.get("from", 0, type=int))
        try:
            done = json.loads(request.args["done"]) if "done" in request.args else None
        except ValueError:
            done = None
        kept = collection.kept(ctx.state_dir)
        return render_template("review_physical.html", item=item, left=left, done=done, kept=kept,
                               answered=len(marks.load(ctx.state_dir)[1]))

    @app.post("/reviews/physical")
    def answer_physical():
        key, choice = request.form.get("key", ""), request.form.get("choice")
        if not key or choice not in ("yes", "no"):
            abort(400)
        marks.set_answer(ctx.state_dir, key, choice == "yes")
        done = json.dumps(dict(key=key, choice=choice, release=request.form.get("release", ""),
                               album=request.form.get("album", "")))
        return redirect(url_for("review_physical", done=done))

    @app.post("/reviews/physical/undo")
    def undo_physical():
        key = request.form.get("key", "")
        if not key:
            abort(400)
        marks.set_answer(ctx.state_dir, key, None)
        return redirect(url_for("review_physical"))

    return doubts
