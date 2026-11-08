"""
cover — puts an image as an album's folder.jpg, then embeds it into the album's tracks.

  music-librarian cover FOLDER IMAGE            # dry run
  music-librarian cover "Beyonce/4" ~/4.jpg --apply

FOLDER is an album folder of the library, IMAGE an image file, copied: it is left where it is.
An image in another format than JPEG (PNG, WebP, HEIC…) is converted to JPEG with ffmpeg.
The folder.jpg it replaces is kept in the state folder (covers/), and both moves go to the
journal: `music-librarian undo` puts the previous folder.jpg back, which the next enrich
embeds again. The dashboard's album form puts its covers in place through `tag`, the same way.

Exit codes: 0 = OK, 2 = refused or failed, 3 = another pass is running, 4 = stopped before
the end.
"""
import secrets
import shlex
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from music_librarian.core import arguments
from music_librarian.core.files import relative
from music_librarian.core.journal import Journal, move_file
from music_librarian.core.lock import EXIT_STOPPED, STOP, handle_stop_signals
from music_librarian.core.log import Log, count
from music_librarian.core.runs import recorded
from music_librarian.core.state import state_path
from music_librarian.enrich import covers
from music_librarian.importer import COVER
from music_librarian.media import artwork, tags, tool
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.split.library import run_locked

KEPT = "covers"


@dataclass
class Options:
    root: Path
    folder: Path
    image: Path
    apply: bool = False
    state_dir: Path = field(default_factory=state_path)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.folder = (self.root / self.folder).resolve()
        self.image = Path(self.image).resolve()


def tracks_of(folder: Path):
    return [str(p) for p in sorted(folder.iterdir())
            if p.is_file() and tags.is_music_file(p.name) and not p.name.startswith(".")]


def present(folder: Path):
    """The album's folder.jpg, whatever the case of its name, or None."""
    return next((p for p in folder.iterdir() if p.name.lower() == COVER and p.is_file()), None)


def dimensions(dims):
    return f"{dims[1]}x{dims[2]}"


class NotAnImage(Exception):
    """Bytes ffmpeg cannot read as an image."""


def as_jpeg(data):
    """(JPEG bytes, artwork.image_size() of them) of an image in any format ffmpeg reads, the
    bytes as they are for a JPEG. Raises NotAnImage."""
    dims = artwork.image_size(data)
    if dims and dims[0] == "jpeg":
        return data, dims
    with tempfile.TemporaryDirectory() as work:
        source, target = Path(work) / "image", Path(work) / "cover.jpg"
        source.write_bytes(data)
        done = tool.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(source), "-frames:v", "1",
                         "-pix_fmt", "yuvj420p", "-q:v", "2", str(target)], tool.PROBE_SECONDS)
        converted = target.read_bytes() if done.returncode == 0 and target.exists() else b""
    dims = artwork.image_size(converted)
    if not dims:
        raise NotAnImage(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "not an image")
    return converted, dims


def read_image(image: Path, log):
    """(JPEG bytes, artwork.image_size() of them) of the image file, or None once refused."""
    try:
        data = image.read_bytes()
    except OSError as e:
        log(f"REFUSED  (cannot read the image: {e.strerror})  {image}")
        return None
    try:
        jpeg, dims = as_jpeg(data)
    except (NotAnImage, OSError) as e:
        log(f"REFUSED  (not an image: {e})  {image}")
        return None
    if jpeg is not data:
        log(f"CONVERT  {image.name} to JPEG")
    return jpeg, dims


def put_in_place(folder, data, previous, state_dir, journal):
    """The image moved in as folder.jpg, the previous one moved out to the state folder."""
    kept = state_dir / KEPT
    kept.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    staged = kept / f"{stamp}.jpg"
    staged.write_bytes(data)
    if previous is not None:
        move_file(previous, kept / f"{stamp}-previous.jpg", journal)
    move_file(staged, folder / COVER, journal)


@dataclass
class Placing:
    """Where an image goes and what it replaces: the album `folder` and its `files`, the image's
    JPEG `data` and `dims`, the folder.jpg it replaces (`previous`) and the tracks to embed it into."""
    folder: Path
    files: list
    data: bytes
    dims: tuple
    previous: Path | None
    outdated: list


