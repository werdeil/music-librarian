"""The license token get_url asks for, read from the account's arl and kept in the state folder, readable
by its owner only, so that a pass reuses it until Deezer calls it expired. It is kept with a digest of
the arl it came from, and set aside once the arl changes."""
import hashlib
import os
import tempfile
from pathlib import Path

FILE = "deezer-license-token"


def _owner(arl):
    return hashlib.sha256(arl.encode()).hexdigest()[:16]


def read(state_dir: Path, arl):
    """The token kept for `arl`, "" without one."""
    try:
        owner, _, token = (Path(state_dir) / FILE).read_text(encoding="utf-8").partition("\n")
    except OSError:
        return ""
    return token.strip() if owner == _owner(arl) else ""


def save(state_dir: Path, arl, token):
    """Keep `token` for `arl`, replacing the file whole. Raises OSError."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=state_dir, prefix=f".{FILE}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(f"{_owner(arl)}\n{token}")
        os.replace(temporary, state_dir / FILE)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
