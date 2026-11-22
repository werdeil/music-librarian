"""A Deezer download asked from the overview's Inbox: queued for watch as any pass, applied at once as
an upload is, refused while watch runs without --apply, the download is off (deezer/config.py) or
DEEZER_LICENSE_TOKEN is not set."""
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
        if ctx.requests is None or not ctx.apply or not config.enabled() or not config.has_license_token():
            abort(403)
        value = request.form.get("item", "")
        kind = kind_of(value, request.form.get("kind", "album"))
        if kind not in deezer.KINDS:
            abort(400)
        if deezer.SHORT_URL.match(value.strip()):
            return refused("deezer_short_link", value=value)
        try:
            item = deezer.item_id(kind, value)
        except ValueError:
            return refused("deezer_refused", value=value)
        options = {"kind": kind, "ids": [item], "complete_only": bool(request.form.get("complete_only"))}
        return submit(ctx, "deezer", options, True)
