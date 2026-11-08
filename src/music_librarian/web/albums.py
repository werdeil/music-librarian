"""The library's pages: the albums and tracks of the metadata cache, searched field by field, those
without lyrics or with a field holding a value twice, an album's page with its tracks' lyrics and
its cover, and the forms that queue their edits."""
import json
from urllib.parse import parse_qsl, urlencode

from flask import abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.datastructures import MultiDict

from music_librarian import cover, repeats, tag
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED
from music_librarian.enrich import lyrics as enrich_lyrics
from music_librarian.lyrics import INSTRUMENTAL, misses
from music_librarian.media import artwork, fields
from music_librarian.tag import genre_list
from music_librarian.web import history, library, query
from music_librarian.web.common import WORK_LISTS, paged, place, refused, submit, texts, when
from music_librarian.web.passes import CANCELLED, DONE, keep_upload, upload_path

MAX_UPLOAD = 32 * 1024 * 1024
PROVIDERS = {"lrclib": "LRCLIB", "netease": "NetEase", "musixmatch": "Musixmatch"}


def register(app, ctx, index):
    """Add the library's pages to the app, the albums read from `index` (a library.Index)."""
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

    def searched(args):
        """What the library page shows for the request's arguments `args`, the albums or tracks
        found None until the album list is built."""
        words = args.get("q", "").strip()
        sort = args.get("sort") if args.get("sort") in library.SORTS else "artist"
        view = args.get("view") if args.get("view") in library.VIEWS else "albums"
        search = query.from_args(args)
        keep = dict(q=words or None, sort=None if sort == "artist" else sort,
                    view=None if view == "albums" else view)
        address = urlencode({k: v for k, v in {**keep, **search.args()}.items() if v}, doseq=True, safe=":")
        catalog = index.catalog()
        found = None if catalog is None else library.find(catalog, words, search, sort, view)
        return dict(query=words, sort=sort, view=view, search=search, keep=keep, address=address,
                    filtered=bool(words or search.conditions), found=found,
                    empty=catalog is not None and not catalog.albums)

    @app.get("/library")
    def library_page():
        shown = searched(request.args)
        rows = [condition_row(c) for c in shown["search"].conditions] + [condition_row(None)]
        t = texts()
        shown.update(rows=rows, groups=query.GROUPS, operators=query.OPERATORS, bare=query.BARE,
                     kinds=query.FIELDS, presets=query.PRESETS,
                     operator_names={op: t[f"op_{op}"] for ops in query.OPERATORS.values() for op in ops},
                     paging={**shown["keep"], **shown["search"].args()},
                     views={name: url_for("library_page", **{**shown["keep"], **shown["search"].args(),
                                                             "view": None if name == "albums" else name})
                            for name in library.VIEWS})
        found = shown.pop("found")
        if found is None:
            return render_template("library.html", building=True, **shown)
        at = {a.folder: i for i, a in enumerate(found.albums)}
        if shown["view"] == "tracks":
            listed, page, pages = paged(found.tracks, library.TRACK_PAGE)
            total = len(found.tracks)
        else:
            listed, page, pages = paged(found.albums, library.PAGE)
            total = len(found.albums)
        return render_template("library.html", total=total, page=page, pages=pages, listed=listed,
                               counts=found.counts, matched=found.matched, at=at,
                               columns=shown["search"].columns(), cell=query.cell,
                               first_album=found.albums[0].folder if found.albums else None, **shown)

    def condition_row(c):
        """A row of the search form: a condition, or a blank one for None."""
        if c is None:
            return dict(field="any", tag="", op="contains", value="", upto="", kind=query.TEXT, blank=True)
        field, name = ("tag", c.field[4:]) if c.field.startswith("tag.") else (c.field, "")
        return dict(field=field, tag=name, op=c.op, value=c.value, upto=c.upto, kind=c.kind, blank=False)

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
                    poll=url_for("request_state", request_id=asked.id, folder=rel, **place()))

    def work_list(name):
        """The albums of the work list `name`, in its order; None until the album list is built."""
        if name == "search":
            found = searched(MultiDict(parse_qsl(place().get("search", ""))))["found"]
            return None if found is None else found.albums
        everything = index.get()
        if everything is None:
            return None
        if name == "repeats":
            return library.with_repeats(everything)
        return [a for a, _ in library.without_lyrics(everything, ctx.root, ctx.state_dir)]

    def in_work_list(rel):
        """Where the album stands in the work list it was opened from, with its neighbours; for
        an album the list no longer holds (fixed), its neighbours where it stood. None without one."""
        where = place()
        albums = work_list(where["list"]) if where else None
        if albums is None:
            return None
        folders = [a.folder for a in albums]
        if rel in folders:
            at = folders.index(rel)
            before, after = at - 1, at + 1
        else:
            at = None
            after = min(where["at"], len(folders))
            before = after - 1
        page = url_for(WORK_LISTS[where["list"]])
        if where.get("search"):
            page += "?" + where["search"]
        keep = {k: v for k, v in where.items() if k != "at"}

        def step(folder, position):
            return url_for("album_page", folder=folder, **keep, at=position)
        return dict(name=where["list"], page=page, position=at,
                    total=len(folders),
                    previous=step(folders[before], before) if before >= 0 else None,
                    next=step(folders[after], after) if after < len(folders) else None)

    def shown_value(name, values):
        """A field's values as the preview shows them, the lyrics by their number of lines."""
        t = texts()
        if not values:
            return t["value_none"]
        if name != fields.LYRICS:
            return fields.brief(name, values)
        if values[0] == INSTRUMENTAL:
            return t["lyrics_instrumental"]
        lines = len(values[0].split("\n"))
        return (t["line_one"] if lines == 1 else t["line_many"]).format(n=lines)

    def album_view(rel, folder, status=200, **shown):
        """The album's page, `shown` adding what a form posted to it asks to show."""
        tracks = library.tracks_of(folder)
        repeated = library.repeated_fields(tracks)
        mapping = genre_list(ctx.state_dir, lambda _msg: None) or {}
        names = [n for n, _ in tracks]
        lyrics = library.with_lyrics(folder, tracks)
        missed = misses.recorded(ctx.state_dir)
        gains = library.replaygain_state(folder, names)
        for name, values in tracks:
            if values:
                values[fields.REPLAYGAIN] = gains[name]
        page = render_template("album.html", folder=rel, tracks=tracks, common=library.common(tracks),
                               discs=library.several_discs(tracks), disc_of=library.disc_of,
                               disc_names=library.disc_names(tracks),
                               seen=library.seen_state(tracks), genres=sorted(set(mapping.values())),
                               lyrics=lyrics, mark=INSTRUMENTAL,
                               missed={n: when(missed[folder / n]) for n in names if folder / n in missed},
                               repeated=repeated, repeated_album=library.album_repeats(repeated),
                               gains={n: fields.track_gain(v) or ("?" if v else None)
                                      for n, v in gains.items()},
                               followed=followed(rel), work=in_work_list(rel), **shown)
        return page, status

    @app.get("/library/album")
    def album_page():
        rel = request.args.get("folder", "")
        folder = library.album_folder(ctx.root, rel)
        if folder is None:
            return refused("album_gone", 404)
        return album_view(rel, folder, unchanged=request.args.get("unchanged") == "1")

    @app.get("/library/lyrics")
    def missed_lyrics():
        everything = index.get()
        if everything is None:
            return render_template("lyrics.html", building=True, days=f"{misses.retry_days():g}")
        albums = [(a, when(at) if at else None)
                  for a, at in library.without_lyrics(everything, ctx.root, ctx.state_dir)]
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("lyrics.html", albums=shown, offset=(page - 1) * library.PAGE,
                               tracks=sum(a.without_lyrics for a, _ in albums), page=page, pages=pages,
                               days=f"{misses.retry_days():g}")

    @app.get("/library/repeats")
    def repeated_values():
        everything = index.get()
        if everything is None:
            return render_template("repeats.html", building=True)
        albums = library.with_repeats(everything)
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("repeats.html", albums=shown, offset=(page - 1) * library.PAGE,
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
        try:
            new_cover = sent_cover()
        except (cover.NotAnImage, OSError):
            return album_view(rel, folder, 400, form=request.form, cover_refused=True)
        if not asked and new_cover is None:
            return redirect(url_for("album_page", folder=rel, unchanged="1"))
        for _, name, value, _ in asked:
            try:
                fields.check(name, [value])
                if (why := repeats.refusal(name, [value])) is not None:
                    raise fields.FieldError(why)
            except fields.FieldError as e:
                return album_view(rel, folder, 400, form=request.form, bad_value=str(e), new_cover=new_cover)
        if request.form.get("apply") == "1":
            options = {"edits": asked, "cover": [rel, new_cover] if new_cover else None}
            return submit(ctx, "tag", options, True, rel)
        edits = [tag.Edit(ctx.root / path, name, value, seen) for path, name, value, seen in asked]
        shown = tag.preview(tag.Options(root=ctx.root, edits=edits, state_dir=ctx.state_dir))
        changes = library.preview_changes(shown, ctx.root, shown_value)
        changes["cover"] = cover_change(folder, new_cover) if new_cover else None
        return album_view(rel, folder, form=request.form, preview=changes, new_cover=new_cover)

    def sent_cover():
        """The upload name of the cover the album form sends: its image, converted to JPEG and kept,
        else the one a preview kept; None for none. Raises cover.NotAnImage."""
        if ctx.requests is None:
            return None
        image = request.files.get("cover_image")
        data = image.read() if image else b""
        if data:
            return keep_upload(ctx.state_dir, cover.as_jpeg(data)[0])
        name = request.form.get("cover", "")
        return name if upload_path(ctx.state_dir, name) else None

    def cover_change(folder, name):
        """(the album's cover, the one sent) as their sizes show them."""
        def size(data):
            dims = artwork.image_size(data)
            return cover.dimensions(dims).replace("x", " × ") if dims else texts()["value_none"]
        previous = cover.present(folder)
        return (size(previous.read_bytes()) if previous else texts()["value_none"],
                size(upload_path(ctx.state_dir, name).read_bytes()))

    @app.get("/library/upload/<name>")
    def uploaded_cover(name):
        path = upload_path(ctx.state_dir, name)
        if path is None:
            abort(404)
        return send_from_directory(path.parent, path.name, mimetype="image/jpeg")

    @app.errorhandler(413)
    def too_large(_error):
        return refused("cover_too_large", 413, mb=MAX_UPLOAD // 1024 // 1024)

    @app.post("/library/track/lyrics/lookup")
    def look_up_lyrics():
        """What the providers have for one track of an album's page, for its lyrics box:
        {"text": the lyrics, the INSTRUMENTAL mark or null, "message": what was found}."""
        path = library.track_file(ctx.root, request.form.get("folder", ""), request.form.get("file", ""))
        if path is None:
            abort(404)
        if ctx.requests is None:
            abort(403)
        text, source = enrich_lyrics.look_up_track(path, ctx.state_dir)
        t = texts()
        if text is None:
            message = t.get(f"lookup_{source}".replace(" ", "_"), t["lookup_none"])
        else:
            key = "lookup_instrumental" if text == INSTRUMENTAL else "lookup_found"
            message = t[key].format(source=PROVIDERS.get(source, source))
        return jsonify(text=text, message=message)

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
