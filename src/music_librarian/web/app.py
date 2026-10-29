"""The dashboard's pages: the recent runs, their output, the schedule and the Inbox.

Everything is read from the state folder and the Inbox on each request; nothing is changed.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from music_librarian import __version__
from music_librarian.core import runs
from music_librarian.importer import ERRORS, list_items
from music_librarian.split import decisions
from music_librarian.split.rules import PENDING
from music_librarian.web.i18n import TRANSLATIONS, pick_lang

ERROR_TAGS = {"ERROR", "FAILED", "CONFLICT"}
WARNING_TAGS = {"WARNING", "SKIPPED", "REFUSED", "UNKNOWN", "GUARDED", "REVIEW", "STOP", "LOCKED"}
PROBLEMS = {runs.INTERRUPTED, runs.ISSUES, runs.STOPPED, runs.FAILED}


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
    """(names of the Inbox items, entries in _failed/), or None for a missing Inbox."""
    try:
        items = [it.name for it in list_items(inbox)]
    except OSError:
        return None
    failed = inbox / ERRORS
    return items, sum(1 for _ in failed.iterdir()) if failed.is_dir() else 0


def pending_decisions(state_dir: Path):
    decided = decisions.load(state_dir / decisions.FILE, lambda _msg: None)
    return sum(1 for d in decided.values() if d == PENDING)


def create_app(ctx: Context):
    app = Flask(__name__)

    @app.context_processor
    def language():
        lang = pick_lang(request.accept_languages)
        return dict(lang=lang, t=TRANSLATIONS[lang], ctx=ctx, version=__version__)

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
        recent = [shown(r) for r in runs.recent(ctx.state_dir, runs.KEEP)]
        latest = {c: shown(r) for c, r in runs.latest(ctx.state_dir).items()}
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
            "home.html", running=[r for r in recent if r["status"] == runs.RUNNING],
            recent=recent[:10], latest=latest, schedule=schedule, inbox=inbox_state(ctx.inbox),
            pending=pending_decisions(ctx.state_dir))

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
        return render_template("run.html", run=shown(run), lines=list(zip(lines, kinds(lines))),
                               offset=offset)

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

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("not_found.html"), 404

    return app
