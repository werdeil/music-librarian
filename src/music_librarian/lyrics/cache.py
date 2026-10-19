"""Process-local, bounded, in-memory cache of lyrics searches."""
import threading
import time
from collections import OrderedDict

TTL_HIT = 24 * 3600
TTL_MISS = 3600

# The cache key includes client-supplied fields, so the cache must stay bounded.
MAX_ENTRIES = 1000

_entries = OrderedDict()
_lock = threading.Lock()


def get(key):
    with _lock:
        entry = _entries.get(key)
        if entry and entry["expires_at"] > time.time():
            _entries.move_to_end(key)
            return entry["value"]
        if entry:
            _entries.pop(key, None)
    return None


def put(key, value, ttl):
    with _lock:
        now = time.time()
        for expired in [k for k, e in _entries.items() if e["expires_at"] <= now]:
            del _entries[expired]
        _entries.pop(key, None)
        while len(_entries) >= MAX_ENTRIES:
            _entries.popitem(last=False)
        _entries[key] = {"value": value, "expires_at": now + ttl}
