"""A Deezer download asked from the overview's Inbox: queued for watch as any pass, applied at once,
refused while watch runs without --apply or the download is off (deezer/config.py)."""
from flask import abort, request

from music_librarian import deezer
from music_librarian.deezer import config
from music_librarian.web.common import refused, submit


def kind_of(value, kind):
    """The kind a deezer.com address names, else `kind` (for a bare id)."""
    found = deezer.URL.match(value.strip())
    return found[1].lower() if found else kind


def register(app, ctx):
    """Add the route of the overview's Deezer form."""

    @app.post("/deezer")
    def deezer_download():
        if ctx.requests is None or not ctx.apply or not config.enabled():
            abort(403)
        value = request.form.get("item", "")
        kind = kind_of(value, request.form.get("kind", "album"))
        if kind not in deezer.KINDS:
            abort(400)
        try:
            item = deezer.item_id(kind, value)
        except ValueError:
            return refused("deezer_refused", value=value)
        options = {"kind": kind, "ids": [item], "complete_only": bool(request.form.get("complete_only"))}
        return submit(ctx, "deezer", options, True)
