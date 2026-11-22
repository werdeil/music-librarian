"""Where a download is written: a hidden folder of the Inbox, which import does not list, each album
moved out of it into the Inbox once complete, by a rename on the same file system."""
import re
import secrets
import shutil
from pathlib import Path

from music_librarian.core.files import drop_abandoned
from music_librarian.importer import free_name

PREFIX = ".deezer-"
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean(name, fallback="Unknown"):
    """`name` without the characters a folder or file name cannot hold, nor a leading dot."""
    return UNSAFE.sub("-", name).strip().lstrip(".").strip() or fallback


class Staging:
    """The hidden folder of one command run, made on first use. Use as a context manager: what is
    left in it (an album interrupted) is removed on exit."""

    def __init__(self, inbox: Path):
        self.inbox = inbox
        self.folder = None

    def __enter__(self):
        drop_abandoned(self.inbox, PREFIX)
        return self

    def __exit__(self, *exc):
        self.close()

    def album(self, name):
        """The folder of the album `name` in waiting, made if needed."""
        if self.folder is None:
            self.inbox.mkdir(parents=True, exist_ok=True)
            self.folder = self.inbox / f"{PREFIX}{secrets.token_hex(8)}"
            self.folder.mkdir()
        path = self.folder / clean(name)
        path.mkdir(exist_ok=True)
        return path

    def publish(self, path: Path):
        """Move an album in waiting into the Inbox, named `name (2)` when one is already there;
        return where it went."""
        target = free_name(self.inbox, path.name)
        path.rename(target)
        return target

    @staticmethod
    def discard(path: Path):
        shutil.rmtree(path, ignore_errors=True)

    def close(self):
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = None
