"""The dashboard: its context, the overview and the header's activity, and the app put together
from the pages of history.py, albums.py and reviews.py, behind the checks of security.py.

Everything shown is read from the state folder and the Inbox on each request; the pages
themselves change nothing.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import time as clock

from flask import Flask, g, jsonify, render_template, request, url_for

from music_librarian import __version__
from music_librarian.core import runs
from music_librarian.core.files import JUNK_NAMES
from music_librarian.integrations import itunes
from music_librarian.lyrics import enabled_providers
from music_librarian.tag import genre_list
from music_librarian.deezer import config as deezer_config, kept as deezer_kept
from music_librarian.physical import collection as physical_collection
from music_librarian.web import (albums, deezer, failed, history, library, playcounts, reviews, security,
                                 uploads)
from music_librarian.web.common import place, server_timing, texts, when
from music_librarian.web.history import shown, stoppable, to_check
from music_librarian.web.i18n import TRANSLATIONS, pick_lang
from music_librarian.web.passes import LONG_WHEN, OPTIONS

HOME_PASSES = ("import", "organize", "enrich", "scan")
POLLED = {"static", "health", "built", "activity_fragment", "run_log", "album_cover", "request_state"}


@dataclass
class Context:  # pylint: disable=too-many-instance-attributes  # one per setting of watch shown
    """What the dashboard shows of the watch that serves it."""
    root: Path
    inbox: Path
    state_dir: Path
    apply: bool = False
    interval: float = 30
    organize_at: time | None = None
    enrich_at: tuple | None = None   # (weekday, time), as watch.weekly parses it
    physical_at: tuple | None = None
    requests: object = None          # web.passes.Requests; None shows no form
    hosts: frozenset = frozenset()   # the names it answers to besides security.local_host's
    lyrion: object = None            # integrations.lyrion.Lyrion; None shows no Lyrion page


def next_daily(now, at):
    slot = datetime.combine(now.date(), at)
    return slot if slot > now else slot + timedelta(days=1)


def next_weekly(now, day, at):
    slot = datetime.combine(now.date() + timedelta(days=(day - now.weekday()) % 7), at)
    return slot if slot > now else slot + timedelta(days=7)


def inbox_state(inbox: Path):
    """The names of the entries in the Inbox's _failed/, or None for a missing Inbox."""
    return failed.entries(inbox) if inbox.is_dir() else None


def without_login(url):
    parts = urlsplit(url)
    return parts._replace(netloc=parts.netloc.rpartition("@")[2]).geturl()


def environment(ctx: Context):
    """What the overview shows of the setup watch runs with, beside its folders."""
    validated = genre_list(ctx.state_dir, lambda _msg: None)
    lookup = itunes.from_env()
    scan = ctx.lyrion.scan if ctx.lyrion else None
    return dict(providers=[name for name, _ in enabled_providers()],
                genres=None if validated is None else len(set(validated.values())),
                genre_lookup=lookup.country if lookup else None,
                lyrion_db=ctx.lyrion.db if ctx.lyrion else None,
                lyrion_url=without_login(scan.url) if scan else None,
                lyrion_rescans=bool(ctx.lyrion and ctx.lyrion.rescans), deezer=deezer_state(ctx))


def deezer_state(ctx: Context):
    """What the overview shows of the Deezer download: None while its keys are not set, else where
    its arl comes from ("kept", "env" or None), whether Deezer refused it, and whether the extension
    has a key."""
    if ctx.requests is None or not deezer_config.keys_set():
        return None
    current = deezer_config.arl(state_dir=ctx.state_dir)
    source = "kept" if deezer_kept.arl(ctx.state_dir) else "env" if current else None
    return dict(arl=source, refused=bool(current) and deezer_kept.refused(ctx.state_dir, current) is not None,
                extension=bool(deezer_kept.extension_key(ctx.state_dir)))


