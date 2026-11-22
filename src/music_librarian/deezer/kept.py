"""What the Deezer download keeps in the state folder, each file readable by its owner only: the arl
set from the dashboard, which stands for DEEZER_ARL, the license token read from an arl, the mark of an
arl Deezer refused, and the Chrome extension's key. The token and the mark hold a digest of their arl,
and stand for it alone."""
import hashlib
import os
import secrets
import tempfile
import time
from pathlib import Path

ARL, TOKEN, REFUSED, KEY = "deezer-arl", "deezer-license-token", "deezer-arl-refused", "deezer-extension-key"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _read(state_dir, name):
    try:
        return (Path(state_dir) / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _write(state_dir, name, text):
    """Replace the file `name` whole, mode 600. Raises OSError."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=state_dir, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temporary, state_dir / name)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def arl(state_dir):
    """The arl set from the dashboard, "" without one."""
    return _read(state_dir, ARL).strip()


def save_arl(state_dir, value):
    """Keep `value` as the arl, its refusal forgotten. Raises OSError."""
    _write(state_dir, ARL, value)
    (Path(state_dir) / REFUSED).unlink(missing_ok=True)


def token(state_dir, of_arl):
    """The license token kept for `of_arl`, "" without one."""
    owner, _, value = _read(state_dir, TOKEN).partition("\n")
    return value.strip() if owner == _digest(of_arl) else ""


def save_token(state_dir, of_arl, value):
    """Keep `value` as the license token of `of_arl`. Raises OSError."""
    _write(state_dir, TOKEN, f"{_digest(of_arl)}\n{value}")


def refused(state_dir, of_arl):
    """When Deezer refused `of_arl` (a timestamp), None when it did not or another arl replaced it."""
    owner, _, when = _read(state_dir, REFUSED).partition("\n")
    try:
        return float(when) if owner == _digest(of_arl) else None
    except ValueError:
        return None


def mark_refused(state_dir, of_arl):
    try:
        _write(state_dir, REFUSED, f"{_digest(of_arl)}\n{time.time()}")
    except OSError:
        pass


def forget_refused(state_dir):
    (Path(state_dir) / REFUSED).unlink(missing_ok=True)


def extension_key(state_dir):
    """The key the Chrome extension sends, "" before one is made."""
    return _read(state_dir, KEY).strip()


def new_extension_key(state_dir):
    """Make a key for the Chrome extension, replacing the one before; return it. Raises OSError."""
    value = secrets.token_hex(32)
    _write(state_dir, KEY, value)
    return value
