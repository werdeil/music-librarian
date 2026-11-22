"""
deezer — download albums and playlists from Deezer into the Inbox, see docs/deezer.md.

The tracks are written to a hidden folder of the Inbox, then each album is moved into the Inbox once
complete, for import to file it as any drop. A dry run reads Deezer's metadata only: it asks for no
stream and downloads nothing. Off until DEEZER_ARL, DEEZER_PRE_KEY and DEEZER_IV are set.

  music-librarian deezer album 302127                       # what would be downloaded
  music-librarian deezer album https://www.deezer.com/fr/album/302127 --apply
  music-librarian deezer playlist 908622995 --apply --complete-only

Exit codes: 0 = OK, 1 = failed (not configured, an album or a playlist not found or not one
track downloaded), 2 = tracks unavailable, 4 = stopped before the end.
"""
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian.core import arguments
from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.core.log import Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import state_path
from music_librarian.deezer import config, download, tags
from music_librarian.deezer.api import FORMATS, Album, Api, disc_total
from music_librarian.deezer.client import Client, DeezerError
from music_librarian.deezer.staging import Staging, clean

EXIT_FAILED, EXIT_MISSING = 1, 2
KINDS = ("album", "playlist")
URL = re.compile(r"^(?:https?://)?(?:www\.)?deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(album|playlist)/(\d+)"
                 r"(?:[/?#].*)?$", re.IGNORECASE)
SHORT_URL = re.compile(r"^(?:https?://)?(?:link\.deezer\.com|deezer\.page\.link)/", re.IGNORECASE)


@dataclass
class Options:  # pylint: disable=too-many-instance-attributes  # one per command-line option
    kind: str
    ids: list
    inbox: Path
    apply: bool = False
    complete_only: bool = False
    state_dir: Path = field(default_factory=state_path)
    syslog: bool = False
    license_token: str = field(default="", repr=False)

    def __post_init__(self):
        self.inbox = Path(self.inbox).resolve()


@dataclass
class Tally:
    albums: int = 0
    tracks: int = 0
    failed: int = 0
    unavailable: list = field(default_factory=list)
    formats: Counter = field(default_factory=Counter)


def item_id(kind, value):
    """The id of an album or a playlist given as its id or its deezer.com address. Raises ValueError."""
    value = value.strip()
    if value.isdigit():
        return value
    if SHORT_URL.match(value):
        raise ValueError(f"{value}: short links are not followed, open it and give the deezer.com address")
    found = URL.match(value)
    if not found:
        raise ValueError(f"{value}: neither an id nor a deezer.com address")
    if found[1].lower() != kind:
        raise ValueError(f"{value}: the address of {_a(found[1].lower())}, not of {_a(kind)}")
    return found[2]


def _a(kind):
    return "an album" if kind == "album" else "a playlist"


def parser():
    ap = arguments.parser("deezer", "Download albums or playlists from Deezer into the Inbox.")
    ap.add_argument("kind", choices=KINDS, help="what the ids name")
    ap.add_argument("items", nargs="+", metavar="ID|URL", help="ids or deezer.com addresses")
    inbox_env = os.environ.get("MUSIC_LIBRARIAN_INBOX")
    ap.add_argument("--inbox", type=Path, default=inbox_env, required=not inbox_env,
                    help="drop folder (default: $MUSIC_LIBRARIAN_INBOX)")
    arguments.add_apply(ap, "download")
    ap.add_argument("--complete-only", action="store_true",
                    help="leave out an album or a playlist with any track unavailable")
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap, "music-deezer")
    return ap


def options(args, ap):
    try:
        ids = [item_id(args.kind, value) for value in args.items]
    except ValueError as exc:
        ap.error(str(exc))
    return Options(kind=args.kind, ids=ids, inbox=args.inbox, apply=args.apply,
                   complete_only=args.complete_only, state_dir=state_path(args.state_dir), syslog=args.syslog)


def main(argv=None):
    ap = parser()
    opts = options(ap.parse_args(argv), ap)
    log = Log(opts.syslog, ident="music-deezer")
    return recorded(lambda: run(opts, log), log, opts.state_dir, "deezer", opts.apply)


def run(opts: Options, log, cfg=None, client=None):
    """A download pass; `cfg` and `client` stand for the environment's and Deezer's. Not locked: it
    only writes to a hidden folder of the Inbox, then moves into it."""
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] inbox: {opts.inbox}")
    if cfg is None:
        if not config.enabled():
            log(f"ERROR    the Deezer download is off: set {', '.join(config.REQUIRED)} (see docs/deezer.md)")
            return EXIT_FAILED
        try:
            cfg = config.load(license_token=opts.license_token)
        except config.ConfigError as exc:
            log(f"ERROR    {exc}")
            return EXIT_FAILED
    api = Api(client or Client(cfg.arl), cfg.license_token)
    tally = Tally()
    with Staging(opts.inbox) if opts.apply else _NoStaging() as staging:
        for item in opts.ids:
            if STOP.is_set():
                break
            pass_one = _album if opts.kind == "album" else _playlist
            try:
                pass_one(opts, api, cfg, staging, item, tally, log)
            except DeezerError as exc:
                log(f"ERROR    {opts.kind} {item}: {exc}")
                tally.failed += 1
    return _summary(opts, tally, log)


class _NoStaging:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        pass


