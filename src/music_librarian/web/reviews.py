"""split --review and dedupe --review on the dashboard: the queues of the terminal reviews, one
question per page, each answer written at once to the decisions or the aliases file. And the
genres the validated list does not know, each added to it as a name of a validated genre or as a
genre of its own.

The queues are built from the metadata cache in the background, again whenever it changes, and
count as None until first built; whether an item still needs an answer is checked against the
files on each page, as the terminal review does, so an answer that settles other items skips
them. Like the terminal review, it takes no lock.
"""
import json
import threading
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from flask import abort, redirect, render_template, request, url_for

from music_librarian import dedupe, split
from music_librarian.dedupe import aliases as aliases_file
from music_librarian.dedupe.aliases import canonical
from music_librarian.dedupe.review import pending, suggestion
from music_librarian.dedupe.similar import find_groups
from music_librarian.enrich import genres
from music_librarian.lyrics.matching import fold_name
from music_librarian.media import credit_tags, metadata_cache
from music_librarian.split import decisions
from music_librarian.split.rules import (KEEP, PENDING, PROTECTED, SPLIT, decided_names, fragments, key,
                                         names_of, resolve, suggest)
from music_librarian.web.library import Refreshed, cache_stamp, cached_files


def quiet(_msg):
    pass


class Reviews:
    def __init__(self, root: Path, state_dir: Path):
        self.root, self.state_dir = root, state_dir
        self.decisions = state_dir / decisions.FILE
        self.aliases = state_dir / aliases_file.FILE
        self.lock = threading.Lock()
        self.queues = Refreshed(lambda: (cache_stamp(state_dir),), self._build)

    def _build(self):
        tracks = [(path, found) for path, snap in cached_files(self.root, self.state_dir)
                  if (found := credit_tags.from_snapshot(snap))]
        known = split.known_from([c for _, c in tracks])
        decided = decisions.load(self.decisions, quiet)
        _, waiting = split.plan(tracks, known | decided_names(decided), decided)
        asked = [(credit, n, str(p.relative_to(self.root)), status)
                 for status in (PENDING, PROTECTED)
                 for credit, n, p, status in split.by_frequency(waiting, status)]
        aliases, different = aliases_file.load(self.aliases, quiet)
        counts, mbids = dedupe.names_in(tracks, aliases)
        examples = {}
        for path, found in tracks:
            for name in {canonical(n, aliases) for f in dedupe.FIELDS for v in found[f] for n in names_of(v)}:
                examples.setdefault(name, str(path.parent.relative_to(self.root)))
        return SimpleNamespace(known=known, credits=asked, groups=find_groups(counts, mbids, different),
                               names={name: (n, examples.get(name)) for name, n in counts.items()})

    def credit(self, start=0):
        """The first credit from position `start` still awaiting an answer, as a dict for the
        page, or None; and the count of those left, None until the queue is first built."""
        queues = self.queues.get()
        if queues is None:
            return None, None
        with self.lock:
            decided = decisions.load(self.decisions, quiet)
            current = queues.known | decided_names(decided)
            left = [i for i, item in enumerate(queues.credits)
                    if resolve(item[0], current, decided)[1] in (PENDING, PROTECTED)]
            nxt = next((i for i in left if i >= start), None)
            if nxt is None:
                return None, len(left)
            credit, tracks, example, status = queues.credits[nxt]
            status = resolve(credit, current, decided)[1]
            parts = fragments(credit)
            return dict(position=nxt, total=len(queues.credits), credit=credit, tracks=tracks,
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
        settle yet, as a dict for the page, or None; and the count of those left, None until the
        queue is first built."""
        queues = self.queues.get()
        if queues is None:
            return None, None
        with self.lock:
            aliases, different = aliases_file.load(self.aliases, quiet)
            left = [(i, names) for i, (group, _) in enumerate(queues.groups)
                    if len(names := pending(group, aliases, different)) >= 2]
            nxt = next(((i, names) for i, names in left if i >= start), None)
            if nxt is None:
                return None, len(left)
            i, names = nxt
            reasons = queues.groups[i][1]
            return dict(position=i, total=len(queues.groups), names=names, reasons=sorted(reasons),
                        counts={n: queues.names.get(n, (0, None))[0] for n in names},
                        examples={n: queues.names.get(n, (0, None))[1] for n in names},
                        suggested=suggestion(names, reasons)), len(left)

    def write_aliases(self, add=(), drop=()):
        with self.lock:
            aliases_file.update(self.aliases, add=add, drop=drop)


class GenreReview:
    """The genres of the library the validated list does not know, the most tracks first."""

    def __init__(self, root: Path, state_dir: Path):
        self.root, self.state_dir = root, state_dir
        self.path = genres.list_path(state_dir)
        self.lookups = state_dir / genres.LOOKUP_CACHE
        self.lock = threading.Lock()
        self.queue = Refreshed(lambda: (cache_stamp(state_dir), self.mapping() is not None), self._build)

    def _build(self):
        mapping = self.mapping()
        found = defaultdict(lambda: dict(tracks=0, example=None, albums=Counter()))
        for path, snap in cached_files(self.root, self.state_dir):
            values = metadata_cache.genres(snap)
            if values:
                entry = found["; ".join(values)]
                entry["tracks"] += 1
                entry["values"] = values
                entry["example"] = entry["example"] or str(path.relative_to(self.root))
                entry["albums"][genres.album_of(snap)] += 1
        unknown = [kv for kv in found.items() if genres.resolve(kv[1]["values"], mapping or {}) is None]
        return sorted(unknown, key=lambda kv: (-kv[1]["tracks"], kv[0]))

    def mapping(self):
        """The validated list as genres.load reads it, or None without a readable one."""
        try:
            return genres.load(self.path) if self.path.exists() else None
        except genres.GenreListError:
            return None

    def apple(self, albums):
        """The genre iTunes gives most of these albums, from what enrich.genres looked up."""
        try:
            answers = json.loads(self.lookups.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        seen = Counter()
        for album, n in albums.items():
            hit = answers.get(f"{fold_name(album[0])}\t{fold_name(album[1])}") if album else None
            if hit and hit.get("genre"):
                seen[hit["genre"]] += n
        return seen.most_common(1)[0][0] if seen else None

    def item(self, start=0):
        """The first genre from position `start` the list still does not know, as a dict for the
        page, or None; and the count of those left, None until the queue is first built. None, 0
        without a list."""
        items = self.queue.get()
        with self.lock:
            mapping = self.mapping()
            if mapping is None:
                return None, 0
            if items is None:
                return None, None
            left = [i for i, (_, entry) in enumerate(items)
                    if genres.resolve(entry["values"], mapping) is None]
            nxt = next((i for i in left if i >= start), None)
            if nxt is None:
                return None, len(left)
            shown, entry = items[nxt]
            apple = self.apple(entry["albums"])
            return dict(position=nxt, total=len(items), genre=shown, tracks=entry["tracks"],
                        example=entry["example"], apple=apple,
                        suggested=genres.resolve([apple], mapping) if apple else None,
                        validated=sorted(set(mapping.values()), key=str.casefold)), len(left)

    def add(self, genre, alias=None):
        with self.lock:
            genres.add_to_list(self.path, genre, alias)

    def remove(self, genre, alias=None):
        with self.lock:
            genres.remove_from_list(self.path, genre, alias)


def register(app, ctx):
    """Add the review pages to the app; return the function that counts the questions awaiting
    an answer, {"split": n, "dedupe": n, "genres": n}, n None until its queue is first built."""
    reviews = Reviews(ctx.root, ctx.state_dir)
    genre_review = GenreReview(ctx.root, ctx.state_dir)

    def waiting():
        return dict(split=reviews.credit()[1], dedupe=reviews.group()[1], genres=genre_review.item()[1])

    @app.get("/reviews")
    def reviews_page():
        left = waiting()
        return render_template("reviews.html", credits=left["split"], groups=left["dedupe"],
                               genres=left["genres"], genre_list=genre_review.mapping() is not None,
                               list_path=genre_review.path)

    @app.get("/reviews/genres")
    def review_genres():
        item, left = genre_review.item(request.args.get("from", 0, type=int))
        return render_template("review_genres.html", item=item, left=left, done=done_of(request.args),
                               genre_list=genre_review.mapping() is not None, list_path=genre_review.path)

    @app.post("/reviews/genres")
    def answer_genre():
        mapping = genre_review.mapping()
        shown, choice = request.form.get("genre", "").strip(), request.form.get("choice")
        if mapping is None or not shown:
            abort(404)
        if choice == "alias":
            genre, alias = request.form.get("validated", ""), shown
            if genre not in mapping.values():
                abort(400)
        else:
            genre, alias = (request.form.get("name") or shown).strip(), None
            if mapping.get(genres.key(genre), genre) != genre:
                abort(400)
        genre_review.add(genre, alias)
        done = json.dumps(dict(genre=genre, alias=alias))
        return redirect(url_for("review_genres", **{"from": request.form.get("position", 0, type=int)},
                                done=done))

    @app.post("/reviews/genres/undo")
    def undo_genre():
        genre = request.form.get("genre", "")
        if not genre:
            abort(400)
        genre_review.remove(genre, request.form.get("alias") or None)
        return redirect(url_for("review_genres", **{"from": request.form.get("position", 0, type=int)}))

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

    return waiting


def done_of(args):
    """The answer just recorded, carried by the redirect for its undo button; None without one."""
    try:
        return json.loads(args["done"]) if "done" in args else None
    except ValueError:
        return None
