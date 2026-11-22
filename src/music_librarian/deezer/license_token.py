"""The license token get_url asks for, read from the account's arl and kept in the state folder, readable
by its owner only, so that a pass reuses it until Deezer calls it expired."""
import os
from pathlib import Path

from music_librarian.deezer.api import user_license_token

FILE = "deezer-license-token"


def read(state_dir: Path):
    """The token kept in the state folder, "" without one."""
    try:
        return (Path(state_dir) / FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save(state_dir: Path, token):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    temporary = state_dir / f".{FILE}.tmp"
    temporary.unlink(missing_ok=True)
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w",
                   encoding="utf-8") as out:
        out.write(token)
    temporary.replace(state_dir / FILE)


def fresh(client, state_dir: Path):
    """A new token from the arl `client` sends, kept in the state folder. Raises DeezerError."""
    token = user_license_token(client)
    save(state_dir, token)
    return token
