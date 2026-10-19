"""Tests for the lyrics cache bounds.

The cache key includes client-supplied artist/title (via /lyrics.json), so the
cache must not grow without limit: entry count is capped with LRU eviction,
expired entries are swept on every write, and absurdly long metadata fields are
refused before they reach the cache or the providers.

White-box tests: they reach into the lyrics package's private helpers on purpose.
"""
# pylint: disable=protected-access

import unittest


from music_librarian import lyrics as L
from music_librarian.lyrics import cache


class CacheBoundsTest(unittest.TestCase):
    def setUp(self):
        cache._entries.clear()
        self._orig_max = cache.MAX_ENTRIES
        cache.MAX_ENTRIES = 3

    def tearDown(self):
        cache.MAX_ENTRIES = self._orig_max
        cache._entries.clear()

    def test_entry_count_is_capped(self):
        for i in range(10):
            cache.put(f"key{i}", {"n": i}, ttl=60)
        self.assertEqual(len(cache._entries), 3)

    def test_eviction_drops_least_recently_used(self):
        cache.put("a", "va", ttl=60)
        cache.put("b", "vb", ttl=60)
        cache.put("c", "vc", ttl=60)
        # Touch "a" so "b" becomes the least recently used entry.
        self.assertEqual(cache.get("a"), "va")
        cache.put("d", "vd", ttl=60)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), "va")
        self.assertEqual(cache.get("d"), "vd")

    def test_expired_entries_are_swept_on_write(self):
        cache.put("stale1", "v", ttl=-1)
        cache.put("stale2", "v", ttl=-1)
        cache.put("fresh", "v", ttl=60)
        self.assertEqual(set(cache._entries), {"fresh"})

    def test_rewriting_a_key_does_not_evict_others(self):
        cache.put("a", "va", ttl=60)
        cache.put("b", "vb", ttl=60)
        cache.put("c", "vc", ttl=60)
        cache.put("c", "vc2", ttl=60)
        self.assertEqual(set(cache._entries), {"a", "b", "c"})
        self.assertEqual(cache.get("c"), "vc2")


class OversizedFieldsTest(unittest.TestCase):
    def setUp(self):
        cache._entries.clear()
        self._orig = L._enabled_providers
        self.provider_calls = []

        def provider(artist, title, album, duration):
            self.provider_calls.append((artist, title, album, duration))
            return {"lyrics": "la la la", "synced": None, "meta": None}
        L._enabled_providers = lambda: [("fake", provider)]

    def tearDown(self):
        L._enabled_providers = self._orig
        cache._entries.clear()

    def test_oversized_title_is_refused_without_caching_or_fetching(self):
        res = L.fetch_lyrics(None, "Muse", "x" * (L.MAX_FIELD_LEN + 1))
        self.assertEqual(res, {"lyrics": None, "synced": None, "source": "none"})
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(len(cache._entries), 0)

    def test_oversized_track_id_is_refused(self):
        res = L.fetch_lyrics("t" * (L.MAX_FIELD_LEN + 1), "Muse", "Space Debris")
        self.assertEqual(res["source"], "none")
        self.assertEqual(self.provider_calls, [])

    def test_normal_fields_still_fetch_and_cache(self):
        res = L.fetch_lyrics(None, "Muse", "Space Debris")
        self.assertEqual(res["source"], "fake")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(len(cache._entries), 1)


if __name__ == "__main__":
    unittest.main()
