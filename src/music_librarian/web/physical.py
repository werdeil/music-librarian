"""The physical albums on the dashboard: what an album's page shows of its mark, and the releases of
the collection only close to an album or matching none, one question per page. They read the
collection the last physical pass kept, never Discogs; the answers go to physical.json, for the
next pass."""
import difflib
import json
import os
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

from music_librarian.physical import Track, collection, marks, plan
from music_librarian.physical.matching import LibraryAlbum, loose, pair_key, words

DISCOGS_RELEASE = "https://www.discogs.com/release/"
FOUND = 12


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


def search(albums, text):
    """The albums whose artist, title or folder hold every word of `text`, or whose title is like
    it, the likest first, at most FOUND of them."""
    wanted, key = words(text).split(), loose(text)
    likeness = lambda a: difflib.SequenceMatcher(None, key, loose(a.title)).ratio()
    found = [a for a in albums if key and (all(w in words(f"{a.artist} {a.title} {a.folder}").split()
                                               for w in wanted) or likeness(a) >= 0.6)]
    return sorted(found, key=lambda a: (-likeness(a), a.folder.casefold()))[:FOUND]


class Doubts:
    """The releases of the kept collection only close to an album, then those the next pass finds
    no album of, as the dashboard asks about them."""

    def __init__(self, ctx, index, later):
        self.ctx, self.index, self.later = ctx, index, later
        self.built = None   # (catalog, stamps of the collection and of physical.json, albums, [question])

    def queue(self):
        """([LibraryAlbum] no release matches, [(release, LibraryAlbum, or None for a release to
        link)]), None until the album list is built, no question without a collection kept."""
        catalog = self.index.catalog()
        if catalog is None:
            return None
        stamp = stamp_of(self.ctx.state_dir / collection.FILE), stamp_of(self.ctx.state_dir / marks.FILE)
        if self.built is None or self.built[0] is not catalog or self.built[1] != stamp:
            kept = collection.kept(self.ctx.state_dir)
            albums, tracks = library_of(catalog)
            found = []
            if kept:
                answers = marks.load(self.ctx.state_dir)["answers"]
                found = plan(albums, tracks, kept["releases"], {})[1].doubtful
                matches = plan(albums, tracks, kept["releases"], answers)[1]
                found += [(r, None) for r in matches.absent]
                albums = [a for a in albums if a.folder not in matches.found]
            self.built = catalog, stamp, albums, found
        return self.built[2], self.built[3]

    def item(self, start=0, text=None):
        """The question `start` places after the first still awaiting an answer, as a dict for the
        page, or None; and the count of those left, None until the album list is built. A release
        to link comes with the albums `text` finds, its title without one."""
        built = self.queue()
        if built is None:
            return None, None
        albums, queue = built
        held = marks.load(self.ctx.state_dir)
        folders = {a.folder for a in albums}
        keys = [pair_key(r["id"], a.key) if a else r["id"] for r, a in queue]
        answered = lambda i: (keys[i] in held["answers"] if queue[i][1] else
                              held["links"].get(keys[i]) in folders | {""})
        left = [i for i in range(len(queue)) if not answered(i)]
        i, number = self.later.pick("physical", left, lambda i: keys[i], start, len(queue))
        if i is None:
            return None, len(left)
        release, album = queue[i]
        item = dict(number=number, total=len(queue), later=keys[i], key=keys[i], release=release,
                    url=DISCOGS_RELEASE + release["id"], album=album, start=start)
        if album is None:
            item["text"] = release["title"] if text is None else text
            item["found"] = search(albums, item["text"])
        return item, len(left)


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
        item, left = doubts.item(request.args.get("from", 0, type=int), request.args.get("q"))
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
        done = json.dumps(dict(key=key, kind="answers", choice=choice, album=request.form.get("album", "")))
        return redirect(url_for("review_physical", done=done))

    @app.post("/reviews/physical/link")
    def link_physical():
        key = request.form.get("key", "")
        folder = "" if "none" in request.form else request.form.get("folder", "")
        built = doubts.queue()
        if not key.isdigit() or built is None or folder and folder not in {a.folder for a in built[0]}:
            abort(400)
        marks.update(ctx.state_dir, "links", {key: folder})
        done = json.dumps(dict(key=key, kind="links", album=request.form.get("release", ""), folder=folder))
        return redirect(url_for("review_physical", done=done))

    @app.post("/reviews/physical/undo")
    def undo_physical():
        key, kind = request.form.get("key", ""), request.form.get("kind", "answers")
        if not key or kind not in marks.KINDS:
            abort(400)
        marks.update(ctx.state_dir, kind, {key: None})
        return redirect(url_for("review_physical"))

    return doubts
