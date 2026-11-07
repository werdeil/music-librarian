"""The run history and the passes the dashboard queues: the forms that ask for one, its waiting
page, the history, a run's page and output followed live, and a running pass stopped."""
import json
import os
import socket
from datetime import datetime
from pathlib import Path

from flask import abort, jsonify, redirect, render_template, request, send_file, url_for

from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED, stop_pass
from music_librarian.web import library
from music_librarian.web.common import paged, place, submit, when
from music_librarian.web.passes import CANCELLED, DONE, FORMS, OPTIONS, box, boxes_ticked, journal_path

ERROR_TAGS = {"ERROR", "FAILED", "CONFLICT"}
WARNING_TAGS = {"WARNING", "SKIPPED", "REFUSED", "UNKNOWN", "GUARDED", "REVIEW", "MULTIPLE", "STOP", "LOCKED"}
PROBLEMS = {runs.INTERRUPTED, runs.ISSUES, runs.STOPPED, runs.FAILED}
HISTORY_PAGE = 50
ALBUM_PASSES = {"tag", "cover", "lyrics"}
REVIEW_PASSES = {"split", "dedupe", "genres"}


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


def duration(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds} s"


def shown(run):
    """The run as the templates show it: status, start and duration formatted."""
    status = runs.status(run)
    end = run["ended"] if run["ended"] is not None else datetime.now().timestamp()
    return dict(run, status=status, when=when(run["started"]),
                duration=None if status == runs.INTERRUPTED else duration(end - run["started"]))


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


def album_url(ctx, command, options, journal=None):
    """The page of the album the pass works on, where it is after the pass; None when it is gone."""
    folder = album_asked(command, options)
    if folder is not None and journal is not None:
        folder = moved_to(journal, ctx.root, folder)
    return url_for("album_page", folder=folder) if folder and library.album_folder(ctx.root, folder) \
        else None


def stoppable(ctx, run):
    """Whether the run is the pass this process is running, which only watch's runs can be."""
    return bool(ctx.requests and runs.status(run) == runs.RUNNING and run["pid"] == os.getpid()
                and run["host"] == socket.gethostname())


def album_followed(ctx):
    """The album folder a form posted from an album's page names, that page then following the
    pass; None for any other form."""
    rel = request.form.get("album")
    return rel if rel is not None and library.album_folder(ctx.root, rel) else None


def run_journal(ctx, run):
    """The journal of an applied run, while it is still in the state folder, else None."""
    name = journal_of(run) if run and run["apply"] else None
    return journal_path(ctx.state_dir, name) if name else None


def register(app, ctx):
    """Add the pages of the run history and of the passes asked for to the app."""

    @app.get("/requests/<int:request_id>.json")
    def request_state(request_id):
        """Whether the pass has ended, and the page of the album ?folder= names, where it is now."""
        asked = ctx.requests.get(request_id) if ctx.requests else None
        if asked is None:
            abort(404)
        done = asked.state in (DONE, CANCELLED)
        folder = request.args.get("folder")
        album = None
        if done and folder is not None:
            run = runs.get(ctx.state_dir, asked.run_id) if asked.run_id is not None else None
            journal = run_journal(ctx, run)
            moved = moved_to(journal, ctx.root, folder) if journal else folder
            album = url_for("album_page", folder=moved, request=request_id, **place())
        return jsonify(done=done, album=album)

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
        return submit(ctx, command, options, request.form.get("apply") == "1", album_followed(ctx))

    @app.post("/runs/<int:run_id>/apply")
    def apply_run(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None or run["options"] is None or run["apply"] or runs.status(run) == runs.RUNNING:
            abort(404)
        return submit(ctx, run["command"], run["options"], True, album_followed(ctx))

    @app.get("/requests/<int:request_id>")
    def request_page(request_id):
        asked = ctx.requests.get(request_id) if ctx.requests else None
        if asked is None:
            abort(404)
        album = album_url(ctx, asked.command, asked.options)
        if asked.state == DONE and asked.code == EXIT_LOCKED:
            return render_template("request.html", asked=asked, locked=True, album=album)
        if asked.run_id is not None:
            return redirect(url_for("run_page", run_id=asked.run_id))
        return render_template("request.html", asked=asked, locked=False, cancelled=asked.state == CANCELLED,
                               album=album)

    @app.post("/requests/<int:request_id>/cancel")
    def cancel_request(request_id):
        if ctx.requests is None or not ctx.requests.cancel(request_id):
            abort(404)
        album = album_followed(ctx)
        if album is not None:
            return redirect(url_for("album_page", folder=album, request=request_id, **place()))
        return redirect(url_for("request_page", request_id=request_id))

    @app.post("/runs/<int:run_id>/stop")
    def stop_run(run_id):
        run = runs.get(ctx.state_dir, run_id)
        if run is None or not stoppable(ctx, run):
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
        page_runs, page, pages = paged(shown_runs, HISTORY_PAGE)
        queued = [] if problems or page > 1 or not ctx.requests else \
            [r for r in reversed(ctx.requests.pending()) if command in (None, r.command)]
        return render_template("runs.html", runs=page_runs,
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
        album = album_url(ctx, run["command"], run["options"],
                          journal and journal_path(ctx.state_dir, journal))
        back = (album, "back_to_album") if album else \
            (url_for("reviews_page"), "back_to_reviews") if run["command"] in REVIEW_PASSES else \
            (url_for("home"), "back_to_overview")
        return render_template(
            "run.html", run=shown(run), lines=list(zip(lines, kinds(lines))), offset=offset,
            boxes=boxes_ticked(run["command"], run["options"])
            if run["options"] is not None and FORMS.get(run["command"]) else None,
            can_apply=bool(ctx.requests and ctx.apply and run["options"] is not None and not run["apply"]),
            can_stop=stoppable(ctx, run), journal=journal, back=back)

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
