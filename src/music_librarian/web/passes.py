"""The passes asked for from the dashboard, queued for the watch loop to run one at a time.

The dashboard's thread only submits; the loop of watch runs each request between its other
jobs, so a requested pass never competes with them for the lock.
"""
import argparse
import itertools
import queue
import re
import threading
from dataclasses import dataclass, field, replace

from music_librarian import dedupe, enrich, importer, organize, scan, split, tag
from music_librarian.core.lock import STOP, resume
from music_librarian.core.runs import recorded

QUEUED, STARTED, DONE, CANCELLED = "queued", "started", "done", "cancelled"
KEPT = 50
JOURNAL_NAME = re.compile(r"^[\w.-]+\.jsonl$")

# the options each command takes from the dashboard: booleans, but undo's journal, tag's edits
# ([[path relative to the root, field, value, values seen]]) and the lyrics' album folder
OPTIONS = {
    "import": (),
    "organize": ("since",),
    "enrich": ("rescan", "no_split", "no_dedupe"),
    "scan": ("force",),
    "split": (),
    "dedupe": (),
    "undo": ("journal",),
    "tag": ("edits",),
    "lyrics": ("folder",),
    "genres": (),
}
FORMS = {command: names for command, names in OPTIONS.items() if command not in ("undo", "tag", "lyrics")}


@dataclass
class Request:
    """A pass asked for: `run_id` is set once it is recorded, `code` once it ends."""
    id: int
    command: str
    options: dict = field(default_factory=dict)
    apply: bool = False
    state: str = QUEUED
    run_id: int | None = None
    code: int | None = None


class Requests:
    """The queue between the dashboard and watch, a job of its loop. `runners` maps each command
    to fn(request, log) -> exit code; `guard(fn, log, what)` runs one, logging a crash."""

    def __init__(self, runners, guard):
        self.runners, self.guard = runners, guard
        self.queue = queue.Queue()
        self.wake = threading.Event()
        self.ids = itertools.count(1)
        self.known = {}
        self.lock = threading.Lock()

    def submit(self, command, options, apply):
        request = Request(next(self.ids), command, dict(options), apply)
        self.known[request.id] = request
        for old in list(self.known)[:-KEPT]:
            del self.known[old]
        self.queue.put(request)
        self.wake.set()
        return request

    def get(self, request_id):
        return self.known.get(request_id)

    def pending(self):
        return [r for r in self.known.values() if r.state == QUEUED]

    def cancel(self, request_id):
        """Take a request out of the queue before it starts; False once it started."""
        with self.lock:
            request = self.known.get(request_id)
            if request is None or request.state != QUEUED:
                return False
            request.state = CANCELLED
            return True

    def tick(self, log):
        self.wake.clear()
        while not STOP.is_set():
            try:
                request = self.queue.get_nowait()
            except queue.Empty:
                return
            with self.lock:
                if request.state == CANCELLED:
                    continue
                request.state = STARTED
            request.code = self.guard(lambda r=request: self.runners[r.command](r, log), log, request.command)
            request.state = DONE
            log("")
            resume()


def journal_path(state_dir, name):
    """The journal of that file name in the state folder, or None for any other name."""
    path = state_dir / "journal" / name
    return path if JOURNAL_NAME.match(name) and path.is_file() else None


def passes(import_opts, organize_opts, enrich_opts, import_log):
    """{command: fn(request, log) -> exit code}, each pass built from the options of watch,
    kept in the run history as started from the dashboard."""
    state_dir, root = import_opts.state_dir, import_opts.root

    def kept(command, fn, log, request):
        return recorded(fn, log, state_dir, command, request.apply, origin="web", request=request)

    def run_import(r, _log):
        opts = replace(import_opts, apply=r.apply)
        return kept("import", lambda: importer.run(opts, import_log), import_log, r)

    def run_organize(r, log):
        opts = replace(organize_opts, apply=r.apply, since=bool(r.options.get("since")))
        return kept("organize", lambda: organize.run(opts, log).code, log, r)

    def run_enrich(r, log):
        opts = replace(enrich_opts, apply=r.apply, rescan=bool(r.options.get("rescan")),
                       split=not r.options.get("no_split"), dedupe=not r.options.get("no_dedupe"))
        return kept("enrich", lambda: enrich.run(opts, log), log, r)

    def run_scan(r, log):
        return kept("scan", lambda: scan.run(root, state_dir, log, bool(r.options.get("force"))), log, r)

    def credits_pass(command, module):
        def run_pass(r, log):
            opts = module.Options(root=root, apply=r.apply, state_dir=state_dir)
            return kept(command, lambda: module.run(opts, log), log, r)
        return run_pass

    def run_undo(r, log):
        def undo_journal():
            journal = journal_path(state_dir, r.options.get("journal", ""))
            if journal is None:
                log(f"ERROR    no journal {r.options.get('journal')!r} in {state_dir / 'journal'}")
                return 2
            args = argparse.Namespace(undo=str(journal), lyrion_replay=None, apply=r.apply)
            return organize.replay(args, replace(organize_opts, apply=r.apply), log)
        return kept("undo", undo_journal, log, r)

    def run_tag(r, log):
        edits = [tag.Edit(root / rel, name, value, seen) for rel, name, value, seen in r.options["edits"]]
        opts = tag.Options(root=root, edits=edits, apply=r.apply, state_dir=state_dir,
                           lyrion=import_opts.lyrion)
        return kept("tag", lambda: tag.run(opts, log), log, r)

    def run_lyrics(r, log):
        def look_up():
            folder = (root / r.options.get("folder", "")).resolve()
            if not folder.is_relative_to(root) or not folder.is_dir():
                log(f"ERROR    not a folder of the library: {r.options.get('folder')!r}")
                return 2
            opts = replace(enrich_opts, apply=r.apply)
            return enrich.tool_pass("lyrics", [folder], opts, log, "--retry")
        return kept("lyrics", look_up, log, r)

    def run_genres(r, log):
        opts = replace(enrich_opts, apply=r.apply)
        return kept("genres", lambda: enrich.tool_pass("genres", [root], opts, log), log, r)

    return {"import": run_import, "organize": run_organize, "enrich": run_enrich, "scan": run_scan,
            "split": credits_pass("split", split), "dedupe": credits_pass("dedupe", dedupe),
            "undo": run_undo, "tag": run_tag, "lyrics": run_lyrics, "genres": run_genres}
