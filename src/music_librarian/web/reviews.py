"""split --review and dedupe --review on the dashboard: the queues of the terminal reviews, one
question per page, each answer written at once to the decisions or the aliases file.

The queues are built from the metadata cache, again whenever it changes; whether an item still
needs an answer is checked against the files on each page, as the terminal review does, so an
answer that settles other items skips them. Like the terminal review, it takes no lock.
"""
import json
import threading
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

from music_librarian import dedupe, split
from music_librarian.dedupe import aliases as aliases_file
from music_librarian.dedupe.review import pending, suggestion
from music_librarian.dedupe.similar import find_groups
from music_librarian.media import credit_tags
from music_librarian.split import decisions
from music_librarian.split.rules import (KEEP, PENDING, PROTECTED, SPLIT, decided_names, fragments, key,
                                         names_of, resolve, suggest)
from music_librarian.web.library import cache_stamp, cached_files


def quiet(_msg):
    pass


class Reviews:
    def __init__(self, root: Path, state_dir: Path):
        self.root, self.state_dir = root, state_dir
        self.decisions = state_dir / decisions.FILE
        self.aliases = state_dir / aliases_file.FILE
        self.lock = threading.Lock()
        self.stamp, self.known, self.credits, self.groups, self.counts = None, set(), [], [], {}

    def _load(self):
        stamp = cache_stamp(self.state_dir)
        if stamp == self.stamp:
            return
        tracks = [(path, found) for path, snap in cached_files(self.root, self.state_dir)
                  if (found := credit_tags.from_snapshot(snap))]
        self.known = split.known_from([c for _, c in tracks])
        decided = decisions.load(self.decisions, quiet)
        _, waiting = split.plan(tracks, self.known | decided_names(decided), decided)
        self.credits = [(credit, n, str(p.relative_to(self.root)), status)
                        for status in (PENDING, PROTECTED)
                        for credit, n, p, status in split.by_frequency(waiting, status)]
        aliases, different = aliases_file.load(self.aliases, quiet)
        self.counts, mbids = dedupe.names_in(tracks, aliases)
        self.groups = find_groups(self.counts, mbids, different)
        self.stamp = stamp

    def credit(self, start=0):
        """The first credit from position `start` still awaiting an answer, as a dict for the
        page, or None; and the count of those left."""
        with self.lock:
            self._load()
            decided = decisions.load(self.decisions, quiet)
            current = self.known | decided_names(decided)
            left = [i for i, item in enumerate(self.credits)
                    if resolve(item[0], current, decided)[1] in (PENDING, PROTECTED)]
            nxt = next((i for i in left if i >= start), None)
            if nxt is None:
                return None, len(left)
            credit, tracks, example, status = self.credits[nxt]
            status = resolve(credit, current, decided)[1]
            parts = fragments(credit)
            return dict(position=nxt, total=len(self.credits), credit=credit, tracks=tracks,
                        example=example, protected=status == PROTECTED, parts=parts,
                        known=[p for p in parts if key(p) in current],
                        suggested=KEEP if status == PROTECTED else suggest(credit, current)), len(left)

    def decide(self, credit, decision):
        """Write the decision; return the one it replaced, for an undo."""
        with self.lock:
            before = decisions.load(self.decisions, quiet).get(key(credit))
            decisions.record(self.decisions, credit, decision)
            return before

    def group(self, start=0):
        """The first group of likely duplicates from position `start` the aliases file does not
        settle yet, as a dict for the page, or None; and the count of those left."""
        with self.lock:
            self._load()
            aliases, different = aliases_file.load(self.aliases, quiet)
            left = [(i, names) for i, (group, _) in enumerate(self.groups)
                    if len(names := pending(group, aliases, different)) >= 2]
            nxt = next(((i, names) for i, names in left if i >= start), None)
            if nxt is None:
                return None, len(left)
            i, names = nxt
            reasons = self.groups[i][1]
            return dict(position=i, total=len(self.groups), names=names, reasons=sorted(reasons),
                        counts={n: self.counts.get(n, 0) for n in names},
                        suggested=suggestion(names, reasons)), len(left)

    def write_aliases(self, add=(), drop=()):
        with self.lock:
            aliases_file.update(self.aliases, add=add, drop=drop)


def register(app, ctx):
    reviews = Reviews(ctx.root, ctx.state_dir)

    @app.get("/reviews")
    def reviews_page():
        _, waiting = reviews.credit()
        _, groups = reviews.group()
        return render_template("reviews.html", credits=waiting, groups=groups)

    @app.get("/reviews/split")
    def review_split():
        item, left = reviews.credit(request.args.get("from", 0, type=int))
        return render_template("review_split.html", item=item, left=left, done=done_of(request.args))

    @app.post("/reviews/split")
    def answer_split():
        credit, choice = request.form.get("credit", ""), request.form.get("choice", "")
        if choice == "names":
            decision = "; ".join(names_of(request.form.get("names", ""))) or None
        else:
            decision = {"split": SPLIT, "keep": KEEP}.get(choice)
        if not credit or decision is None:
            abort(400)
        before = reviews.decide(credit, decision)
        done = dict(credit=credit, decision=decision, before=before)
        return redirect(url_for("review_split", **{"from": request.form.get("position", 0, type=int)},
                                done=json.dumps(done)))

    @app.post("/reviews/split/undo")
    def undo_split():
        credit = request.form.get("credit", "")
        if not credit:
            abort(400)
        reviews.decide(credit, request.form.get("before") or None)
        return redirect(url_for("review_split", **{"from": request.form.get("position", 0, type=int)}))

    @app.get("/reviews/dedupe")
    def review_dedupe():
        item, left = reviews.group(request.args.get("from", 0, type=int))
        return render_template("review_dedupe.html", item=item, left=left, done=done_of(request.args))

    @app.post("/reviews/dedupe")
    def answer_dedupe():
        position = request.form.get("position", 0, type=int)
        item, _ = reviews.group(position)
        try:
            shown = json.loads(request.form.get("names", ""))
        except ValueError:
            abort(400)
        if item is None or item["names"] != shown:
            return redirect(url_for("review_dedupe", **{"from": position}))
        names = item["names"]
        if request.form.get("choice") == "different":
            entries = [(a, "!=", b) for i, a in enumerate(names) for b in names[i + 1:]]
        else:
            target = (request.form.get("other") or "").strip() or request.form.get("target", "")
            if not target:
                abort(400)
            entries = [(n, "=>", target) for n in names if n != target]
        reviews.write_aliases(add=entries)
        done = json.dumps(dict(entries=entries))
        return redirect(url_for("review_dedupe", **{"from": position}, done=done))

    @app.post("/reviews/dedupe/undo")
    def undo_dedupe():
        try:
            entries = [tuple(e) for e in json.loads(request.form.get("entries", ""))]
        except (ValueError, TypeError):
            abort(400)
        reviews.write_aliases(drop=[aliases_file.identity(e) for e in entries])
        return redirect(url_for("review_dedupe", **{"from": request.form.get("position", 0, type=int)}))


def done_of(args):
    """The answer just recorded, carried by the redirect for its undo button; None without one."""
    try:
        return json.loads(args["done"]) if "done" in args else None
    except ValueError:
        return None
