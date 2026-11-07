"""What the dashboard's pages share: times as shown, the language's strings, a page of a list,
a refusal, and the pass a form queues."""
import math
from datetime import datetime

from flask import abort, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from music_librarian.web.i18n import TRANSLATIONS, pick_lang
from music_librarian.web.passes import OPTIONS


WORK_LISTS = {"repeats": "repeated_values", "lyrics": "missed_lyrics"}   # name: its page


def when(timestamp):
    return f"{datetime.fromtimestamp(timestamp):%Y-%m-%d %H:%M}"


def texts():
    """The strings of the language the request asks for."""
    return TRANSLATIONS[pick_lang(request.accept_languages)]


def paged(items, size):
    """(the items of the page ?page= asks for, that page, the number of pages), a page out of
    range taken as the nearest one."""
    pages = max(math.ceil(len(items) / size), 1)
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    return items[(page - 1) * size:page * size], page, pages


def refused(key, status=400, **values):
    """The page that tells why a request was refused, from the string `key`."""
    return render_template("not_found.html", message=texts()[key].format(**values)), status


def place():
    """The work list an album's page was opened from, as the request carries it on: {"list": name,
    "at": the album's position in it}, {} for none. The album's page keeps it in its links and forms."""
    try:
        values = request.values
    except RequestEntityTooLarge:   # the 413 page: its form is the one too large to read
        return {}
    name = values.get("list")
    if name not in WORK_LISTS:
        return {}
    return {"list": name, "at": max(values.get("at", 0, type=int), 0)}


def submit(ctx, command, options, apply, album=None):
    """Queue the pass for watch and redirect to its page, or to the page of `album` (a folder
    relative to the root) that follows it; 403 without a queue, for an unknown command, or to
    apply while watch runs without --apply."""
    if ctx.requests is None or command not in OPTIONS or (apply and not ctx.apply):
        abort(403)
    request_id = ctx.requests.submit(command, options, apply).id
    if album is not None:
        return redirect(url_for("album_page", folder=album, request=request_id, **place()))
    return redirect(url_for("request_page", request_id=request_id))
