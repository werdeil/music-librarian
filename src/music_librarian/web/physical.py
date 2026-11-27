"""The physical albums on the dashboard: what an album's page shows of its mark, and the releases of
the collection only close to an album, one question per page. They read the collection the last
physical pass kept, never Discogs; the answers go to physical.json, for the next pass."""
import json
import os
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

from music_librarian.physical import Track, collection, marks, plan
from music_librarian.physical.matching import LibraryAlbum, pair_key

DISCOGS_RELEASE = "https://www.discogs.com/release/"


def first(tags, *names):
    return next((v for n in names for v in tags.get(n, []) if v.strip()), "")


def library_of(catalog):
    """([LibraryAlbum], {folder: [Track]}) of a library.Catalog, as physical.plan() takes them."""
    albums, tracks = {}, {}
    for t in catalog.tracks:
        album = albums.get(t.folder)
        if album is None:
            album = albums[t.folder] = LibraryAlbum(t.folder, "", "")
            tracks[t.folder] = []
        album.artist = album.artist or first(t.tags, "albumartist", "album artist", "artist")
        album.title = album.title or first(t.tags, "album")
        ids = [v for v in t.tags.get("discogs_release_id", []) if v.strip()]
        album.ids |= set(ids)
        tracks[t.folder].append(Track(Path(t.folder) / t.name, t.tags.get("comment", []), ids,
                                      "compilation" in t.flags))
    for folder, album in albums.items():
        album.compilation = all(t.compilation for t in tracks[folder])
    return list(albums.values()), tracks


def stamp_of(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class Doubts:
    """The releases of the kept collection only close to an album, as the next pass asks about them."""

    def __init__(self, ctx, index, later):
        self.ctx, self.index, self.later = ctx, index, later
        self.built = None   # (catalog, stamps of the collection and of physical.json, [(release, album)])

    def queue(self):
        """[(release, LibraryAlbum)], None until the album list is built, [] without a collection kept."""
        catalog = self.index.catalog()
        if catalog is None:
            return None
        stamp = stamp_of(self.ctx.state_dir / collection.FILE), stamp_of(self.ctx.state_dir / marks.FILE)
        if self.built is None or self.built[0] is not catalog or self.built[1] != stamp:
            kept = collection.kept(self.ctx.state_dir)
            found = plan(*library_of(catalog), kept["releases"], {})[1].doubtful if kept else []
            self.built = catalog, stamp, found
        return self.built[2]

    def item(self, start=0):
        """The question `start` places after the first still awaiting an answer, as a dict for the
        page, or None; and the count of those left, None until the album list is built."""
        queue = self.queue()
        if queue is None:
            return None, None
        answers = marks.load(self.ctx.state_dir)["answers"]
        keys = [pair_key(r["id"], a.key) for r, a in queue]
        left = [i for i, k in enumerate(keys) if k not in answers]
        i, number = self.later.pick("physical", left, lambda i: keys[i], start, len(queue))
        if i is None:
            return None, len(left)
        release, album = queue[i]
        return dict(number=number, total=len(queue), later=keys[i], key=keys[i], release=release,
                    url=DISCOGS_RELEASE + release["id"], album=album), len(left)


def album_state(tracks):
    """What an album's head shows of its mark, from its tracks as library.tracks_of() reads them."""
    readable = [values for _, values in tracks if values]
    return dict(marked=sum(marks.marked(values["comment"]) for values in readable), tracks=len(readable))


def register(app, ctx, index, later):
    """Add the review of the doubtful releases to the app; return the Doubts, `later` the
    reviews.Postponed the questions put off are kept in."""
    doubts = Doubts(ctx, index, later)

    @app.get("/reviews/physical")
    def review_physical():
        item, left = doubts.item(request.args.get("from", 0, type=int))
        try:
            done = json.loads(request.args["done"]) if "done" in request.args else None
        except ValueError:
            done = None
        return render_template("review_physical.html", item=item, left=left, done=done,
                               kept=collection.kept(ctx.state_dir))

    @app.post("/reviews/physical")
    def answer_physical():
        key, choice = request.form.get("key", ""), request.form.get("choice")
        if not key or choice not in ("yes", "no"):
            abort(400)
        marks.update(ctx.state_dir, "answers", {key: choice == "yes"})
        done = json.dumps(dict(key=key, choice=choice, album=request.form.get("album", "")))
        return redirect(url_for("review_physical", done=done))

    @app.post("/reviews/physical/undo")
    def undo_physical():
        key = request.form.get("key", "")
        if not key:
            abort(400)
        marks.update(ctx.state_dir, "answers", {key: None})
        return redirect(url_for("review_physical"))

    return doubts
