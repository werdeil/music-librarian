"""
cover — puts an image as an album's folder.jpg, then embeds it into the album's tracks.

  music-librarian cover FOLDER IMAGE            # dry run
  music-librarian cover "Beyonce/4" ~/4.jpg --apply

FOLDER is an album folder of the library, IMAGE a JPEG file, copied: it is left where it is.
The folder.jpg it replaces is kept in the state folder (covers/), and both moves go to the
journal: `music-librarian undo` puts the previous folder.jpg back, which the next enrich
embeds again. The dashboard uploads its covers through it.

Exit codes: 0 = OK, 2 = refused or failed, 3 = another pass is running, 4 = stopped before
the end.
"""
import secrets
import shlex
import sys
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
from music_librarian.media import artwork, tags
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


def read_image(opts, log):
    """(bytes, artwork.image_size()) of the image, or None once refused."""
    try:
        data = opts.image.read_bytes()
    except OSError as e:
        log(f"REFUSED  (cannot read the image: {e.strerror})  {opts.image}")
        return None
    dims = artwork.image_size(data)
    if not dims or dims[0] != "jpeg":
        log(f"REFUSED  (not a JPEG image)  {opts.image}")
        return None
    return data, dims


def put_in_place(opts, data, previous, journal):
    """The image moved in as folder.jpg, the previous one moved out to the state folder."""
    kept = opts.state_dir / KEPT
    kept.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    staged = kept / f"{stamp}.jpg"
    staged.write_bytes(data)
    if previous is not None:
        move_file(previous, kept / f"{stamp}-previous.jpg", journal)
    move_file(staged, opts.folder / COVER, journal)


def cover_pass(opts: Options, log):
    """A cover pass, without taking the lock: the caller holds it when `opts.apply`."""
    started = time.time()
    rel = relative(opts.folder, opts.root)
    log(f"[{'APPLY' if opts.apply else 'DRY-RUN'}] cover of {rel}")
    files = tracks_of(opts.folder) if opts.folder.is_relative_to(opts.root) and opts.folder.is_dir() else []
    if not files:
        log(f"REFUSED  (not an album folder of the library)  {rel}")
        return 2
    image = read_image(opts, log)
    if image is None:
        return 2
    data, dims = image
    previous = present(opts.folder)
    old = artwork.image_size(previous.read_bytes()) if previous else None
    same = previous is not None and previous.read_bytes() == data
    stats = {"tracks": len(files)}
    if same:
        log(f"SKIPPED  ({COVER} already holds this image)  {rel}")
    else:
        was = f"replacing {dimensions(old) if old else 'an unreadable image'}" if previous else "new"
        log(f"COVER    {COVER}  {dimensions(dims)}, {was}  {rel}")
    with MetadataCache(opts.state_dir) as cache:
        outdated = covers.stale(files, data, cache)
    journal = Journal(opts.state_dir, dict(root=str(opts.root), started=started, argv=sys.argv[1:]))
    stopped = failed = False
    if not opts.apply:
        if outdated:
            log(f"WOULD    embed {dimensions(dims)} into {len(outdated)} of {count(len(files), 'track')}")
        stats["to embed"] = len(outdated)
    elif STOP.is_set():
        log("STOP     requested: the cover is left as it was")
        stopped = True
    else:
        if not same:
            put_in_place(opts, data, previous, journal)
        failures = covers.process(str(opts.folder), outdated, data, dims, log) if outdated else []
        with MetadataCache(opts.state_dir) as cache:
            for path in outdated:
                cache.snapshot(path)
        stats["embedded"] = len(outdated) - len(failures)
        stats["failed"] = len(failures)
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
    ap.add_argument("image", help="the JPEG image to put as its cover")
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
