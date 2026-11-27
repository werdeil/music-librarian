"""The library's pages: the albums and tracks of the metadata cache, searched field by field, those
without lyrics or with a field holding a value twice, an album's page with its tracks' lyrics and
its cover, and the forms that queue their edits."""
import json
from urllib.parse import parse_qsl, urlencode

from flask import abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.datastructures import MultiDict

from music_librarian import cover, editions, repeats, tag
from music_librarian.core import runs
from music_librarian.core.files import AUDIO_EXT, is_junk
from music_librarian.core.lock import EXIT_LOCKED
from music_librarian.enrich import lyrics as enrich_lyrics
from music_librarian.lyrics import INSTRUMENTAL, awaited
from music_librarian.media import artwork, fields, metadata_cache, tags
from music_librarian.split.rules import names_of
from music_librarian.tag import genre_list
from music_librarian.web import history, library, physical, query
from music_librarian.web.common import WORK_LISTS, paged, place, refused, submit, texts, timed
from music_librarian.web.passes import CANCELLED, DONE, keep_upload, upload_path

MAX_UPLOAD = 32 * 1024 * 1024
AHEAD = 2   # albums of a work list read ahead of their page
PROVIDERS = {"lrclib": "LRCLIB", "netease": "NetEase", "musixmatch": "Musixmatch"}


