"""The Deezer settings, read from the environment, the arl set from the dashboard standing for
DEEZER_ARL. A value is reported by its variable's name, never shown: every one of them is a secret."""
import os
import re
from dataclasses import dataclass, field

from music_librarian.deezer import kept

ARL, PRE_KEY, IV = "DEEZER_ARL", "DEEZER_PRE_KEY", "DEEZER_IV"
REQUIRED = (ARL, PRE_KEY, IV)


class ConfigError(Exception):
    """A variable is missing or malformed; the message names it, never its value."""


@dataclass(frozen=True)
class Config:
    arl: str = field(repr=False)
    pre_key: bytes = field(repr=False)
    iv: bytes = field(repr=False)


def _value(env, name):
    return (env.get(name) or "").strip()


def _arl(env, state_dir):
    return (kept.arl(state_dir) if state_dir is not None else "") or _value(env, ARL)


def arl(env=None, state_dir=None):
    """The arl the download goes with, "" without one; the keys are not checked."""
    return _arl(os.environ if env is None else env, state_dir)


def keys_set(env=None):
    """Whether DEEZER_PRE_KEY and DEEZER_IV are set: the download waits for an arl only."""
    env = os.environ if env is None else env
    return all(_value(env, name) for name in (PRE_KEY, IV))


def enabled(env=None, state_dir=None):
    """Whether the download is turned on: an arl (DEEZER_ARL, or the dashboard's in `state_dir`),
    DEEZER_PRE_KEY and DEEZER_IV all set."""
    env = os.environ if env is None else env
    return bool(_arl(env, state_dir)) and keys_set(env)


def load(env=None, state_dir=None):
    """The settings, the arl set from the dashboard in `state_dir` taking the place of DEEZER_ARL.
    Raises ConfigError."""
    env = os.environ if env is None else env
    missing = [name for name in REQUIRED if not (_arl(env, state_dir) if name == ARL else _value(env, name))]
    if missing:
        raise ConfigError(f"not set: {', '.join(missing)}")
    pre_key = _value(env, PRE_KEY).encode()
    if len(pre_key) != 16:
        raise ConfigError(f"{PRE_KEY} must be 16 bytes long")
    iv = _value(env, IV)
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", iv):
        raise ConfigError(f"{IV} must be 16 hexadecimal characters")
    return Config(arl=_arl(env, state_dir), pre_key=pre_key, iv=bytes.fromhex(iv))
