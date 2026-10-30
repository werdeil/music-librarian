"""The dashboard's pages: the recent runs, their output, the schedule and the Inbox, the
library's albums, and the forms that queue a pass for watch to run (the reviews of split and
dedupe are in reviews.py).

Everything shown is read from the state folder and the Inbox on each request; the pages
themselves change nothing.
"""
import hmac
import json
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, url_for)

from music_librarian import __version__
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED
from music_librarian.importer import ERRORS, list_items
from music_librarian.lyrics import misses
from music_librarian.media import fields
from music_librarian.tag import genre_list
from music_librarian.web import library, reviews
from music_librarian.web.i18n import TRANSLATIONS, pick_lang
from music_librarian.web.passes import DONE, FORMS, OPTIONS, journal_path

ERROR_TAGS = {"ERROR", "FAILED", "CONFLICT"}
WARNING_TAGS = {"WARNING", "SKIPPED", "REFUSED", "UNKNOWN", "GUARDED", "REVIEW", "STOP", "LOCKED"}
PROBLEMS = {runs.INTERRUPTED, runs.ISSUES, runs.STOPPED, runs.FAILED}
HOME_PASSES = ("import", "organize", "enrich", "scan")


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


def kinds(lines, kind=""):
    """The style of each log line, from its 8-character tag: "error", "warning", "section" or "".
    An indented line continues the previous one and takes its kind, unless that is a section."""
    out = []
    for line in lines:
        if not line.strip() or line[0].isspace():
            kind = kind if kind in ("error", "warning") else ""
        elif line.startswith(("---", "===", "[")):
            kind = "section"
        else:
            tag = line[:8].strip()
            kind = "error" if tag in ERROR_TAGS else "warning" if tag in WARNING_TAGS else ""
        out.append(kind)
    return out


def next_daily(now, at):
    slot = datetime.combine(now.date(), at)
    return slot if slot > now else slot + timedelta(days=1)


def next_weekly(now, day, at):
    slot = datetime.combine(now.date() + timedelta(days=(day - now.weekday()) % 7), at)
    return slot if slot > now else slot + timedelta(days=7)


def duration(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds} s"


def when(timestamp):
    return f"{datetime.fromtimestamp(timestamp):%Y-%m-%d %H:%M}"


def shown(run):
    """The run as the templates show it: status, start and duration formatted."""
    status = runs.status(run)
    end = run["ended"] if run["ended"] is not None else datetime.now().timestamp()
    return dict(run, status=status, when=when(run["started"]),
                duration=None if status == runs.INTERRUPTED else duration(end - run["started"]))


def inbox_state(inbox: Path):
    """(names of the Inbox items, names of the entries in _failed/), or None for a missing Inbox."""
    try:
        items = [it.name for it in list_items(inbox)]
    except OSError:
        return None
    failed = inbox / ERRORS
    return items, sorted(p.name for p in failed.iterdir()) if failed.is_dir() else []


def journal_of(run):
    """The file name of the journal a run's summary names, when it is still in the state folder."""
    for entry in run["summary"]:
        key, _, value = entry.partition(": ")
        if key == "journal":
            return Path(value).name
    return None


