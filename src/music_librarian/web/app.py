"""The dashboard's pages: the recent runs, their output, the schedule and the Inbox, the
library's albums, and the forms that queue a pass for watch to run (the reviews of split and
dedupe are in reviews.py).

Everything shown is read from the state folder and the Inbox on each request; the pages
themselves change nothing.
"""
import hmac
import ipaddress
import json
import math
import os
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, url_for)

from music_librarian import __version__, cover, repeats
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED, stop_pass
from music_librarian.importer import ERRORS, list_items
from music_librarian.lyrics import INSTRUMENTAL, is_instrumental, misses
from music_librarian.media import fields
from music_librarian.tag import genre_list
from music_librarian.web import library, reviews
from music_librarian.web.i18n import TRANSLATIONS, pick_lang
from music_librarian.web.passes import (CANCELLED, DONE, FORMS, LONG_WHEN, OPTIONS, UNDER, box, boxes_ticked,
                                        journal_path, keep_upload)

ERROR_TAGS = {"ERROR", "FAILED", "CONFLICT"}
WARNING_TAGS = {"WARNING", "SKIPPED", "REFUSED", "UNKNOWN", "GUARDED", "REVIEW", "MULTIPLE", "STOP", "LOCKED"}
PROBLEMS = {runs.INTERRUPTED, runs.ISSUES, runs.STOPPED, runs.FAILED}
HOME_PASSES = ("import", "organize", "enrich", "scan")
HISTORY_PAGE = 50
MAX_UPLOAD = 32 * 1024 * 1024
POLLED = {"static", "health", "built", "activity_fragment", "run_log", "album_cover"}
ALBUM_PASSES = {"tag", "cover", "lyrics"}
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal")


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
    hosts: frozenset = frozenset()   # the names it answers to besides local_host's


def host_name(host):
    """The name or address of a Host header, its port and an IPv6 address's brackets aside."""
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.rpartition(":")[0] if host.count(":") == 1 else host


def local_host(name):
    """Whether no public DNS can point `name` at the dashboard: an address, a name without a dot,
    or under a suffix kept for local networks. A DNS rebinding page is refused by its name."""
    name = name.lower().rstrip(".")
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return "." not in name or name.endswith(LOCAL_SUFFIXES)


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


def album_asked(command, options):
    """The album folder, relative to the root, a pass queued from an album's page works on, or None."""
    if command not in ALBUM_PASSES or not options:
        return None
    if command == "tag":
        edits = options.get("edits") or []
        return str(Path(edits[0][0]).parent) if edits else None
    return options.get("folder")


