"""split --review and dedupe --review on the dashboard: the queues of the terminal reviews, one
question per page, each answer written at once to the decisions or the aliases file. And the
genres the validated list does not know, each added to it as a name of a validated genre or as a
genre of its own. And the tracks without lyrics, each answered as instrumental, recorded in
instrumentals.txt, or left for later.

A question put off with Later goes after those never put off, the longest put off first, kept in
review-later.json in the state folder.

The queues are built from the metadata cache in the background, again whenever it changes, and
count as None until first built; whether an item still needs an answer is checked against the
files on each page, as the terminal review does, so an answer that settles other items skips
them. Like the terminal review, it takes no lock.
"""
import json
import os
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from flask import abort, g, redirect, render_template, request, url_for

from music_librarian import dedupe, split
from music_librarian.dedupe import aliases as aliases_file
from music_librarian.dedupe.aliases import canonical
from music_librarian.dedupe.review import pending, suggestion
from music_librarian.dedupe.similar import find_groups
from music_librarian.enrich import genres
from music_librarian.lyrics import instrumentals
from music_librarian.lyrics.matching import fold_name
from music_librarian.media import credit_tags, metadata_cache
from music_librarian.split import decisions
from music_librarian.split.rules import (KEEP, PENDING, PROTECTED, SPLIT, decided_names, fragments, key,
                                         names_of, resolve, suggest)
from music_librarian.web import library
from music_librarian.web.library import Refreshed, cache_stamp, cached_files, part


LATER = "review-later.json"
REVIEWS = ("split", "dedupe", "genres", "instrumental")


def quiet(_msg):
    pass


class Postponed:
    """The questions put off with Later: {review: {question's key: when}}."""

    def __init__(self, state_dir: Path):
        self.path = state_dir / LATER
        self.lock = threading.Lock()

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def put(self, review, asked):
        with self.lock:
            data = self._read()
            data.setdefault(review, {})[asked] = time.time()
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)

    def pick(self, review, left, key_of, start, total):
        """Of `left`, the positions still awaiting an answer, the one `start` places after the first
        once those put off are moved to the end, and its number among `total` questions; None, None
        past the end."""
        times = self._read().get(review, {})
        ordered = sorted(left, key=lambda i: times.get(key_of(i), 0.0))
        if start >= len(ordered):
            return None, None
        return ordered[start], total - len(left) + start + 1


def dedupe_key(names):
    return json.dumps(sorted(names), ensure_ascii=False)


