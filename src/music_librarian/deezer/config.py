"""The Deezer settings, read from the environment. A value is reported by its variable's name, never
shown: every one of them is a secret."""
import os
import re
from dataclasses import dataclass, field

ARL, PRE_KEY, IV, LICENSE_TOKEN = "DEEZER_ARL", "DEEZER_PRE_KEY", "DEEZER_IV", "DEEZER_LICENSE_TOKEN"
REQUIRED = (ARL, PRE_KEY, IV)


class ConfigError(Exception):
    """A variable is missing or malformed; the message names it, never its value."""


@dataclass(frozen=True)
class Config:
    arl: str = field(repr=False)
    pre_key: bytes = field(repr=False)
    iv: bytes = field(repr=False)
    license_token: str = field(default="", repr=False)


def _value(env, name):
    return (env.get(name) or "").strip()


def enabled(env=None):
    """Whether the download is turned on: DEEZER_ARL, DEEZER_PRE_KEY and DEEZER_IV all set."""
    env = os.environ if env is None else env
    return all(_value(env, name) for name in REQUIRED)


def load(env=None, license_token=None):
    """The settings, `license_token` (a request's own) taking the place of DEEZER_LICENSE_TOKEN.
    Raises ConfigError."""
    env = os.environ if env is None else env
    missing = [name for name in REQUIRED if not _value(env, name)]
    if missing:
        raise ConfigError(f"not set: {', '.join(missing)}")
    pre_key = _value(env, PRE_KEY).encode()
    if len(pre_key) != 16:
        raise ConfigError(f"{PRE_KEY} must be 16 bytes long")
    iv = _value(env, IV)
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", iv):
        raise ConfigError(f"{IV} must be 16 hexadecimal characters")
    return Config(arl=_value(env, ARL), pre_key=pre_key, iv=bytes.fromhex(iv),
                  license_token=(license_token or "").strip() or _value(env, LICENSE_TOKEN))
