"""The library's pages: the albums of the metadata cache, those without lyrics or with a field
holding a value twice, an album's page and its cover, a track's lyrics, and the forms that queue
their edits."""
import json
from pathlib import Path

from flask import abort, redirect, render_template, request, send_from_directory, url_for

from music_librarian import cover, repeats, tag
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED
from music_librarian.lyrics import INSTRUMENTAL, is_instrumental, misses
from music_librarian.media import fields
from music_librarian.tag import genre_list
from music_librarian.web import history, library
from music_librarian.web.common import paged, refused, submit, when
from music_librarian.web.passes import CANCELLED, DONE, keep_upload

MAX_UPLOAD = 32 * 1024 * 1024


def register(app, ctx, index):
    """Add the library's pages to the app, the albums read from `index` (a library.Index)."""
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

    @app.get("/library")
    def library_page():
        query = request.args.get("q", "").strip()
        everything = index.get()
        if everything is None:
            return render_template("library.html", query=query, building=True)
        albums = index.search(query) if query else everything
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("library.html", query=query, total=len(albums), page=page, pages=pages,
                               albums=shown,
                               empty=not everything,
                               without_lyrics=sum(a.without_lyrics for a in everything),
                               repeated=sum(a.repeated for a in everything))

    def followed(rel):
        """The pass ?request= names, as the album's page follows it: the request, its run once
        recorded, the journal it wrote, and where to ask whether it ended; None without one."""
        asked = ctx.requests.get(request.args.get("request", type=int)) if ctx.requests else None
        if asked is None:
            return None
        run = runs.get(ctx.state_dir, asked.run_id) if asked.run_id is not None else None
        journal = history.run_journal(ctx, run)
        return dict(asked=asked, run=history.shown(run) if run else None,
                    done=asked.state in (DONE, CANCELLED), journal=journal.name if journal else None,
                    locked=asked.state == DONE and asked.code == EXIT_LOCKED,
                    poll=url_for("request_state", request_id=asked.id, folder=rel))

    def album_view(rel, folder, status=200, **shown):
        """The album's page, `shown` adding what a form posted to it asks to show."""
        tracks = library.tracks_of(folder)
        repeated = library.repeated_fields(tracks)
        mapping = genre_list(ctx.state_dir, lambda _msg: None) or {}
        names = [n for n, _ in tracks]
        gains = library.replaygain_state(folder, names)
        for name, values in tracks:
            if values:
                values[fields.REPLAYGAIN] = gains[name]
        page = render_template("album.html", folder=rel, tracks=tracks, common=library.common(tracks),
                               discs=library.several_discs(tracks),
                               seen=library.seen_state(tracks), genres=sorted(set(mapping.values())),
                               lyrics=library.lyrics_state(folder, names),
                               repeated=repeated, repeated_album=library.album_repeats(repeated),
                               gains={n: fields.track_gain(v) or ("?" if v else None)
                                      for n, v in gains.items()},
                               followed=followed(rel), **shown)
        return page, status

    @app.get("/library/album")
    def album_page():
        rel = request.args.get("folder", "")
        folder = library.album_folder(ctx.root, rel)
        if folder is None:
            return refused("album_gone", 404)
        return album_view(rel, folder, unchanged=request.args.get("unchanged") == "1")

    @app.post("/library/album/lyrics")
    def album_lyrics():
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        return submit(ctx, "lyrics", {"folder": rel}, False, rel)

    @app.get("/library/lyrics")
    def missed_lyrics():
        everything = index.get()
        if everything is None:
            return render_template("lyrics.html", building=True, days=f"{misses.retry_days():g}")
        albums = [(a, when(at) if at else None)
                  for a, at in library.without_lyrics(everything, ctx.root, ctx.state_dir)]
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("lyrics.html", albums=shown,
                               tracks=sum(a.without_lyrics for a, _ in albums), page=page, pages=pages,
                               days=f"{misses.retry_days():g}")

    @app.get("/library/repeats")
    def repeated_values():
        everything = index.get()
        if everything is None:
            return render_template("repeats.html", building=True)
        albums = library.with_repeats(everything)
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("repeats.html", albums=shown,
                               tracks=sum(a.repeated for a in albums), page=page, pages=pages)

    @app.post("/library/album")
    def edit_album():
        """Show what the form changes on the album's page, or with apply=1, queue it applied."""
        rel = request.form.get("folder", "")
        folder = library.album_folder(ctx.root, rel)
        if folder is None:
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
                return album_view(rel, folder, 400, form=request.form, bad_value=str(e))
        if request.form.get("apply") == "1":
            return submit(ctx, "tag", {"edits": asked}, True, rel)
        edits = [tag.Edit(ctx.root / path, name, value, seen) for path, name, value, seen in asked]
        shown = tag.preview(tag.Options(root=ctx.root, edits=edits, state_dir=ctx.state_dir))
        return album_view(rel, folder, form=request.form,
                          preview=library.preview_changes(shown, ctx.root))

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
        return submit(ctx, "cover", {"folder": rel, "image": keep_upload(ctx.state_dir, jpeg)}, False, rel)

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
        edit = [str(Path(rel) / name), fields.LYRICS, value, seen]
        return submit(ctx, "tag", {"edits": [edit]}, False, rel)

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