class Reviews:
    def __init__(self, root: Path, state_dir: Path, later: Postponed):
        self.root, self.state_dir, self.later = root, state_dir, later
        self.decisions = state_dir / decisions.FILE
        self.aliases = state_dir / aliases_file.FILE
        self.lock = threading.Lock()
        self.queues = Refreshed(lambda: (cache_stamp(state_dir),), self._build)

    def _build(self):
        with part(0, 0.3):
            tracks = [(path, found) for path, snap in cached_files(self.root, self.state_dir)
                      if (found := credit_tags.from_snapshot(snap))]
        with part(0.3, 0.45):
            known = split.known_from([c for _, c in tracks])
            decided = decisions.load(self.decisions, quiet)
            _, waiting = split.plan(tracks, known | decided_names(decided), decided)
            asked = [(credit, n, str(p.relative_to(self.root)), status)
                     for status in (PENDING, PROTECTED)
                     for credit, n, p, status in split.by_frequency(waiting, status)]
        with part(0.45, 0.55):
            aliases, different = aliases_file.load(self.aliases, quiet)
            counts, mbids = dedupe.names_in(tracks, aliases)
            examples = {}
            for path, found in tracks:
                names = {canonical(n, aliases) for f in dedupe.FIELDS for v in found[f] for n in names_of(v)}
                if not names <= examples.keys():
                    folder = str(path.parent.relative_to(self.root))
                    for name in names:
                        examples.setdefault(name, folder)
        return SimpleNamespace(known=known, credits=asked, groups=find_groups(counts, mbids, different),
                               names={name: (n, examples.get(name)) for name, n in counts.items()})

    def credit(self, start=0):
        """The credit `start` places after the first still awaiting an answer, as a dict for the
        page, or None; and the count of those left, None until the queue is first built."""
        queues = self.queues.get()
        if queues is None:
            return None, None
        with self.lock:
            decided = decisions.load(self.decisions, quiet)
            current = queues.known | decided_names(decided)
            left = [i for i, item in enumerate(queues.credits)
                    if resolve(item[0], current, decided)[1] in (PENDING, PROTECTED)]
            nxt, number = self.later.pick("split", left, lambda i: key(queues.credits[i][0]), start,
                                          len(queues.credits))
            if nxt is None:
                return None, len(left)
            credit, tracks, example, status = queues.credits[nxt]
            status = resolve(credit, current, decided)[1]
            parts = fragments(credit)
            return dict(number=number, total=len(queues.credits), later=key(credit), credit=credit,
                        tracks=tracks, example=example, protected=status == PROTECTED, parts=parts,
                        known=[p for p in parts if key(p) in current],
                        suggested=KEEP if status == PROTECTED else suggest(credit, current)), len(left)

    def decide(self, credit, decision):
        """Write the decision; return the one it replaced, for an undo."""
        with self.lock:
            before = decisions.load(self.decisions, quiet).get(key(credit))
            decisions.record(self.decisions, credit, decision)
            return before

    def group(self, start=0):
        """The group of likely duplicates `start` places after the first the aliases file does not
        settle yet, as a dict for the page, or None; and the count of those left, None until the
        queue is first built."""
        queues = self.queues.get()
        if queues is None:
            return None, None
        with self.lock:
            aliases, different = aliases_file.load(self.aliases, quiet)
            left = {i: names for i, (group, _) in enumerate(queues.groups)
                    if len(names := pending(group, aliases, different)) >= 2}
            i, number = self.later.pick("dedupe", list(left), lambda i: dedupe_key(left[i]), start,
                                        len(queues.groups))
            if i is None:
                return None, len(left)
            names = left[i]
            reasons = queues.groups[i][1]
            return dict(number=number, total=len(queues.groups), later=dedupe_key(names), names=names,
                        reasons=sorted(reasons),
                        counts={n: queues.names.get(n, (0, None))[0] for n in names},
                        examples={n: queues.names.get(n, (0, None))[1] for n in names},
                        suggested=suggestion(names, reasons)), len(left)

    def write_aliases(self, add=(), drop=()):
        with self.lock:
            aliases_file.update(self.aliases, add=add, drop=drop)