def _album(opts, api, cfg, staging, album_id, tally, log):
    album = api.album(album_id)
    tracks = api.album_tracks(album_id)
    name = f"{album.artist} - {album.title}"
    if not opts.apply:
        log(f"PLAN     {name} ({count(len(tracks), 'track')}), best of {'/'.join(FORMATS)}")
        tally.albums += 1
        tally.tracks += len(tracks)
        return
    log(f"ALBUM    {name} ({count(len(tracks), 'track')})")
    folder = staging.album(name)
    missing = []
    for track in tracks:
        if STOP.is_set():
            return
        if not _track(api, cfg, folder, track, album, tracks, tally, log):
            missing.append(tags.title(track))
            if opts.complete_only:
                break
    _publish(opts, staging, [folder], name, missing, tally, log)


def _playlist(opts, api, cfg, staging, playlist_id, tally, log):
    playlist = api.playlist(playlist_id)
    name = playlist.title or playlist_id
    if not opts.apply:
        tracks = count(len(playlist.track_ids), "track")
        log(f"PLAN     playlist {name} ({tracks}), best of {'/'.join(FORMATS)}")
        tally.tracks += len(playlist.track_ids)
        return
    log(f"PLAYLIST {name} ({count(len(playlist.track_ids), 'track')})")
    albums, folders, missing = {}, {}, []
    for track_id in playlist.track_ids:
        if STOP.is_set():
            return
        try:
            track = api.track(track_id)
        except DeezerError as exc:
            log(f"SKIPPED  track {track_id}: {exc}")
            missing.append(f"track {track_id}")
            if opts.complete_only:
                break
            continue
        if track.album_id not in albums:
            try:
                albums[track.album_id] = api.album(track.album_id)
            except DeezerError as exc:
                log(f"WARNING  album of {tags.title(track)}: {exc}, its tags left incomplete")
                albums[track.album_id] = Album(track.album_id, title=track.album)
        album = albums[track.album_id]
        folder_name = f"{album.artist or ', '.join(track.artists)} - {album.title or track.album}"
        folder = folders.setdefault(track.album_id, staging.album(folder_name))
        if not _track(api, cfg, folder, track, album, (), tally, log):
            missing.append(tags.title(track))
            if opts.complete_only:
                break
    _publish(opts, staging, list(folders.values()), f"playlist {name}", missing, tally, log)


def _publish(opts, staging, folders, name, missing, tally, log):
    """Move the folders of an album or a playlist into the Inbox, unless nothing was downloaded, or
    something is missing under --complete-only."""
    tally.unavailable += missing
    downloaded = [f for f in folders if any(p.suffix in (".flac", ".mp3") for p in f.iterdir())]
    if not downloaded:
        log(f"ERROR    {name}: not one track downloaded")
        tally.failed += 1
    elif missing and opts.complete_only:
        log(f"SKIPPED  {name}: {count(len(missing), 'track')} unavailable, left out (--complete-only)")
        downloaded = []
    for folder in folders:
        if folder in downloaded:
            log(f"ADDED    {staging.publish(folder).name}")
            tally.albums += 1
        else:
            staging.discard(folder)


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _track(api, cfg, folder, track, album, tracks, tally, log):
    """Download one track into `folder` with its tags and cover; False when it is unavailable."""
    title = tags.title(track)
    try:
        stream = api.stream(track)
    except DeezerError as exc:
        log(f"SKIPPED  {title}: {exc}")
        return False
    if stream.fallback:
        log(f"FALLBACK {title}: no rights of its own, recording {stream.song_id} downloaded instead")
    cover = folder / "folder.jpg"
    if not cover.exists() and tags.cover_url(album, track):
        try:
            download.cover(api.client, tags.cover_url(album, track), cover)
        except DeezerError as exc:
            log(f"WARNING  cover of {album.title or track.album}: {exc}")
    ext = "flac" if stream.format.upper().startswith("FLAC") else "mp3"
    number = int(track.track_number) if track.track_number.isdigit() else 0
    disc = f"{track.disc_number}-" if disc_total(album, tracks) > 1 and track.disc_number else ""
    path = folder / f"{disc}{number:02d} - {clean(title, 'Untitled')}.{ext}"
    try:
        download.fetch(api.client, stream.url, stream.song_id, path, cfg)
    except DeezerError as exc:
        log(f"SKIPPED  {title}: {exc}")
        return False
    quality = stream.format
    if ext == "flac":
        try:
            quality = f"{stream.format} {download.resolution(path)}"
        except Exception as exc:
            log(f"WARNING  {title}: resolution unreadable ({exc})")
    try:
        tags.write(path, tags.fields(track, album, tracks))
        if cover.exists():
            tags.embed_cover(path, cover.read_bytes())
    except tags.TagWriteError as exc:
        log(f"WARNING  {title}: tags not written ({exc})")
    log(f"DOWNLOAD {title} as {quality}")
    tally.tracks += 1
    tally.formats[stream.format.upper()] += 1
    return True


def _summary(opts, tally, log):
    stopped = STOP.is_set()
    log(f"\n--- Deezer summary{' (stopped)' if stopped else ''} ---")
    formats = ", ".join(f"{n} {name}" for name, n in tally.formats.most_common())
    unavailable = (f"{count(len(tally.unavailable), 'track')} ({', '.join(tally.unavailable)})"
                   if tally.unavailable else "")
    rows = (("albums" if opts.apply else "albums to download", tally.albums),
            ("tracks" if opts.apply else "tracks to download", tally.tracks),
            ("unavailable", unavailable), ("formats", formats), ("failed", tally.failed))
    for key, value in rows:
        if value:
            log(f"{key:>20}: {value}")
    if stopped:
        return EXIT_STOPPED
    if tally.failed:
        return EXIT_FAILED
    return EXIT_MISSING if tally.unavailable else 0
