"""The command-line arguments the commands share."""
import argparse
import os


def parser(command, description):
    return argparse.ArgumentParser(prog=f"music-librarian {command}", description=description)


def add_root(ap, **kwargs):
    root_env = os.environ.get("MUSIC_LIBRARIAN_ROOT")
    ap.add_argument("--root", default=root_env, required=not root_env,
                    help="library root (default: $MUSIC_LIBRARIAN_ROOT)", **kwargs)


def add_apply(ap, what="do it"):
    ap.add_argument("--apply", action="store_true", help=f"{what} (otherwise dry run)")


def add_state_dir(ap):
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_LIBRARIAN_STATE"),
                    help="state folder: journals, run history, metadata cache, decisions "
                         "(default: $MUSIC_LIBRARIAN_STATE, else ~/.local/state/music-librarian)")


def add_syslog(ap, tag="music-librarian"):
    ap.add_argument("--syslog", action="store_true",
                    help=f"also send the lines to syslog/journald (tag {tag})")