class GenreReview:
    """The genres of the library the validated list does not know, the most tracks first."""

    def __init__(self, root: Path, state_dir: Path, later: Postponed):
        self.root, self.state_dir, self.later = root, state_dir, later
        self.path = genres.list_path(state_dir)
        self.lookups = state_dir / genres.LOOKUP_CACHE
        self.lock = threading.Lock()
        self.queue = Refreshed(lambda: (cache_stamp(state_dir), self.mapping() is not None), self._build)

    def _build(self):
        mapping = self.mapping()
        found = defaultdict(lambda: dict(tracks=0, example=None, albums=Counter()))
        with part(0, 0.9):
            files = cached_files(self.root, self.state_dir)
        for path, snap in files:
            values = metadata_cache.genres(snap)
            if values:
                entry = found["; ".join(values)]
                entry["tracks"] += 1
                entry["values"] = values
                entry["example"] = entry["example"] or str(path.relative_to(self.root))
                entry["albums"][genres.album_of(snap)] += 1
        unknown = [kv for kv in found.items() if genres.resolve(kv[1]["values"], mapping or {}) is None]
        return SimpleNamespace(unknown=sorted(unknown, key=lambda kv: (-kv[1]["tracks"], kv[0])),
                               tags=[(entry["values"], entry["tracks"]) for entry in found.values()])

    def tracks_per_genre(self, mapping):
        """{validated genre: tracks whose genre the list aligns to it}, None until first built."""
        built = self.queue.get()
        if built is None:
            return None
        out = Counter()
        for values, tracks in built.tags:
            validated = genres.resolve(values, mapping)
            if validated:
                out[validated] += tracks
        return out

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
        """The genre `start` places after the first the list still does not know, as a dict for the
        page, or None; and the count of those left, None until the queue is first built. None, 0
        without a list."""
        built = self.queue.get()
        items = None if built is None else built.unknown
        with self.lock:
            mapping = self.mapping()
            if mapping is None:
                return None, 0
            if items is None:
                return None, None
            left = [i for i, (_, entry) in enumerate(items)
                    if genres.resolve(entry["values"], mapping) is None]
            nxt, number = self.later.pick("genres", left, lambda i: items[i][0], start, len(items))
            if nxt is None:
                return None, len(left)
            shown, entry = items[nxt]
            apple = self.apple(entry["albums"])
            return dict(number=number, total=len(items), later=shown, genre=shown, tracks=entry["tracks"],
                        example=entry["example"], apple=apple,
                        suggested=genres.resolve([apple], mapping) if apple else None,
                        validated=sorted(set(mapping.values()), key=str.casefold)), len(left)

    def add(self, genre, alias=None):
        with self.lock:
            genres.add_to_list(self.path, genre, alias)

    def remove(self, genre, alias=None):
        with self.lock:
            genres.remove_from_list(self.path, genre, alias)


class InstrumentalReview:
    """The tracks of the metadata cache without lyrics, in the order of their paths."""

    def __init__(self, root: Path, state_dir: Path, later: Postponed):
        self.root, self.state_dir, self.later = root, state_dir, later
        self.lock = threading.Lock()
        self.queue = Refreshed(lambda: (cache_stamp(state_dir),), self._build)

    def _build(self):
        with part(0, 0.6):
            files = cached_files(self.root, self.state_dir)
        bare = [(str(path.relative_to(self.root)), metadata_cache.metadata(snap))
                for path, snap in files if not snap.get("lyrics")]
        return sorted(bare, key=lambda item: item[0])

    def item(self, start=0):
        """The track `start` places after the first not answered as instrumental, as a dict for the
        page, or None; and the count of those left, None until the queue is first built."""
        items = self.queue.get()
        if items is None:
            return None, None
        with self.lock:
            answered = set(instrumentals.load(self.state_dir))
        left = [i for i, (rel, _) in enumerate(items) if rel not in answered]
        nxt, number = self.later.pick("instrumental", left, lambda i: items[i][0], start, len(items))
        if nxt is None:
            return None, len(left)
        rel, meta = items[nxt]
        shown = {k: meta.get(k) for k in ("title", "artist", "album")} if meta else {}
        return dict(number=number, total=len(items), later=rel, path=rel, **shown), len(left)

    def answer(self, rel, instrumental):
        with self.lock:
            (instrumentals.add if instrumental else instrumentals.remove)(self.state_dir, rel)


