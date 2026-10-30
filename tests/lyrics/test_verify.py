"""Tests for the lyrics result verification used by the batch CLI.

The batch tool writes lyrics permanently into tags, so it must reject a
provider's result when the matched track doesn't line up with the file. These
cover the normalisation, the match rule, and fetch_lyrics' verify wiring.

White-box tests: they reach into the lyrics package's private helpers on purpose.
"""
# pylint: disable=protected-access

import unittest


from music_librarian import lyrics as L
from music_librarian.lyrics import matching


class NormalizeTest(unittest.TestCase):
    def test_folds_accents_case_and_punctuation(self):
        self.assertEqual(matching.normalize("Éléphant!"), "elephant")

    def test_strips_parenthetical_qualifiers(self):
        self.assertEqual(
            matching.normalize("Space Debris (Remastered 2019)"),
            matching.normalize("Space Debris"),
        )

    def test_strips_feat_credits(self):
        self.assertEqual(matching.normalize("Muse feat. Someone"), matching.normalize("Muse"))

    def test_keeps_names_in_other_scripts(self):
        self.assertEqual(matching.normalize("李荣浩"), "李荣浩")
        self.assertEqual(matching.normalize("Мумий Тролль!"), "мумии тролль")

    def test_empty_and_none(self):
        self.assertEqual(matching.normalize(None), "")
        self.assertEqual(matching.normalize(""), "")


class FoldNameTest(unittest.TestCase):
    def test_leading_article_is_dropped(self):
        self.assertEqual(matching.fold_name("The xx"), matching.fold_name("xx"))
        self.assertEqual(matching.fold_name("L'Affaire Louis Trio"), matching.fold_name("Affaire Louis Trio"))

    def test_plural_disagreement_still_folds_together(self):
        self.assertEqual(matching.fold_name("Les Fatals Picards"), matching.fold_name("Fatal Picards"))

    def test_an_article_inside_the_name_is_kept(self):
        self.assertNotEqual(matching.fold_name("Take The Power Back"), matching.fold_name("Take Power Back"))

    def test_short_words_keep_their_s(self):
        self.assertNotEqual(matching.fold_name("Yes"), matching.fold_name("Ye"))

    def test_different_names_stay_apart(self):
        self.assertNotEqual(matching.fold_name("The Beatles"), matching.fold_name("The Beach Boys"))


class MatchesRequestTest(unittest.TestCase):
    def _meta(self, **kw):
        base = {"artist": "Muse", "title": "Space Debris", "album": None, "duration": 247}
        base.update(kw)
        return base

    def test_exact_match_within_duration_tolerance(self):
        self.assertTrue(matching.matches_request(self._meta(duration=248), "Muse", "Space Debris", "247.1"))

    def test_duration_off_beyond_tolerance(self):
        self.assertFalse(matching.matches_request(self._meta(duration=300), "Muse", "Space Debris", "247"))

    def test_title_mismatch(self):
        self.assertFalse(
            matching.matches_request(self._meta(title="Other Song"), "Muse", "Space Debris", "247"))

    def test_artist_mismatch(self):
        self.assertFalse(
            matching.matches_request(self._meta(artist="Radiohead"), "Muse", "Space Debris", "247"))

    def test_title_in_another_script_is_compared(self):
        meta = {"artist": "李荣浩", "title": "山川", "duration": 247}
        self.assertFalse(matching.matches_request(meta, "李荣浩", "山", "247"))
        self.assertTrue(matching.matches_request(dict(meta, title="山"), "李荣浩", "山", "247"))

    def test_no_meta_is_reject(self):
        self.assertFalse(matching.matches_request(None, "Muse", "Space Debris", "247"))

    def test_missing_candidate_duration_is_reject(self):
        self.assertFalse(matching.matches_request(self._meta(duration=None), "Muse", "Space Debris", "247"))

    def test_missing_requested_duration_is_reject(self):
        self.assertFalse(matching.matches_request(self._meta(duration=248), "Muse", "Space Debris", None))

    def test_artist_tagged_without_its_article_still_matches(self):
        meta = self._meta(artist="Les Fatals Picards", title="Chez Tom")
        self.assertTrue(matching.matches_request(meta, "Fatal Picards", "Chez Tom", "247"))

    def test_title_tagged_without_its_article_still_matches(self):
        meta = self._meta(title="The Space Debris")
        self.assertTrue(matching.matches_request(meta, "Muse", "Space Debris", "247"))

    def test_qualifier_and_feat_still_match(self):
        meta = self._meta(title="Space Debris (Live)", artist="Muse feat. X", duration=246)
        self.assertTrue(matching.matches_request(meta, "Muse", "Space Debris", "247"))


class FetchLyricsVerifyTest(unittest.TestCase):
    def setUp(self):
        self._orig = L.enabled_providers

    def tearDown(self):
        L.enabled_providers = self._orig

    def _stub_provider(self, meta):
        def provider(_artist, _title, _album, _duration):
            return {"lyrics": "la la la", "synced": None, "meta": meta}
        L.enabled_providers = lambda: [("fake", provider)]

    def test_verify_accepts_matching_candidate(self):
        self._stub_provider({"artist": "Muse", "title": "Space Debris", "album": None, "duration": 247})
        res = L.fetch_lyrics("Muse", "Space Debris", duration="247", verify=True)
        self.assertEqual(res["source"], "fake")
        self.assertEqual(res["lyrics"], "la la la")

    def test_verify_rejects_mismatching_candidate(self):
        self._stub_provider({"artist": "Someone Else", "title": "Wrong", "album": None, "duration": 999})
        res = L.fetch_lyrics("Muse", "Space Debris", duration="247", verify=True)
        self.assertEqual(res["source"], "rejected")
        self.assertIsNone(res["lyrics"])

    def test_lenient_accepts_mismatching_candidate(self):
        self._stub_provider({"artist": "Someone Else", "title": "Wrong", "album": None, "duration": 999})
        res = L.fetch_lyrics("Muse", "Space Debris", duration="247", verify=False)
        self.assertEqual(res["source"], "fake")
        self.assertEqual(res["lyrics"], "la la la")


if __name__ == "__main__":
    unittest.main()
