"""Tests for the order in which the provider chain settles on a result."""
# pylint: disable=protected-access

import unittest


from music_librarian import lyrics as L

META = {"artist": "Muse", "title": "Space Debris", "album": None, "duration": 247}
PLAIN = {"lyrics": "la la la", "synced": None, "meta": META}
SYNCED = {"lyrics": "la la la", "synced": "[00:01.00]la la la", "meta": META}


def _answers(found):
    def provider(*_args):
        provider.calls += 1
        if isinstance(found, Exception):
            raise found
        return dict(found) if found else None
    provider.calls = 0
    return provider


class ProviderChainTest(unittest.TestCase):
    def setUp(self):
        self._orig = L.enabled_providers

    def tearDown(self):
        L.enabled_providers = self._orig

    def _use(self, *named):
        L.enabled_providers = lambda: list(named)

    def test_the_first_usable_answer_ends_the_chain(self):
        second = _answers(SYNCED)
        self._use(("lrclib", _answers(PLAIN)), ("musixmatch", second))
        res = L.fetch_lyrics("Muse", "Space Debris")
        self.assertEqual((res["source"], res["lyrics"]), ("lrclib", "la la la"))
        self.assertEqual(second.calls, 0)

    def test_a_synced_only_answer_is_usable(self):
        synced_only = {"lyrics": None, "synced": "[00:01.00]la la la", "meta": META}
        self._use(("lrclib", _answers(None)), ("netease", _answers(synced_only)))
        res = L.fetch_lyrics("Muse", "Space Debris")
        self.assertEqual((res["source"], res["synced"]), ("netease", synced_only["synced"]))

    def test_a_provider_that_crashes_does_not_break_the_chain(self):
        self._use(("lrclib", _answers(RuntimeError("boom"))), ("netease", _answers(PLAIN)))
        self.assertEqual(L.fetch_lyrics("Muse", "Space Debris")["source"], "netease")

    def test_a_rejected_answer_lets_the_next_provider_try(self):
        wrong = {"lyrics": "x", "synced": None, "meta": {**META, "title": "Other"}}
        self._use(("lrclib", _answers(wrong)), ("netease", _answers(PLAIN)))
        res = L.fetch_lyrics("Muse", "Space Debris", duration="247", verify=True)
        self.assertEqual(res["source"], "netease")

    def test_no_artist_or_title_asks_no_provider(self):
        provider = _answers(PLAIN)
        self._use(("lrclib", provider))
        self.assertEqual(L.fetch_lyrics("", "Space Debris")["source"], "none")
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