def register(app, ctx):
    """Add the review pages to the app; return the function that counts the questions awaiting
    an answer, {"split": n, "dedupe": n, "genres": n}, n None until its queue is first built
    (counted once per request), and the queues, Refreshed values."""
    later = Postponed(ctx.state_dir)
    reviews = Reviews(ctx.root, ctx.state_dir, later)
    genre_review = GenreReview(ctx.root, ctx.state_dir, later)
    instrumental_review = InstrumentalReview(ctx.root, ctx.state_dir, later)

    def waiting():
        if "waiting" not in g:
            g.waiting = dict(split=reviews.credit()[1], dedupe=reviews.group()[1],
                             genres=genre_review.item()[1])
        return g.waiting

    @app.get("/reviews")
    def reviews_page():
        left = waiting()
        return render_template("reviews.html", credits=left["split"], groups=left["dedupe"],
                               genres=left["genres"], genre_list=genre_review.mapping() is not None,
                               bare=instrumental_review.item()[1],
                               answered=len(instrumentals.load(ctx.state_dir)),
                               list_path=genre_review.path,
                               rules=dict(decisions=len(decisions.entries(reviews.decisions)),
                                          aliases=len(aliases_file.entries(reviews.aliases)),
                                          genres=len(genres.entries(genre_review.path))))

    @app.post("/reviews/<review>/later")
    def review_later(review):
        asked = request.form.get("later", "")
        if review not in REVIEWS or not asked:
            abort(404)
        later.put(review, asked)
        return redirect(url_for(f"review_{review}"))

    @app.get("/reviews/genres/list")
    def genre_list_page():
        mapping = genre_review.mapping()
        return render_template("rules.html", kind="genres", path=genre_review.path,
                               genres=genres.entries(genre_review.path) if mapping is not None else None,
                               tracks=genre_review.tracks_per_genre(mapping) if mapping is not None else None)

    @app.get("/reviews/dedupe/aliases")
    def aliases_page():
        entries = aliases_file.entries(reviews.aliases)
        return render_template("rules.html", kind="aliases", path=reviews.aliases,
                               unified=[(a, b) for a, kind, b in entries if kind == "=>"],
                               different=[(a, b) for a, kind, b in entries if kind == "!="])

    @app.get("/reviews/split/decisions")
    def decisions_page():
        return render_template("rules.html", kind="decisions", path=reviews.decisions,
                               decided=decisions.entries(reviews.decisions))

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
        return redirect(url_for("review_genres", done=done))

    @app.post("/reviews/genres/undo")
    def undo_genre():
        genre = request.form.get("genre", "")
        if not genre:
            abort(400)
        genre_review.remove(genre, request.form.get("alias") or None)
        return redirect(url_for("review_genres"))

    @app.get("/reviews/instrumental")
    def review_instrumental():
        item, left = instrumental_review.item(request.args.get("from", 0, type=int))
        return render_template("review_instrumental.html", item=item, left=left, done=done_of(request.args))

    @app.post("/reviews/instrumental")
    def answer_instrumental():
        rel = request.form.get("path", "")
        if library.track_file(ctx.root, str(Path(rel).parent), Path(rel).name) is None:
            abort(404)
        instrumental_review.answer(rel, True)
        return redirect(url_for("review_instrumental", done=json.dumps(dict(path=rel))))

    @app.post("/reviews/instrumental/undo")
    def undo_instrumental():
        instrumental_review.answer(request.form.get("path", ""), False)
        return redirect(url_for("review_instrumental"))

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
        return redirect(url_for("review_split", done=json.dumps(done)))

    @app.post("/reviews/split/undo")
    def undo_split():
        credit = request.form.get("credit", "")
        if not credit:
            abort(400)
        reviews.decide(credit, request.form.get("before") or None)
        return redirect(url_for("review_split"))

    @app.get("/reviews/dedupe")
    def review_dedupe():
        item, left = reviews.group(request.args.get("from", 0, type=int))
        return render_template("review_dedupe.html", item=item, left=left, done=done_of(request.args))

    @app.post("/reviews/dedupe")
    def answer_dedupe():
        item, _ = reviews.group()
        try:
            shown = json.loads(request.form.get("names", ""))
        except ValueError:
            abort(400)
        if item is None or item["names"] != shown:
            return redirect(url_for("review_dedupe"))
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
        return redirect(url_for("review_dedupe", done=done))

    @app.post("/reviews/dedupe/undo")
    def undo_dedupe():
        try:
            entries = [tuple(e) for e in json.loads(request.form.get("entries", ""))]
        except (ValueError, TypeError):
            abort(400)
        reviews.write_aliases(drop=[aliases_file.identity(e) for e in entries])
        return redirect(url_for("review_dedupe"))

    return waiting, (reviews.queues, genre_review.queue, instrumental_review.queue)


def done_of(args):
    """The answer just recorded, carried by the redirect for its undo button; None without one."""
    try:
        return json.loads(args["done"]) if "done" in args else None
    except ValueError:
        return None
