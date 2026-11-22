"""A Deezer download asked from the overview's Inbox or from the Chrome extension: queued for watch as
any pass, applied at once as an upload is, refused while watch runs without --apply or the download is
off (deezer/config.py). The Inbox's form also needs DEEZER_LICENSE_TOKEN; the extension needs it, or
sends a token of its own.

The extension's routes (/deezer/ext/…) answer only a request bearing DEEZER_EXTENSION_KEY from the
origin DEEZER_EXTENSION_ORIGIN, and 404 while either is unset. They only queue a download and tell how
far it has got: the extension polls GET /deezer/ext/request/<id>.
"""
import hmac
import os
import re

from flask import abort, jsonify, request

from music_librarian import deezer
from music_librarian.core import runs
from music_librarian.deezer import config
from music_librarian.web.common import refused, submit
from music_librarian.web.history import progress
from music_librarian.web.passes import CANCELLED, DONE

EXTENSION_KEY, EXTENSION_ORIGIN = "DEEZER_EXTENSION_KEY", "DEEZER_EXTENSION_ORIGIN"
TOTAL = re.compile(r"^(?:ALBUM|PLAYLIST) .* \((\d+) tracks?\)$")
TRACK_DONE = ("DOWNLOAD ", "SKIPPED  ")


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


def _extension():
    """(key, origin) of the extension, both set, else None."""
    key = os.environ.get(EXTENSION_KEY, "").strip()
    origin = os.environ.get(EXTENSION_ORIGIN, "").strip().rstrip("/")
    return (key, origin) if key and origin else None


def tracks_done(lines):
    """(tracks gone through, tracks to go through) of a deezer pass's output so far."""
    total = sum(int(m[1]) for m in (TOTAL.match(line) for line in lines) if m)
    return sum(1 for line in lines if line.startswith(TRACK_DONE)), total


def register_extension(app, ctx):
    """Add the routes of the Chrome extension."""

    def cors(response, origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Vary"] = "Origin"
        return response

    def guarded(answer):
        """`answer()`'s response, once the extension is configured, the origin its own and, but for a
        preflight, the key its own."""
        extension = _extension()
        if extension is None:
            abort(404)
        key, origin = extension
        if request.headers.get("Origin", "").rstrip("/") != origin:
            abort(403)
        if request.method == "OPTIONS":
            return cors(app.response_class(status=204), origin)
        sent = request.headers.get("Authorization", "")
        if not hmac.compare_digest(sent.encode(), f"Bearer {key}".encode()):
            return cors(jsonify(error="wrong key"), origin), 401
        response, status = answer()
        return cors(response, origin), status

    @app.route("/deezer/ext/health", methods=["GET", "OPTIONS"])
    def deezer_ext_health():
        return guarded(lambda: (jsonify(status="ok", enabled=config.enabled(), apply=ctx.apply), 200))

    @app.route("/deezer/ext/request", methods=["POST", "OPTIONS"])
    def deezer_ext_request():
        return guarded(_queue)

    @app.route("/deezer/ext/request/<int:request_id>", methods=["GET", "OPTIONS"])
    def deezer_ext_request_state(request_id):
        return guarded(lambda: _state(request_id))

    def _queue():
        if ctx.requests is None or not ctx.apply or not config.enabled():
            return jsonify(error="the dashboard cannot download: watch runs without --apply, "
                                 "or the Deezer download is off"), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get("kind") not in deezer.KINDS:
            return jsonify(error="expected {\"kind\": \"album\"|\"playlist\", \"id\": …}"), 400
        try:
            item = deezer.item_id(body["kind"], str(body.get("id", "")))
        except ValueError:
            return jsonify(error="not an album or a playlist id"), 400
        token = body.get("license_token") if isinstance(body.get("license_token"), str) else ""
        if not token.strip() and not config.has_license_token():
            return jsonify(error="no license token: open Deezer in the browser first"), 400
        asked = ctx.requests.submit("deezer", {"kind": body["kind"], "ids": [item], "complete_only": False},
                                    True, license_token=token.strip())
        return jsonify(id=asked.id), 201

    def _state(request_id):
        asked = ctx.requests.get(request_id) if ctx.requests else None
        if asked is None or asked.command != "deezer":
            return jsonify(error="no such request"), 404
        run = runs.get(ctx.state_dir, asked.run_id) if asked.run_id is not None else None
        lines = runs.read_log(ctx.state_dir, run["id"])[0] if run else []
        done, total = tracks_done(lines)
        answer = dict(state=asked.state, code=asked.code, done=done, total=total,
                      step=(progress(ctx.state_dir, run) or {}).get("step", ""))
        if asked.state in (DONE, CANCELLED) and run:
            answer["summary"] = run["summary"]
        return jsonify(answer), 200
