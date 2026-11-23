"""The items the import left in the Inbox's _failed/: why each failed, read from the import runs,
put back into the Inbox for watch to import again, or set aside into _done/, which the import purges."""
import shutil
from datetime import date
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

from music_librarian.core import runs
from music_librarian.importer import DONE, ERRORS, free_name

ERROR_TAG = "ERROR    "
SENT_TO = "→ "


def entries(inbox: Path):
    """The names in _failed/, hidden ones aside, sorted."""
    failed = inbox / ERRORS
    try:
        return sorted(p.name for p in failed.iterdir() if not p.name.startswith("."))
    except OSError:
        return []


def reasons(state_dir: Path, names):
    """{name: (the reason its import gave, that run's id)} for the names in _failed/ a kept import run
    sent there, the latest such run for each."""
    wanted, found = set(names), {}
    for run in runs.recent(state_dir, runs.KEEP, "import"):
        if not wanted - set(found):
            break
        if not run["apply"]:
            continue
        error = None
        for line in runs.read_log(state_dir, run["id"])[0]:
            if line.startswith(ERROR_TAG):
                error = line[len(ERROR_TAG):].partition(": ")[2]
            elif error is not None and line.strip().startswith(SENT_TO):
                target = Path(line.strip()[len(SENT_TO):])
                if target.parent.name == ERRORS and target.name in wanted and target.name not in found:
                    found[target.name] = (error, run["id"])
            elif not line[:1].isspace():
                error = None
    return found


def register(app, ctx):
    """Add the page of the failed imports and its two actions, refused while watch runs without --apply."""

    def chosen():
        if ctx.requests is None or not ctx.apply:
            abort(403)
        name = request.form.get("name", "")
        if name not in entries(ctx.inbox):
            abort(404)
        return ctx.inbox / ERRORS / name

    @app.get("/inbox/failed")
    def failed_imports():
        names = entries(ctx.inbox)
        why = reasons(ctx.state_dir, names)
        items = [dict(name=n, folder=(ctx.inbox / ERRORS / n).is_dir(), reason=why.get(n, (None, None))[0],
                      run=why.get(n, (None, None))[1]) for n in names]
        return render_template("failed.html", items=items, retried=request.args.get("retried"),
                               dismissed=request.args.get("dismissed"))

    @app.post("/inbox/failed/retry")
    def failed_retry():
        """Move the item back to the Inbox's root, renamed `name (2)` when one is already there."""
        item = chosen()
        target = free_name(ctx.inbox, item.name, item.is_dir())
        item.rename(target)
        return redirect(url_for("failed_imports", retried=target.name))

    @app.post("/inbox/failed/dismiss")
    def failed_dismiss():
        """Move the item to today's folder of _done/, purged with the originals of that day."""
        item = chosen()
        day = ctx.inbox / DONE / date.today().isoformat()
        day.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item), str(free_name(day, item.name, item.is_dir())))
        return redirect(url_for("failed_imports", dismissed=item.name))