def create_app(ctx: Context):
    app = Flask(__name__)
    token = secrets.token_urlsafe(16)
    waiting = reviews.register(app, ctx)

    @app.context_processor
    def language():
        lang = pick_lang(request.accept_languages)
        return dict(lang=lang, t=TRANSLATIONS[lang], ctx=ctx, version=__version__, csrf=token,
                    waiting_total=sum(n or 0 for n in waiting().values()), activity=activity())

    @app.before_request
    def same_origin_forms():
        if request.method == "POST" and not hmac.compare_digest(request.form.get("csrf", ""), token):
            abort(403)

    def submit(command, options, apply):
        if ctx.requests is None or command not in OPTIONS or (apply and not ctx.apply):
            abort(403)
        return redirect(url_for("request_page", request_id=ctx.requests.submit(command, options, apply).id))

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/")
    def home():
        t = TRANSLATIONS[pick_lang(request.accept_languages)]
        now = datetime.now()
        latest = {c: shown(r) for c, r in runs.latest(ctx.state_dir).items()}
        week_ago = (now - timedelta(days=7)).timestamp()
        problems = sum(1 for r in runs.recent(ctx.state_dir, runs.KEEP)
                       if r["started"] >= week_ago and runs.status(r) in PROBLEMS)
        items, failed = inbox_state(ctx.inbox) or ([], [])
        todo = dict(waiting(), failed=len(failed), lyrics=sum(a.without_lyrics for a in index.get()))
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
        return render_template(
            "home.html", latest=latest, schedule=schedule, problems=problems, todo=todo,
            inbox=inbox_state(ctx.inbox), waiting_items=items,
            passes={command: OPTIONS[command] for command in HOME_PASSES})

    def activity():
        """The passes running now, and those the dashboard queued, for the header."""
        running = [shown(r) for r in runs.recent(ctx.state_dir, 20) if runs.status(r) == runs.RUNNING]
        return dict(running=running, queued=ctx.requests.pending() if ctx.requests else [])

    @app.get("/activity")
    def activity_fragment():
        return render_template("_activity.html")

    @app.post("/passes")
    def new_pass():
        command = request.form.get("command", "")
        if command not in FORMS and command != "undo":
            abort(403)
        names = OPTIONS[command]
        options = {name: True for name in names if name != "journal" and request.form.get(name)}
        if "journal" in names:
            options["journal"] = request.form.get("journal", "")
            if journal_path(ctx.state_dir, options["journal"]) is None:
                abort(404)
        return submit(command, options, request.form.get("apply") == "1")

    @app.post("/runs/<int:run_id>/apply")
    def apply_run(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None or run["options"] is None or run["apply"] or runs.status(run) == runs.RUNNING:
            abort(404)
        return submit(run["command"], run["options"], True)

    @app.get("/requests/<int:request_id>")
    def request_page(request_id):
        asked = ctx.requests.get(request_id) if ctx.requests else None
        if asked is None:
            abort(404)
        if asked.state == DONE and asked.code == EXIT_LOCKED:
            return render_template("request.html", asked=asked, locked=True)
        if asked.run_id is not None:
            return redirect(url_for("run_page", run_id=asked.run_id))
        return render_template("request.html", asked=asked, locked=False)

    @app.get("/runs")
    def history():
        command = request.args.get("command") or None
        problems = request.args.get("problems") == "1"
        shown_runs = [shown(r) for r in runs.recent(ctx.state_dir, runs.KEEP, command)]
        if problems:
            shown_runs = [r for r in shown_runs if r["status"] in PROBLEMS]
        commands = sorted(runs.latest(ctx.state_dir))
        return render_template("runs.html", runs=shown_runs, commands=commands, command=command,
                               problems=problems)

    @app.get("/runs/<int:run_id>")
    def run_page(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None:
            abort(404)
        lines, offset = runs.read_log(ctx.state_dir, run_id)
        journal = journal_of(run) if run["apply"] else None
        return render_template(
            "run.html", run=shown(run), lines=list(zip(lines, kinds(lines))), offset=offset,
            can_apply=bool(ctx.requests and ctx.apply and run["options"] is not None and not run["apply"]),
            journal=journal if journal and journal_path(ctx.state_dir, journal) else None)

    @app.get("/runs/<int:run_id>/log.json")
    def run_log(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None:
            abort(404)
        lines, offset = runs.read_log(ctx.state_dir, run_id, request.args.get("from", 0, type=int))
        line_kinds = kinds(lines, request.args.get("kind", ""))
        return jsonify(lines=[dict(text=text, kind=k) for text, k in zip(lines, line_kinds)],
                       next=offset, status=runs.status(run))

    @app.get("/runs/<int:run_id>/log.txt")
    def run_text(run_id):
        path = runs.log_path(ctx.state_dir, run_id)
        if runs.get(ctx.state_dir, run_id) is None or not path.exists():
            abort(404)
        return send_file(path, mimetype="text/plain; charset=utf-8")

    index = library.Index(ctx.root, ctx.state_dir)

    @app.get("/library")
    def library_page():
        query = request.args.get("q", "").strip()
        albums = index.search(query) if query else index.get()
        pages = max(math.ceil(len(albums) / library.PAGE), 1)
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        return render_template("library.html", query=query, total=len(albums), page=page, pages=pages,
                               albums=albums[(page - 1) * library.PAGE:page * library.PAGE],
                               empty=not index.get(),
                               without_lyrics=sum(a.without_lyrics for a in index.get()))

    @app.get("/library/album")
    def album_page():
        rel = request.args.get("folder", "")
        folder = library.album_folder(ctx.root, rel)
        if folder is None:
            t = TRANSLATIONS[pick_lang(request.accept_languages)]
            return render_template("not_found.html", message=t["album_gone"]), 404
        tracks = library.tracks_of(folder)
        mapping = genre_list(ctx.state_dir, lambda _msg: None) or {}
        lyrics = {name: (when(v) if isinstance(v, float) else v)
                  for name, v in library.lyrics_state(ctx.state_dir, folder, [n for n, _ in tracks]).items()}
        return render_template("album.html", folder=rel, tracks=tracks, common=library.common(tracks),
                               discs=library.several_discs(tracks),
                               seen=library.seen_state(tracks), genres=sorted(set(mapping.values())),
                               unchanged=request.args.get("unchanged") == "1", lyrics=lyrics)

    @app.post("/library/album/lyrics")
    def album_lyrics():
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        return submit("lyrics", {"folder": rel}, False)

    @app.get("/library/lyrics")
    def missed_lyrics():
        albums = [(a, when(at) if at else None)
                  for a, at in library.without_lyrics(index.get(), ctx.root, ctx.state_dir)]
        pages = max(math.ceil(len(albums) / library.PAGE), 1)
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        return render_template("lyrics.html", albums=albums[(page - 1) * library.PAGE:page * library.PAGE],
                               tracks=sum(a.without_lyrics for a, _ in albums), page=page, pages=pages,
                               days=f"{misses.retry_days():g}")

    @app.post("/library/album")
    def edit_album():
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        try:
            asked = library.edits(rel, request.form, json.loads(request.form.get("seen", "")))
        except (ValueError, KeyError, TypeError):
            abort(400)
        if not asked:
            return redirect(url_for("album_page", folder=rel, unchanged="1"))
        for _, name, value, _ in asked:
            try:
                fields.check(name, [value])
            except fields.FieldError as e:
                t = TRANSLATIONS[pick_lang(request.accept_languages)]
                return render_template("not_found.html", message=t["bad_value"].format(error=e)), 400
        return submit("tag", {"edits": asked}, False)

    @app.get("/library/cover")
    def album_cover():
        folder = library.album_folder(ctx.root, request.args.get("folder", ""))
        if folder is None:
            abort(404)
        found = library.cover(folder)
        if found is None:
            response = send_from_directory(app.static_folder, "no-cover.svg")
        else:
            response = app.response_class(found[0], mimetype=found[1])
        response.headers["Cache-Control"] = "max-age=300"
        return response

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("not_found.html"), 404

    return app
