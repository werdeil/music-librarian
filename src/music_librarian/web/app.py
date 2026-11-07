"""The dashboard: its context, the overview and the header's activity, and the app put together
from the pages of history.py, albums.py and reviews.py, behind the checks of security.py.

Everything shown is read from the state folder and the Inbox on each request; the pages
themselves change nothing.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, url_for

from music_librarian import __version__
from music_librarian.core import runs
from music_librarian.importer import ERRORS, list_items
from music_librarian.lyrics import misses
from music_librarian.tag import genre_list
from music_librarian.web import albums, history, library, reviews, security
from music_librarian.web.common import place, texts, when
from music_librarian.web.history import PROBLEMS, shown, stoppable
from music_librarian.web.i18n import TRANSLATIONS, pick_lang
from music_librarian.web.passes import LONG_WHEN, OPTIONS, UNDER

HOME_PASSES = ("import", "organize", "enrich", "scan")
POLLED = {"static", "health", "built", "activity_fragment", "run_log", "album_cover", "request_state"}


@dataclass
class Context:
    """What the dashboard shows of the watch that serves it."""
    root: Path
    inbox: Path
    state_dir: Path
    apply: bool = False
    interval: float = 30
    organize_at: time | None = None
    enrich_at: tuple | None = None   # (weekday, time), as watch.weekly parses it
    requests: object = None          # web.passes.Requests; None shows no form
    hosts: frozenset = frozenset()   # the names it answers to besides security.local_host's


def next_daily(now, at):
    slot = datetime.combine(now.date(), at)
    return slot if slot > now else slot + timedelta(days=1)


def next_weekly(now, day, at):
    slot = datetime.combine(now.date() + timedelta(days=(day - now.weekday()) % 7), at)
    return slot if slot > now else slot + timedelta(days=7)


def inbox_state(inbox: Path):
    """(names of the Inbox items, names of the entries in _failed/), or None for a missing Inbox."""
    try:
        items = [it.name for it in list_items(inbox)]
    except OSError:
        return None
    failed = inbox / ERRORS
    return items, sorted(p.name for p in failed.iterdir()) if failed.is_dir() else []


def create_app(ctx: Context):
    app = Flask(__name__)
    token = security.register(app, ctx)
    waiting, queues = reviews.register(app, ctx)
    index = library.Index(ctx.root, ctx.state_dir)
    history.register(app, ctx)
    albums.register(app, ctx, index)
    built_in_background = (index.albums, *queues)
    credit_queue, genre_queue = queues
    shows = {"library_page": (index.albums,), "missed_lyrics": (index.albums,), "reviews_page": queues,
             "review_split": (credit_queue,), "review_dedupe": (credit_queue,),
             "review_genres": (genre_queue,), "genre_list_page": (genre_queue,)}

    @app.context_processor
    def language():
        lang = pick_lang(request.accept_languages)
        return dict(lang=lang, t=TRANSLATIONS[lang], ctx=ctx, version=__version__, csrf=token,
                    waiting_total=lambda: sum(n or 0 for n in waiting().values()), activity=activity(),
                    rebuilding=request.endpoint in shows and not settled(request.endpoint),
                    cover_url=cover_url, build_percent=lambda: build_percent(request.endpoint), place=place())

    def cover_url(rel):
        return url_for("album_cover", folder=rel, v=library.cover_version(ctx.root, rel))

    def settled(page=None):
        """Whether the lists `page` shows are built, every list without a page."""
        return all(value.ready() for value in shows.get(page, built_in_background))

    def build_percent(page=None):
        values = shows.get(page, built_in_background)
        return round(100 * sum(value.progress() for value in values) / len(values))

    @app.before_request
    def start_builds():
        if request.endpoint not in POLLED:
            for value in built_in_background:
                value.start()

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/")
    def home():
        t = texts()
        now = datetime.now()
        latest = {c: shown(r) for c, r in runs.latest(ctx.state_dir).items()}
        week_ago = (now - timedelta(days=7)).timestamp()
        problems = sum(1 for r in runs.recent(ctx.state_dir, runs.KEEP)
                       if r["started"] >= week_ago and runs.status(r) in PROBLEMS)
        schedule = [("import", t["every_seconds"].format(n=f"{ctx.interval:g}"), None)]
        if ctx.organize_at:
            schedule.append(("organize", t["daily_at"].format(at=f"{ctx.organize_at:%H:%M}"),
                             when(next_daily(now, ctx.organize_at).timestamp())))
        else:
            schedule.append(("organize", t["not_scheduled"], None))
        if ctx.enrich_at:
            day, at = ctx.enrich_at
            schedule.append(("enrich", t["weekly_at"].format(day=t["days"][day], at=f"{at:%H:%M}"),
                             when(next_weekly(now, day, at).timestamp())))
        else:
            schedule.append(("enrich", t["not_scheduled"], None))
        inbox = inbox_state(ctx.inbox)
        return render_template(
            "home.html", **to_do(inbox), latest=latest, schedule=schedule, problems=problems,
            inbox=inbox, waiting_items=inbox[0] if inbox else [],
            passes={command: OPTIONS[command] for command in HOME_PASSES}, long_when=LONG_WHEN, under=UNDER,
            days=f"{misses.retry_days():g}")

    def to_do(inbox):
        """What the To do tiles show, `inbox` as inbox_state() returns it."""
        everything = index.get()
        todo = dict(waiting(), failed=len(inbox[1]) if inbox else 0,
                    lyrics=None if everything is None else sum(a.without_lyrics for a in everything),
                    repeats=None if everything is None else sum(a.repeated for a in everything))
        return dict(todo=todo, genre_list=genre_list(ctx.state_dir, lambda _msg: None) is not None)

    @app.get("/tiles")
    def tiles_fragment():
        return render_template("_tiles.html", **to_do(inbox_state(ctx.inbox)))

    def activity():
        """The passes running now, and those the dashboard queued, for the header."""
        running = [dict(shown(r), stoppable=stoppable(ctx, r))
                   for r in runs.recent(ctx.state_dir, 20) if runs.status(r) == runs.RUNNING]
        return dict(running=running, queued=ctx.requests.pending() if ctx.requests else [])

    @app.get("/built")
    def built():
        page = request.args.get("page")
        return jsonify(ready=settled(page), percent=build_percent(page))

    @app.get("/activity")
    def activity_fragment():
        return render_template("_activity.html")

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("not_found.html"), 404

    return app