def moved_to(journal: Path, root: Path, folder):
    """Where the journal moved the tracks of the album folder `folder` (relative to `root`), else `folder`."""
    before = root / folder
    with open(journal, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") == "move" and Path(rec["src"]).parent == before:
                after = Path(rec["dst"]).parent
                return str(after.relative_to(root)) if after.is_relative_to(root) else folder
    return folder


def create_app(ctx: Context):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
    token = secrets.token_urlsafe(16)
    waiting, queues = reviews.register(app, ctx)
    index = library.Index(ctx.root, ctx.state_dir)
    built_in_background = (index.albums, *queues)
    credit_queue, genre_queue, instrumental_queue = queues
    shows = {"library_page": (index.albums,), "missed_lyrics": (index.albums,), "reviews_page": queues,
             "review_split": (credit_queue,), "review_dedupe": (credit_queue,),
             "review_genres": (genre_queue,), "genre_list_page": (genre_queue,),
             "review_instrumental": (instrumental_queue,)}

    @app.before_request
    def known_host():
        name = host_name(request.host).lower().rstrip(".")
        if not local_host(name) and name not in ctx.hosts:
            t = TRANSLATIONS[pick_lang(request.accept_languages)]
            return app.response_class(t["host_refused"].format(host=name), 403,
                                      mimetype="text/plain; charset=utf-8")
        return None

    @app.context_processor
    def language():
        lang = pick_lang(request.accept_languages)
        return dict(lang=lang, t=TRANSLATIONS[lang], ctx=ctx, version=__version__, csrf=token,
                    waiting_total=lambda: sum(n or 0 for n in waiting().values()), activity=activity(),
                    rebuilding=request.endpoint in shows and not settled(request.endpoint),
                    cover_url=cover_url, build_percent=lambda: build_percent(request.endpoint))

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
        albums = index.get()
        todo = dict(waiting(), failed=len(inbox[1]) if inbox else 0,
                    lyrics=None if albums is None else sum(a.without_lyrics for a in albums),
                    repeats=None if albums is None else sum(a.repeated for a in albums))
        return dict(todo=todo, genre_list=genre_list(ctx.state_dir, lambda _msg: None) is not None)

    @app.get("/tiles")
    def tiles_fragment():
        return render_template("_tiles.html", **to_do(inbox_state(ctx.inbox)))

    def activity():
        """The passes running now, and those the dashboard queued, for the header."""
        running = [dict(shown(r), stoppable=stoppable(r))
                   for r in runs.recent(ctx.state_dir, 20) if runs.status(r) == runs.RUNNING]
        return dict(running=running, queued=ctx.requests.pending() if ctx.requests else [])

    @app.get("/built")
    def built():
        page = request.args.get("page")
        return jsonify(ready=settled(page), percent=build_percent(page))

    @app.get("/activity")
    def activity_fragment():
        return render_template("_activity.html")

    @app.post("/passes")
    def new_pass():
        command = request.form.get("command", "")
        if command not in FORMS and command != "undo":
            abort(403)
        names = OPTIONS[command]
        options = {name: True for name in names if name != "journal"
                   and bool(request.form.get(box(name))) != name.startswith("no_")}
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
        album = album_url(asked.command, asked.options)
        if asked.state == DONE and asked.code == EXIT_LOCKED:
            return render_template("request.html", asked=asked, locked=True, album=album)
        if asked.run_id is not None:
            return redirect(url_for("run_page", run_id=asked.run_id))
        return render_template("request.html", asked=asked, locked=False, cancelled=asked.state == CANCELLED,
                               album=album)

    def album_url(command, options, journal=None):
        """The page of the album the pass works on, where it is after the pass; None when it is gone."""
        folder = album_asked(command, options)
        if folder is not None and journal is not None:
            folder = moved_to(journal, ctx.root, folder)
        return url_for("album_page", folder=folder) if folder and library.album_folder(ctx.root, folder) \
            else None

    @app.post("/requests/<int:request_id>/cancel")
    def cancel_request(request_id):
        if ctx.requests is None or not ctx.requests.cancel(request_id):
            abort(404)
        return redirect(url_for("request_page", request_id=request_id))

    def stoppable(run):
        """Whether the run is the pass this process is running, which only watch's runs can be."""
        return bool(ctx.requests and runs.status(run) == runs.RUNNING and run["pid"] == os.getpid()
                    and run["host"] == socket.gethostname())

    @app.post("/runs/<int:run_id>/stop")
    def stop_run(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None or not stoppable(run):
            abort(404)
        stop_pass()
        return redirect(url_for("run_page", run_id=run_id))

    @app.get("/runs")
    def history():
        command = request.args.get("command") or None
        problems = request.args.get("problems") == "1"
        shown_runs = [shown(r) for r in runs.recent(ctx.state_dir, runs.KEEP, command)]
        if problems:
            shown_runs = [r for r in shown_runs if r["status"] in PROBLEMS]
        commands = sorted(runs.latest(ctx.state_dir))
        pages = max(math.ceil(len(shown_runs) / HISTORY_PAGE), 1)
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        queued = [] if problems or page > 1 or not ctx.requests else \
            [r for r in reversed(ctx.requests.pending()) if command in (None, r.command)]
        return render_template("runs.html", runs=shown_runs[(page - 1) * HISTORY_PAGE:page * HISTORY_PAGE],
                               queued=queued, commands=commands, command=command, problems=problems,
                               page=page, pages=pages)

    @app.get("/runs/<int:run_id>")
    def run_page(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None:
            abort(404)
        lines, offset = runs.read_log(ctx.state_dir, run_id)
        journal = journal_of(run) if run["apply"] else None
        journal = journal if journal and journal_path(ctx.state_dir, journal) else None
        return render_template(
            "run.html", run=shown(run), lines=list(zip(lines, kinds(lines))), offset=offset,
            boxes=boxes_ticked(run["command"], run["options"])
            if run["options"] is not None and FORMS.get(run["command"]) else None,
            can_apply=bool(ctx.requests and ctx.apply and run["options"] is not None and not run["apply"]),
            can_stop=stoppable(run), journal=journal,
            album=album_url(run["command"], run["options"], journal and journal_path(ctx.state_dir, journal)))

    @app.get("/runs/<int:run_id>/log.json")
    def run_log(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None:
            abort(404)
        lines, offset = runs.read_log(ctx.state_dir, run_id, request.args.get("from", 0, type=int))
        line_kinds = kinds(lines, request.args.get("kind", ""))
        return jsonify(lines=[dict(text=text, kind=k) for text, k in zip(lines, line_kinds)],
                       next=offset, status=runs.status(run), duration=shown(run)["duration"])

    @app.get("/runs/<int:run_id>/log.txt")
    def run_text(run_id):
        path = runs.log_path(ctx.state_dir, run_id)
        if runs.get(ctx.state_dir, run_id) is None or not path.exists():
            abort(404)
        return send_file(path, mimetype="text/plain; charset=utf-8")

    @app.get("/library")
    def library_page():
        query = request.args.get("q", "").strip()
        everything = index.get()
        if everything is None:
            return render_template("library.html", query=query, building=True)
        albums = index.search(query) if query else everything
        pages = max(math.ceil(len(albums) / library.PAGE), 1)
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        return render_template("library.html", query=query, total=len(albums), page=page, pages=pages,
                               albums=albums[(page - 1) * library.PAGE:page * library.PAGE],
                               empty=not everything,
                               without_lyrics=sum(a.without_lyrics for a in everything),
                               repeated=sum(a.repeated for a in everything))

    @app.get("/library/album")
    def album_page():
        rel = request.args.get("folder", "")
        folder = library.album_folder(ctx.root, rel)
        if folder is None:
            t = TRANSLATIONS[pick_lang(request.accept_languages)]
            return render_template("not_found.html", message=t["album_gone"]), 404
        tracks = library.tracks_of(folder)
        repeated = library.repeated_fields(tracks)
        mapping = genre_list(ctx.state_dir, lambda _msg: None) or {}
        names = [n for n, _ in tracks]
        gains = library.replaygain_state(folder, names)
        for name, values in tracks:
            if values:
                values[fields.REPLAYGAIN] = gains[name]
        return render_template("album.html", folder=rel, tracks=tracks, common=library.common(tracks),
                               discs=library.several_discs(tracks),
                               seen=library.seen_state(tracks), genres=sorted(set(mapping.values())),
                               unchanged=request.args.get("unchanged") == "1",
                               lyrics=library.lyrics_state(folder, names),
                               repeated=repeated, repeated_album=library.album_repeats(repeated),
                               gains={n: fields.track_gain(v) or ("?" if v else None)
                                      for n, v in gains.items()})

    @app.post("/library/album/lyrics")
    def album_lyrics():
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        return submit("lyrics", {"folder": rel}, False)

    @app.get("/library/lyrics")
    def missed_lyrics():
        everything = index.get()
        if everything is None:
            return render_template("lyrics.html", building=True, days=f"{misses.retry_days():g}")
        albums = [(a, when(at) if at else None)
                  for a, at in library.without_lyrics(everything, ctx.root, ctx.state_dir)]
        pages = max(math.ceil(len(albums) / library.PAGE), 1)
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        return render_template("lyrics.html", albums=albums[(page - 1) * library.PAGE:page * library.PAGE],
                               tracks=sum(a.without_lyrics for a, _ in albums), page=page, pages=pages,
                               days=f"{misses.retry_days():g}")

    @app.get("/library/repeats")
    def repeated_values():
        everything = index.get()
        if everything is None:
            return render_template("repeats.html", building=True)
        albums = library.with_repeats(everything)
        pages = max(math.ceil(len(albums) / library.PAGE), 1)
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        return render_template("repeats.html", albums=albums[(page - 1) * library.PAGE:page * library.PAGE],
                               tracks=sum(a.repeated for a in albums), page=page, pages=pages)

    @app.post("/library/album")
    def edit_album():
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        try:
            seen = json.loads(request.form.get("seen", ""))
            if any(library.track_file(ctx.root, rel, name) is None for name, _ in seen["tracks"]):
                abort(400)
            asked = library.edits(rel, request.form, seen)
        except (ValueError, KeyError, TypeError):
            abort(400)
        if not asked:
            return redirect(url_for("album_page", folder=rel, unchanged="1"))
        for _, name, value, _ in asked:
            try:
                fields.check(name, [value])
                if (why := repeats.refusal(name, [value])) is not None:
                    raise fields.FieldError(why)
            except fields.FieldError as e:
                t = TRANSLATIONS[pick_lang(request.accept_languages)]
                return render_template("not_found.html", message=t["bad_value"].format(error=e)), 400
        return submit("tag", {"edits": asked}, False)

    def refused(key, status=400, **values):
        t = TRANSLATIONS[pick_lang(request.accept_languages)]
        return render_template("not_found.html", message=t[key].format(**values)), status

    @app.post("/library/album/cover")
    def album_new_cover():
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        upload = request.files.get("image")
        data = upload.read() if upload else b""
        if not data:
            return refused("cover_missing")
        if ctx.requests is None:
            abort(403)
        try:
            jpeg, _dims = cover.as_jpeg(data)
        except (cover.NotAnImage, OSError):
            return refused("cover_not_an_image")
        return submit("cover", {"folder": rel, "image": keep_upload(ctx.state_dir, jpeg)}, False)

    @app.errorhandler(413)
    def too_large(_error):
        return refused("cover_too_large", 413, mb=MAX_UPLOAD // 1024 // 1024)

    @app.get("/library/track/lyrics")
    def track_lyrics():
        rel, name = request.args.get("folder", ""), request.args.get("file", "")
        path = library.track_file(ctx.root, rel, name)
        if path is None:
            return refused("album_gone", 404)
        try:
            values, text = fields.read(path), fields.lyrics(path)
        except Exception:
            return refused("track_unreadable", 404)
        return render_template("track_lyrics.html", folder=rel, file=name, values=values,
                               text=text[0] if text else "", seen=json.dumps(text),
                               instrumental=bool(text) and is_instrumental(text[0]), mark=INSTRUMENTAL,
                               missed=None if text else when_missed(path),
                               unchanged=request.args.get("unchanged") == "1")

    def when_missed(path):
        at = misses.recorded(ctx.state_dir).get(path)
        return when(at) if at else None

    @app.post("/library/track/lyrics")
    def edit_lyrics():
        rel, name = request.form.get("folder", ""), request.form.get("file", "")
        if library.track_file(ctx.root, rel, name) is None:
            abort(404)
        try:
            seen = json.loads(request.form.get("seen", ""))
        except ValueError:
            abort(400)
        if not isinstance(seen, list) or not all(isinstance(v, str) for v in seen):
            abort(400)
        value = ("" if request.form.get("delete") else INSTRUMENTAL if request.form.get("instrumental")
                 else request.form.get("lyrics", ""))
        if fields.check(fields.LYRICS, [value]) == seen:
            return redirect(url_for("track_lyrics", folder=rel, file=name, unchanged="1"))
        return submit("tag", {"edits": [[str(Path(rel) / name), fields.LYRICS, value, seen]]}, False)

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