def register(app, ctx, index, later):
    """Add the library's pages to the app, the albums read from `index` (a library.Index), the
    albums put off in the list of tracks without lyrics kept in `later` (a reviews.Postponed)."""
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
                               columns=shown["search"].columns(), cell=query.cell, **shown)

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
                    progress=history.progress(ctx.state_dir, run),
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
        if name == "mixed":
            return library.with_mixed(everything)
        if name == "dates":
            return library.with_dates(everything)
        return library.without_lyrics(everything, later.times("lyrics"))

    def warm(*rels):
        library.WARMER.ask(*filter(None, (library.album_folder(ctx.root, r) for r in rels)))

    def in_work_list(rel, sorted_out=False):
        """Where the album stands in the work list it was opened from, with its neighbours and its
        number, in the lyrics list counted from the first album not put off in the round under way;
        for an album the list no longer holds (fixed, or with `sorted_out` its page holding no track
        to sort out, whatever the album list says), its neighbours where it stood. None without one."""
        where = place()
        albums = work_list(where["list"]) if where else None
        if albums is None:
            return None
        folders = [a.folder for a in albums
                   if not (sorted_out and where["list"] == "lyrics" and a.folder == rel)]
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
        ahead = dict.fromkeys(folders[(after + i) % len(folders)] for i in range(AHEAD)) if folders else {}
        warm(*[f for f in ahead if f != rel])
        seen = later.seen("lyrics", folders) if where["list"] == "lyrics" and at is not None else 0
        return dict(name=where["list"], page=page, position=at,
                    number=None if at is None else (seen + at) % len(folders) + 1, total=len(folders),
                    previous=step(folders[before], before) if before >= 0 else None,
                    next=step(folders[after], after) if after < len(folders)
                    else step(folders[0], 0) if where["list"] == "lyrics" and folders and folders[0] != rel
                    else None)

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
        with timed("files"):
            tracks = library.tracks_of(folder)
        lyrics = library.with_lyrics(tracks)
        gains = {name: values[fields.REPLAYGAIN] if values else [] for name, values in tracks}
        repeated = library.repeated_fields(tracks)
        total = library.total_length(tracks)
        mapping = genre_list(ctx.state_dir, lambda _msg: None) or {}
        with timed("list"):
            catalog = index.catalog()
            waiting = catalog.awaited.get(rel, set()) if catalog else set()
            sorted_out = not any(values and lyrics[name] is False and name not in waiting
                                 for name, values in tracks)
            counted = catalog.album(rel) if catalog else None
            if not sorted_out or (counted and counted.without_lyrics):
                index.settle(rel, sorted_out)
            found = library.reread_album(tracks)
            if counted and any(getattr(counted, name) != value for name, value in found.items()):
                index.note(rel, found)
            work = in_work_list(rel, sorted_out)
        owned = physical.album_state(tracks)
        page = render_template("album.html", folder=rel, tracks=tracks, common=library.common(tracks),
                               spread=library.spread(tracks),
                               discs=library.several_discs(tracks), groupings=library.has_groupings(tracks),
                               disc_of=library.disc_of,
                               disc_names=library.disc_names(tracks),
                               seen=library.seen_state(tracks), genres=sorted(set(mapping.values())),
                               lyrics=lyrics, mark=INSTRUMENTAL,
                               awaited=waiting,
                               unsorted=len(catalog.unsorted(rel)) if catalog and not sorted_out else 0,
                               repeated=repeated, repeated_album=library.album_repeats(repeated),
                               lengths={n: query.minutes(v[fields.LENGTH]) if v else None
                                        for n, v in tracks},
                               total=None if total is None else history.duration(total),
                               gains={n: fields.track_gain(v) or ("?" if v else None)
                                      for n, v in gains.items()},
                               followed=followed(rel), work=work, physical=owned, **shown)
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
            return render_template("lyrics.html", building=True)
        albums = library.without_lyrics(everything, later.times("lyrics"))
        shown, page, pages = paged(albums, library.PAGE)
        warm(*[a.folder for a in shown[:AHEAD]])
        return render_template("lyrics.html", albums=shown, offset=(page - 1) * library.PAGE,
                               tracks=sum(a.without_lyrics for a in albums), count=len(albums),
                               page=page, pages=pages,
                               awaited=sum(a.awaited for a in everything))

    @app.get("/library/mixed")
    def mixed_albums():
        everything = index.get()
        if everything is None:
            return render_template("mixed.html", building=True)
        albums = library.with_mixed(everything)
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("mixed.html", albums=shown, offset=(page - 1) * library.PAGE,
                               count=len(albums), page=page, pages=pages)

    @app.get("/library/dates")
    def dated_albums():
        everything = index.get()
        if everything is None:
            return render_template("dates.html", building=True)
        albums = library.with_dates(everything)
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("dates.html", albums=shown, offset=(page - 1) * library.PAGE,
                               count=len(albums), page=page, pages=pages)

    @app.get("/library/repeats")
    def repeated_values():
        everything = index.get()
        if everything is None:
            return render_template("repeats.html", building=True)
        albums = library.with_repeats(everything)
        shown, page, pages = paged(albums, library.PAGE)
        return render_template("repeats.html", albums=shown, offset=(page - 1) * library.PAGE,
                               tracks=sum(a.repeated for a in albums), count=len(albums),
                               page=page, pages=pages)

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
        """What the providers have for one track of an album's page, for its lyrics box, looked up
        with the artist, title and album typed in its form: {"text": the lyrics, the INSTRUMENTAL
        mark or null, "message": what was found}."""
        path = library.track_file(ctx.root, request.form.get("folder", ""), request.form.get("file", ""))
        if path is None:
            abort(404)
        if ctx.requests is None:
            abort(403)
        typed = {key: next(iter(names_of(request.form.get(key, ""))), None)
                 for key in ("artist", "title", "album")}
        text, source = enrich_lyrics.look_up_track(path, typed)
        t = texts()
        if text is None:
            message = t.get(f"lookup_{source}".replace(" ", "_"), t["lookup_none"])
        else:
            key = "lookup_instrumental" if text == INSTRUMENTAL else "lookup_found"
            message = t[key].format(source=PROVIDERS.get(source, source))
        return jsonify(text=text, message=message)

    @app.post("/library/album/editions")
    def look_up_editions():
        """The editions the providers know of an album of the dashboard, looked up with the album artist
        and album typed in its form, as an HTML fragment for its page. Writes nothing."""
        folder = library.album_folder(ctx.root, request.form.get("folder", ""))
        if folder is None:
            abort(404)
        if ctx.requests is None:
            abort(403)
        snapshots = [tags.read_snapshot(p) for p in sorted(folder.iterdir())
                     if p.is_file() and p.suffix.lower() in AUDIO_EXT and not is_junk(p)]
        album = editions.album_of(snapshots, {"artist": request.form.get("albumartist", ""),
                                              "title": request.form.get("album", "")})
        found, source, unreachable = editions.look_up(album)
        return render_template("_editions.html", editions=found, held=source == editions.HELD,
                               source=editions.NAMES.get(source),
                               why=None if found else source.replace(" ", "_"),
                               unreachable=", ".join(editions.NAMES[n] for n in unreachable))

    def track_meta(rel, path):
        """The track's metadata as the album list holds it, read from the file when it holds none."""
        catalog = index.catalog()
        known = [meta for t, meta in (catalog.bare if catalog else [])
                 if (t.folder, t.name) == (rel, path.name)]
        return known[0] if known else metadata_cache.metadata(tags.read_snapshot(path))

    @app.post("/library/track/lyrics/awaited")
    def await_lyrics():
        """Mark one track of an album's page as awaited, or with awaited=0 no longer;
        {"awaited": its state}."""
        rel = request.form.get("folder", "")
        path = library.track_file(ctx.root, rel, request.form.get("file", ""))
        if path is None:
            abort(404)
        if ctx.requests is None:
            abort(403)
        wanted = request.form.get("awaited") == "1"
        (awaited.mark if wanted else awaited.unmark)(ctx.state_dir, [track_meta(rel, path)])
        return jsonify(awaited=wanted)

    @app.post("/library/album/later")
    def album_later():
        """Put the album off to the end of the list of tracks without lyrics, unless it left the
        list, then step on (see step_on)."""
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        if rel in [a.folder for a in work_list("lyrics") or []]:
            later.put("lyrics", rel)
        return step_on(rel, request.form.get("at", 0, type=int))

    def step_on(rel, at):
        """Open the album of the list of tracks without lyrics now standing at `at`, where `rel`
        stood, the first one past the end; the list once none but `rel` is left."""
        folders = [a.folder for a in work_list("lyrics") or []]
        following = next((i for i in (at, 0) if i < len(folders) and folders[i] != rel), None)
        if following is None:
            return redirect(url_for("missed_lyrics"))
        return redirect(url_for("album_page", folder=folders[following], list="lyrics", at=following))

    @app.post("/library/album/awaited")
    def await_album_lyrics():
        """Mark every track of the album the album list holds without lyrics as awaited, then step
        on in the list of tracks without lyrics when the album was opened from it, else show it again."""
        rel = request.form.get("folder", "")
        if library.album_folder(ctx.root, rel) is None:
            abort(404)
        if ctx.requests is None:
            abort(403)
        catalog = index.catalog()
        if catalog is not None:
            awaited.mark(ctx.state_dir, list(catalog.unsorted(rel).values()))
        if place().get("list") == "lyrics":
            return step_on(rel, place()["at"])
        return redirect(url_for("album_page", folder=rel, **place()))

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
