"""Tests telling an empty search apart from one that never happened.

A provider that can't be reached raises ProviderUnavailable, so fetch_lyrics can report
"unavailable" when no provider answered at all: --clear must not drop a tag on that.
"""
# pylint: disable=protected-access

import unittest


from music_librarian import lyrics as L

FOUND = {"lyrics": "la la la", "synced": None, "meta": None}


def _unreachable(name):
    def provider(*_args):
        provider.calls += 1
        raise L.ProviderUnavailable(name)
    provider.calls = 0
    return provider


def _silent():
    def provider(*_args):
        provider.calls += 1
    provider.calls = 0
    return provider


def _finds():
    def provider(*_args):
        provider.calls += 1
        return dict(FOUND)
    provider.calls = 0
    return provider


class UnavailableProvidersTest(unittest.TestCase):
    def setUp(self):
        self._orig = L._enabled_providers

    def tearDown(self):
        L._enabled_providers = self._orig

    def _use(self, *providers):
        L._enabled_providers = lambda: [(f"p{i}", p) for i, p in enumerate(providers)]

    def test_all_providers_unreachable_reports_unavailable(self):
        self._use(_unreachable("a"), _unreachable("b"))
        res = L.fetch_lyrics("Muse", "Space Debris")
        self.assertEqual(res, {"lyrics": None, "synced": None, "source": "unavailable"})

    def test_a_reachable_provider_with_no_match_is_a_miss(self):
        self._use(_unreachable("a"), _silent())
        self.assertEqual(L.fetch_lyrics("Muse", "Space Debris")["source"], "none")

    def test_a_later_provider_still_answers_when_an_earlier_one_is_down(self):
        self._use(_unreachable("a"), _finds())
        res = L.fetch_lyrics("Muse", "Space Debris")
        self.assertEqual(res["lyrics"], "la la la")
        self.assertEqual(res["source"], "p1")


if __name__ == "__main__":
    unittest.main()