def create_app(ctx: Context, build_now=False):
    """The dashboard's app; with `build_now`, its lists start building at once, else on the
    first request."""
    app = Flask(__name__)
    token = security.register(app, ctx)
    index = library.Index(ctx.root, ctx.state_dir)
    later = reviews.Postponed(ctx.state_dir)
    waiting, queues = reviews.register(app, ctx, index, later)
    history.register(app, ctx)
    albums.register(app, ctx, index, later)
    uploads.register(app, ctx)
    failed.register(app, ctx)
    deezer.register(app, ctx)
    deezer.register_extension(app, ctx)
    orphans_waiting, orphans = playcounts.register(app, ctx)
    built_in_background = (index.albums, *queues, *orphans)
    credit_queue, genre_queue = queues
    shows = {"library_page": (index.albums,), "missed_lyrics": (index.albums,),
             "reviews_page": (*queues, index.albums),
             "review_split": (credit_queue,), "review_dedupe": (credit_queue,),
             "review_genres": (genre_queue,), "genre_list_page": (genre_queue,),
             "lyrion_page": orphans, "lyrion_orphan": orphans}

    @app.context_processor
    def language():
        lang = pick_lang(request.accept_languages)
        return dict(lang=lang, t=TRANSLATIONS[lang], ctx=ctx, version=__version__, csrf=token,
                    waiting_total=lambda: sum(n or 0 for n in waiting().values()), activity=activity(),
                    orphans_waiting=orphans_waiting,
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

    def start_builds():
        for value in built_in_background:
            value.start()

    @app.before_request
    def start_timing():
        g.started = clock.perf_counter()

    @app.after_request
    def timing_header(response):
        if "started" in g:
            response.headers["Server-Timing"] = server_timing(g.started)
        return response

    @app.before_request
    def start_builds_on_request():
        if request.endpoint not in POLLED:
            start_builds()

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
                       if r["started"] >= week_ago and to_check(r))
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
        if ctx.physical_at:
            day, at = ctx.physical_at
            schedule.append(("physical", t["weekly_at"].format(day=t["days"][day], at=f"{at:%H:%M}"),
                             when(next_weekly(now, day, at).timestamp())))
        inbox = inbox_state(ctx.inbox)
        return render_template(
            "home.html", **to_do(inbox), latest=latest, schedule=schedule, problems=problems,
            inbox=inbox,
            passes={command: OPTIONS[command] for command in HOME_PASSES}, long_when=LONG_WHEN,
            env=environment(ctx), deezer_keys=deezer_config.keys_set(),
            deezer_ready=deezer_config.enabled(state_dir=ctx.state_dir),
            upload=dict(extensions=sorted(uploads.SENT_EXT), junk=sorted(JUNK_NAMES),
                        max_file=uploads.MAX_FILE))

    def to_do(inbox):
        """What the Checks chips show, `inbox` as inbox_state() returns it."""
        everything = index.get()
        downloads = deezer_state(ctx)
        todo = dict(waiting(), failed=len(inbox or ()), orphans=orphans_waiting(),
                    repeats=None if everything is None else sum(a.repeated for a in everything),
                    deezer_arl=int(bool(downloads and (downloads["refused"] or not downloads["arl"]))))
        bare = library.without_lyrics(everything, later.times("lyrics")) if everything else []
        return dict(todo=todo, switched_off=set(reviews.KINDS) - set(todo),
                    physical_kept=physical_collection.kept(ctx.state_dir) is not None,
                    deezer_arl_missing=bool(downloads and not downloads["arl"]),
                    genre_list=genre_list(ctx.state_dir, lambda _msg: None) is not None,
                    first_bare=bare[0].folder if bare else None,
                    first_mixed=next((a.folder for a in library.with_mixed(everything or [])), None),
                    first_dated=next((a.folder for a in library.with_dates(everything or [])), None))

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

    if build_now:
        start_builds()
    return app
