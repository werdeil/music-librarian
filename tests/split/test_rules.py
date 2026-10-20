"""Which credits are split, kept or left for a decision."""
import pytest

from music_librarian.split.rules import (KEEP, PENDING, SPLIT, decided_names, known_names, resolve, rewrite,
                                         suggest)

KNOWN = known_names(["AC/DC", "Earth, Wind & Fire", "The Beatles"],
                    ["John Lennon", "Paul McCartney", "John Lennon/Paul McCartney", "Malcolm Young"])


@pytest.mark.parametrize("credit, names, status", [
    ("AC/DC", ["AC/DC"], KEEP),                                     # an album artist
    ("earth,  wind & fire", ["earth,  wind & fire"], KEEP),         # case and spaces aside
    ("John Lennon/Paul McCartney", ["John Lennon", "Paul McCartney"], SPLIT),
    ("John Lennon , Paul McCartney", ["John Lennon", "Paul McCartney"], SPLIT),
    ("Bach, Johann Sebastian", ["Bach, Johann Sebastian"], PENDING),
    ("Malcolm Young/Angus Young", ["Malcolm Young/Angus Young"], PENDING),  # one fragment unknown
    ("Prince", ["Prince"], KEEP),
])
def test_resolve(credit, names, status):
    assert resolve(credit, KNOWN, {}) == (names, status)


def test_aggregates_are_not_known_names():
    assert "john lennon/paul mccartney" not in KNOWN


@pytest.mark.parametrize("decision, names, status", [
    ("keep", ["Malcolm Young/Angus Young"], KEEP),
    ("split", ["Malcolm Young", "Angus Young"], SPLIT),
    ("Malcolm Young; Angus Young; Bon Scott", ["Malcolm Young", "Angus Young", "Bon Scott"], SPLIT),
    ("?", ["Malcolm Young/Angus Young"], PENDING),
])
def test_decision_wins(decision, names, status):
    assert resolve("Malcolm Young/Angus Young", KNOWN, {"malcolm young/angus young": decision}) \
        == (names, status)


def test_decision_overrides_a_known_name():
    assert resolve("AC/DC", KNOWN, {"ac/dc": "split"}) == (["AC", "DC"], SPLIT)


@pytest.mark.parametrize("value, new, pending", [
    ("John Lennon/Paul McCartney", "John Lennon; Paul McCartney", []),
    ("John Lennon/Paul McCartney; John Lennon", "John Lennon; Paul McCartney", []),
    ("John Lennon; Paul McCartney", None, []),                      # already split
    ("AC/DC; Malcolm Young", None, []),
    ("John Lennon/Paul McCartney; Bach, J.S.", "John Lennon; Paul McCartney; Bach, J.S.", ["Bach, J.S."]),
    ("Bach, J.S.", None, ["Bach, J.S."]),
])
def test_rewrite(value, new, pending):
    assert rewrite(value, KNOWN, {}) == (new, pending)


@pytest.mark.parametrize("credit, hint", [
    ("AC/DC", KEEP),
    ("Angus Young/Malcolm Young", SPLIT),
    ("Bach, Johann Sebastian", KEEP),
    ("Crosby, Stills, Nash & Young", KEEP),
    ("Maurice White, Verdine White", SPLIT),
    ("Holland, Dozier", KEEP),
    ("Holland, Paul McCartney", SPLIT),                             # a known fragment
])
def test_suggest(credit, hint):
    assert suggest(credit, KNOWN) == hint


def test_decided_names():
    assert decided_names({"angus young/malcolm young": "split", "bach, j.s.": "keep",
                          "goffin/king": "Gerry Goffin; Carole King", "x/y": "?"}) == \
        {"angus young", "malcolm young", "bach, j.s.", "gerry goffin", "carole king"}
