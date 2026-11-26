"""The physical albums on the dashboard: an album's PHYSICAL marks toggled from its page, written at
once through tag, and the questions of the next physical pass, one per page: a release only close to
an album, the edition to add an album marked but without a Discogs id, and an album never synced
marked but not in the collection. They read the collection the last pass kept; the answers go to
physical.json. An album's editions, asked for on its question, are the one lookup of Discogs, and
write nothing."""
import json
import os
import re
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

from music_librarian.editions import discogs
from music_librarian.editions.common import ProviderUnavailable
from music_librarian.physical import CONFLICT, EDITION, Track, collection, marks, plan
from music_librarian.physical.matching import LibraryAlbum, pair_key
from music_librarian.web import library
from music_librarian.web.common import submit

DISCOGS_RELEASE = "https://www.discogs.com/release/"
DISCOGS_SEARCH = "https://www.discogs.com/search/"
RELEASE_ID = re.compile(r"(?:/release/)?(\d+)(?:-[^/]*)?/?$")
KINDS = ("match", EDITION, CONFLICT)


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
        tracks[t.folder].append(Track(Path(t.folder) / t.name, t.tags.get("comment", []), ids))
    return list(albums.values()), tracks


def collection_stamp(state_dir):
    try:
        st = os.stat(state_dir / collection.FILE)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def release_id(text):
    """The release id of what was typed: the id itself or the address of its page; None for neither."""
    found = RELEASE_ID.search((text or "").strip())
    return found[1] if found else None


class Questions:
    """The questions of the next pass, as it would ask them from the kept collection."""

    def __init__(self, ctx, index, later):
        self.ctx, self.index, self.later = ctx, index, later
        self.built = None   # (catalog, collection stamp, physical.json stamp, [(kind, key, what)])

    def queue(self):
        """[(kind, key, {release, album})] in KINDS' order, None until the album list is built,
        [] without a collection kept."""
        catalog = self.index.catalog()
        if catalog is None:
            return None
        stamp = collection_stamp(self.ctx.state_dir), marks_stamp(self.ctx.state_dir)
        if self.built is None or self.built[0] is not catalog or self.built[1] != stamp:
            self.built = catalog, stamp, self._questions(catalog)
        return self.built[2]

    def _questions(self, catalog):
        kept = collection.kept(self.ctx.state_dir)
        if not kept:
            return []
        changes, matches = plan(*library_of(catalog), kept["releases"], marks.load(self.ctx.state_dir))
        out = [("match", pair_key(r["id"], a.key), dict(release=r, album=a)) for r, a in matches.doubtful]
        for kind in (EDITION, CONFLICT):
            out += [(kind, c.album.key, dict(release=None, album=c.album))
                    for c in changes if c.action == kind]
        return out

    def settled(self, kind, key):
        held = marks.load(self.ctx.state_dir)
        return key in held[{"match": "answers", EDITION: "editions", CONFLICT: "conflicts"}[kind]]

    def item(self, start=0):
        """The question `start` places after the first still awaiting an answer, as a dict for the
        page, or None; and the count of those left, None until the album list is built."""
        queue = self.queue()
        if queue is None:
            return None, None
        left = [i for i, (kind, key, _) in enumerate(queue) if not self.settled(kind, key)]
        later = lambda i: f"{queue[i][0]}\t{queue[i][1]}"
        i, number = self.later.pick("physical", left, later, start, len(queue))
        if i is None:
            return None, len(left)
        kind, key, what = queue[i]
        album = what["album"]
        release = what["release"]
        return dict(number=number, total=len(queue), later=later(i), kind=kind, key=key, album=album,
                    release=release, url=DISCOGS_RELEASE + release["id"] if release else None,
                    search=f"{DISCOGS_SEARCH}?type=release&q={album.artist} {album.title}"), len(left)


def marks_stamp(state_dir):
    try:
        st = os.stat(state_dir / marks.FILE)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def album_state(state_dir, tracks):
    """What an album's head shows of its mark, from its tracks as library.tracks_of() reads them."""
    readable = [values for _, values in tracks if values]
    ids = sorted({v for values in readable for v in values["discogs_release"]})
    kept = collection.kept(state_dir)
    release = next((r for r in kept["releases"] if r["id"] in ids), None) if kept else None
    return dict(marked=sum(marks.marked(values["comment"]) for values in readable), tracks=len(readable),
                ids=ids, release=release, url=DISCOGS_RELEASE + ids[0] if ids else None)


def marker_edits(rel, tracks, wanted):
    """tag's dashboard edits that give every readable track the marker, or take it away."""
    out = []
    for name, values in tracks:
        if values and marks.marked(values["comment"]) != wanted:
            new = marks.with_marker(values["comment"]) if wanted else marks.without_marker(values["comment"])
            out.append([f"{rel}/{name}", "comment", "\n".join(new), values["comment"]])
    return out


def editions_of(album):
    """[edition, with its "id"] Discogs lists of the album, None when it cannot be reached. Writes nothing."""
    try:
        found = discogs.fetch({"artist": album.artist, "title": album.title})
    except ProviderUnavailable:
        return None
    return [dict(e, id=release_id(e["url"])) for e in found if release_id(e["url"])][:20]


def register(app, ctx, index, later):
    """Add the album's toggle and the review of the next pass's questions to the app; return the
    Questions, `later` the reviews.Postponed the questions put off are kept in."""
    questions = Questions(ctx, index, later)

    @app.post("/library/album/physical")
    def album_physical():
        """Give every track of the album PHYSICAL, or with physical=0 take it away, through tag."""
        rel = request.form.get("folder", "")
        folder = library.album_folder(ctx.root, rel)
        if folder is None:
            abort(404)
        edits = marker_edits(rel, library.tracks_of(folder), request.form.get("physical") != "0")
        if not edits:
            return redirect(url_for("album_page", folder=rel))
        return submit(ctx, "tag", {"edits": edits, "cover": None}, ctx.apply, rel)

    @app.get("/reviews/physical")
    def review_physical():
        item, left = questions.item(request.args.get("from", 0, type=int))
        try:
            done = json.loads(request.args["done"]) if "done" in request.args else None
        except ValueError:
            done = None
        editions = None
        if item and item["kind"] == EDITION and request.args.get("editions") and ctx.requests is not None:
            editions = editions_of(item["album"])
        return render_template("review_physical.html", item=item, left=left, done=done, editions=editions,
                               kept=collection.kept(ctx.state_dir))

    @app.post("/reviews/physical")
    def answer_physical():
        form = request.form
        kind, key, choice = form.get("kind"), form.get("key", ""), form.get("choice", "")
        if kind == "match" and choice in ("yes", "no"):
            name, value = "answers", choice == "yes"
        elif kind == CONFLICT and choice in ("add", "drop"):
            name, value = "conflicts", choice
        elif kind == EDITION and release_id(choice):
            name, value = "editions", release_id(choice)
        else:
            abort(400)
        if not key:
            abort(400)
        marks.update(ctx.state_dir, name, {key: value})
        done = json.dumps(dict(kind=kind, name=name, key=key, value=value, album=form.get("album", "")))
        return redirect(url_for("review_physical", done=done))

    @app.post("/reviews/physical/undo")
    def undo_physical():
        name, key = request.form.get("name"), request.form.get("key", "")
        if name not in ("answers", "editions", "conflicts") or not key:
            abort(400)
        marks.update(ctx.state_dir, name, {key: None})
        return redirect(url_for("review_physical"))

    return questions