def planned(folder: Path, image: Path, rel, state_dir, log):
    """The Placing of the image file in the album folder, logged as the dry run shows it, or None
    once refused."""
    files = tracks_of(folder) if folder.is_dir() else []
    if not files:
        log(f"REFUSED  (not an album folder of the library)  {rel}")
        return None
    image = read_image(image, log)
    if image is None:
        return None
    data, dims = image
    previous = present(folder)
    if previous is not None and previous.read_bytes() == data:
        log(f"SKIPPED  ({COVER} already holds this image)  {rel}")
    else:
        old = artwork.image_size(previous.read_bytes()) if previous else None
        was = f"replacing {dimensions(old) if old else 'an unreadable image'}" if previous else "new"
        log(f"COVER    {COVER}  {dimensions(dims)}, {was}  {rel}")
    with MetadataCache(state_dir) as cache:
        outdated = covers.stale(files, data, cache)
    return Placing(folder, files, data, dims, previous, outdated)


def would(placing, log):
    if placing.outdated:
        log(f"WOULD    embed {dimensions(placing.dims)} into {len(placing.outdated)} of "
            f"{count(len(placing.files), 'track')}")


def place(placing, state_dir, journal, log):
    """The image put as folder.jpg (journaled) and embedded into the tracks that need it: the
    number of tracks it failed to be embedded into."""
    if placing.previous is None or placing.previous.read_bytes() != placing.data:
        put_in_place(placing.folder, placing.data, placing.previous, state_dir, journal)
    outdated = placing.outdated
    failures = covers.process(str(placing.folder), outdated, placing.data, placing.dims, log) \
        if outdated else []
    with MetadataCache(state_dir) as cache:
        for path in outdated:
            cache.snapshot(path)
    return len(failures)


def cover_pass(opts: Options, log):
    """A cover pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    rel = relative(opts.folder, opts.root)
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] cover of {rel}")
    if not opts.folder.is_relative_to(opts.root):
        log(f"REFUSED  (not an album folder of the library)  {rel}")
        return 2
    placing = planned(opts.folder, opts.image, rel, opts.state_dir, log)
    if placing is None:
        return 2
    stats = {"tracks": len(placing.files)}
    journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    stopped = failed = False
    if not opts.apply:
        would(placing, log)
        stats["to embed"] = len(placing.outdated)
    elif STOP.is_set():
        log("STOP     requested: the cover is left as it was")
        stopped = True
    else:
        failures = place(placing, opts.state_dir, journal, log)
        stats["embedded"] = len(placing.outdated) - failures
        stats["failed"] = failures
        failed = bool(failures)
    journal.close()
    log("\n--- Cover summary ---")
    for key, value in stats.items():
        if value:
            log(f"{key:>20}: {value}")
    if journal.path:
        log(f"{'journal':>20}: {journal.path}")
        log(f"{'undo':>20}: music-librarian undo {shlex.quote(str(journal.path))} "
            f"--root {shlex.quote(str(opts.root))} --apply")
    return EXIT_STOPPED if stopped else 2 if failed else 0


def run(opts: Options, log):
    """A cover pass, under the lock with `apply`."""
    return run_locked(cover_pass, opts, log)


def main(argv=None):
    ap = arguments.parser("cover", "Put an image as an album's folder.jpg, then embed it into its tracks.")
    arguments.add_root(ap)
    ap.add_argument("folder", help="the album folder, relative to the root or absolute")
    ap.add_argument("image", help="the image to put as its cover, converted to JPEG when in another format")
    arguments.add_apply(ap, "write the cover")
    arguments.add_state_dir(ap)
    arguments.add_syslog(ap)
    args = ap.parse_args(argv)
    opts = Options(root=args.root, folder=Path(args.folder), image=args.image,
                   apply=args.apply, state_dir=state_path(args.state_dir))
    log = Log(args.syslog)
    return recorded(lambda: run(opts, log), log, opts.state_dir, "cover", opts.apply)


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
