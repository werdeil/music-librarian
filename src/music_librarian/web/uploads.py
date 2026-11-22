"""Files sent to the Inbox from the browser: one batch at a time, written to a hidden folder of the
Inbox that watch does not list, then moved into it, where watch imports them as any copy."""
import re
import secrets
import shutil
from pathlib import Path, PurePosixPath

from flask import abort, jsonify, request

from music_librarian.core.files import AUDIO_EXT, drop_abandoned, is_junk
from music_librarian.importer import ANNEX_EXT, OTHER_AUDIO, RESERVED, free_name
from music_librarian.web.common import texts

STAGING = ".upload-"
BATCH_ID = re.compile(r"^[0-9a-f]{16}$")
SENT_EXT = frozenset(AUDIO_EXT | OTHER_AUDIO | ANNEX_EXT)
MAX_FILE = 2 * 1024 ** 3   # waitress's max_request_body_size is raised to match
CHUNK = 1024 * 1024


def sent_path(rel):
    """The parts of a path sent by the browser, relative and '/'-separated, or None when it would
    leave the batch, be hidden or reserved, or not be a file the import takes."""
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") or "\\" in p or "\0" in p for p in parts):
        return None
    if parts[0] in RESERVED or is_junk(Path(parts[-1])):
        return None
    return parts if PurePosixPath(parts[-1]).suffix.lower() in SENT_EXT else None


def register(app, ctx):
    """Add the routes that send files to the Inbox, refused while watch runs without --apply."""

    def error(key, status, **values):
        return jsonify(error=texts()[key].format(**values)), status

    def allowed():
        if ctx.requests is None or not ctx.apply:
            abort(403)
        if not ctx.inbox.is_dir():
            abort(404)

    def batch(batch_id):
        allowed()
        folder = ctx.inbox / f"{STAGING}{batch_id}"
        if not BATCH_ID.match(batch_id) or not folder.is_dir():
            abort(404)
        return folder

    @app.post("/inbox/uploads")
    def inbox_upload_start():
        """{"id": …} of a new batch, `size` the bytes it will send; 507 when the disk lacks room."""
        allowed()
        drop_abandoned(ctx.inbox, STAGING)
        size = request.form.get("size", 0, type=int)
        free = shutil.disk_usage(ctx.inbox).free
        if size > free:
            return error("upload_no_room", 507, free=free // 1024 ** 2)
        batch_id = secrets.token_hex(8)
        (ctx.inbox / f"{STAGING}{batch_id}").mkdir()
        return jsonify(id=batch_id), 201

    @app.post("/inbox/uploads/<batch_id>")
    def inbox_upload_file(batch_id):
        """One file of the batch, its path in `?path=`, its content the request's body."""
        folder = batch(batch_id)
        parts = sent_path(request.args.get("path", ""))
        if parts is None:
            return error("upload_refused", 400, name=request.args.get("path", ""))
        if request.content_length is None:
            abort(411)
        if request.content_length > MAX_FILE:
            return error("upload_too_large", 413, gb=MAX_FILE // 1024 ** 3)
        request.max_content_length = MAX_FILE
        target = folder.joinpath(*parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as out:
                shutil.copyfileobj(request.stream, out, CHUNK)
        except FileExistsError:
            return error("upload_twice", 409, name="/".join(parts))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return "", 204

    @app.post("/inbox/uploads/<batch_id>/done")
    def inbox_upload_done(batch_id):
        """Move the batch's items into the Inbox, renamed `name (2)` when one is already there:
        {"added": their names, "message": …}."""
        folder = batch(batch_id)
        added = []
        for item in sorted(folder.iterdir()):
            target = free_name(ctx.inbox, item.name, item.is_dir())
            item.rename(target)
            added.append(target.name)
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify(added=added, message=texts()["upload_added"].format(n=len(added)))

    @app.post("/inbox/uploads/<batch_id>/cancel")
    def inbox_upload_cancel(batch_id):
        shutil.rmtree(batch(batch_id), ignore_errors=True)
        return "", 204
