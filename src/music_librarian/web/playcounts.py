"""playcounts --review on the dashboard: Lyrion's orphan entries, each given a track of the library
or ignored on a page of its own, the answer written at once to the matches file.

The orphans are read from persist.db, opened read-only, and the library from the metadata cache,
in the background, again whenever either changes; what settles each orphan is read from the
matches file on each page. Only the pass watch runs writes to persist.db.
"""
import contextlib
import os
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

from flask import abort, g, redirect, render_template, request, url_for

from music_librarian import playcounts
from music_librarian.core.files import relative
from music_librarian.integrations import lyrion
from music_librarian.media import metadata_cache
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.playcounts import decisions, history
from music_librarian.playcounts.matching import LIKELY, POSSIBLE
from music_librarian.web.common import paged, when
from music_librarian.web.library import Refreshed, cache_stamp

PAGE = 50
SUGGESTIONS = 8
STATES = ("waiting", "settled", "ignored")


def changed_at(path: Path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def quality(note):
    return "likely" if note >= LIKELY else "possible" if note >= POSSIBLE else "unlikely"


class Orphans:
    def __init__(self, ctx):
        self.root = ctx.root
        self.opts = playcounts.Options(root=ctx.root, lyrion=ctx.lyrion, state_dir=ctx.state_dir)
        self.lock = threading.Lock()
        self.built = Refreshed(self._stamp, self._build)

    def _stamp(self):
        db = self.opts.lyrion.db
        return (cache_stamp(self.opts.state_dir), changed_at(db), changed_at(Path(f"{db}-wal")),
                changed_at(self.opts.state_dir / "journal"))

    def _library(self):
        """{urlmd5 Lyrion gives a track of the metadata cache still there: its path relative to the
        root}."""
        if not (self.opts.state_dir / metadata_cache.FILE).exists():
            return {}
        with MetadataCache(self.opts.state_dir) as cache:
            return {lyrion.md5(url): relative(path, self.root) for path in cache.paths(self.root)
                    if os.path.exists(path) and (url := self.opts.lyrion.url(Path(path)))}

    def _build(self):
        library = self._library()
        try:
            with contextlib.closing(self.opts.lyrion.connect()) as con:
                orphans = playcounts.orphans_of(self.opts, con, library)
                return SimpleNamespace(
                    error=None, orphans=orphans, by_key={orphan.key: orphan for orphan, _, _ in orphans},
                    why={orphan.key: why for orphan, _, why in orphans},
                    filed=set(library.values()), matcher=playcounts.matcher(con, library),
                    zeroed=len(history.zeroed(con)), dpsv=len(history.dpsv_changes(con)))
        except sqlite3.Error as e:
            return SimpleNamespace(error=str(e))

    def state(self):
        """(what was built, its orphans by what settles them as [(orphan, its track or None)] under
        each of STATES); None, None until first built, the second None when persist.db is
        unreadable."""
        built = self.built.get()
        if built is None or built.error:
            return built, None
        found = playcounts.sorted_out(built.orphans, self.opts.matches, built.filed)
        return built, dict(waiting=[(orphan, None) for orphan in found["waiting"]],
                           settled=found["journal"] + found["case"] + found["matched"] + found["gone"],
                           ignored=[(orphan, None) for orphan in found["ignored"]])

    def answer(self, **changes):
        with self.lock:
            decisions.update(self.opts.matches, **changes)

    def shown(self, orphan):
        return dict(key=orphan.key, path=self.opts.lyrion.relative(orphan.url), plays=orphan.plays,
                    stars=round(orphan.rating / 20),
                    last=when(orphan.last_played) if orphan.last_played else "")


def register(app, ctx):
    """Add the Lyrion pages to the app; return the function that counts the orphans awaiting an
    answer, None until first built or without Lyrion (counted once per request), and the Refreshed
    values the pages show."""
    orphans = Orphans(ctx) if ctx.lyrion else None

    def waiting():
        if "orphans_waiting" not in g:
            sorted_out = orphans.state()[1] if orphans else None
            g.orphans_waiting = len(sorted_out["waiting"]) if sorted_out else None
        return g.orphans_waiting

    def following(sorted_out, key):
        """The orphan awaiting an answer after the one of `key`, the first for none after it."""
        keys = [orphan.key for orphan, _ in sorted_out["waiting"]]
        after = keys[keys.index(key) + 1:] if key in keys else []
        return next(iter(after or [k for k in keys if k != key]), None)

    def recorded(built):
        """The orphan whose answer was just recorded, carried by the redirect for its undo button."""
        done = built.by_key.get(request.args.get("done", ""))
        return orphans.shown(done) if done else None

    @app.get("/lyrion")
    def lyrion_page():
        if orphans is None:
            return render_template("lyrion.html", off=True)
        built, sorted_out = orphans.state()
        if sorted_out is None:
            return render_template("lyrion.html", building=built is None, error=built and built.error)
        state = request.args.get("state")
        if state not in STATES:
            state = "settled" if sorted_out["settled"] and not sorted_out["waiting"] else "waiting"
        rows, page, pages = paged(sorted_out[state], PAGE)
        matched = decisions.load(orphans.opts.matches)[0]
        return render_template(
            "lyrion.html", state=state, counts={name: len(sorted_out[name]) for name in STATES},
            rows=[dict(orphans.shown(orphan), target=target,
                       gone=target is not None and target not in built.filed,
                       why=None if orphan.key in matched else built.why[orphan.key])
                  for orphan, target in rows],
            page=page, pages=pages, zeroed=built.zeroed, dpsv=built.dpsv, done=recorded(built))

    @app.get("/lyrion/orphans/<key>")
    def lyrion_orphan(key):
        if orphans is None:
            abort(404)
        built, sorted_out = orphans.state()
        if sorted_out is None:
            return render_template("lyrion_orphan.html", building=built is None, error=built and built.error)
        orphan = built.by_key.get(key)
        if orphan is None:
            abort(404)
        item = orphans.shown(orphan)
        state, target = next((name, track) for name in STATES for other, track in sorted_out[name]
                             if other.key == key)
        words = request.args.get("q", "").strip()
        found = built.matcher.best(item["path"], orphan.plays, SUGGESTIONS, words)
        suggestions = [dict(note=round(note), quality=quality(note), path=rel, plays=plays,
                            album=os.path.dirname(rel)) for note, _, rel, plays in found]
        return render_template(
            "lyrion_orphan.html", item=item, state=state, target=target, words=words,
            answered=key in decisions.load(orphans.opts.matches)[0] or state == "ignored",
            gone=target is not None and target not in built.filed, suggestions=suggestions,
            why=built.why[key],
            following=following(sorted_out, key), left=len(sorted_out["waiting"]),
            done=recorded(built))

    @app.post("/lyrion/orphans/<key>")
    def answer_orphan(key):
        if orphans is None:
            abort(404)
        built, sorted_out = orphans.state()
        if sorted_out is None or key not in built.by_key:
            abort(404)
        choice = request.form.get("choice")
        if choice == "forget":
            orphans.answer(drop=[key])
            return redirect(url_for("lyrion_orphan", key=key))
        if choice == "ignore":
            orphans.answer(ignore=[key])
        elif choice == "match":
            target = request.form.get("target", "")
            if target not in built.filed or not (ctx.root / target).is_file():
                abort(400)
            orphans.answer(match={key: target})
        else:
            abort(400)
        after = following(sorted_out, key)
        if after is None:
            return redirect(url_for("lyrion_page", done=key))
        return redirect(url_for("lyrion_orphan", key=after, done=key))

    return waiting, ((orphans.built,) if orphans else ())
