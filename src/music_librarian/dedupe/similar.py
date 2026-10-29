"""Names that are likely one artist spelled several ways."""
import re
import unicodedata
from collections import defaultdict

from music_librarian.split.rules import key

NORMALIZED = "same name once normalized"
MUSICBRAINZ = "same MusicBrainz id"
TYPO = "close spelling"
PARTIAL = "partial name"
CONJUNCTION = re.compile(r"\s*(?:&|\+|\band\b|\bet\b)\s*")
TYPO_MIN_LENGTH = 6


def fold(name):
    s = unicodedata.normalize("NFKD", name.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"^the\s+", "", CONJUNCTION.sub(" and ", s).strip())


def compact(name):
    return re.sub(r"[\W_]+", "", fold(name))


def turned(name):
    """The compact form of a name read 'First Last' when it is written 'Last, First'."""
    if name.count(",") != 1:
        return compact(name)
    last, first = name.split(",")
    return compact(f"{first} {last}")


def forms(name):
    return {compact(name), turned(name)} - {""}


def words(name):
    return re.findall(r"\w+", fold(name))


def surname(name):
    return (words(name.split(",")[0]) if name.count(",") == 1 else words(name))[-1:]


class Groups:
    """Union of names, never across two names declared different or carrying
    MusicBrainz ids that do not meet (homonyms)."""

    def __init__(self, names, mbids, different):
        self.parent = {n: n for n in names}
        self.reasons = defaultdict(set)
        self.mbids, self.different = mbids, different

    def root(self, n):
        while self.parent[n] != n:
            self.parent[n] = self.parent[self.parent[n]]
            n = self.parent[n]
        return n

    def link(self, a, b, reason):
        if a == b or frozenset((key(a), key(b))) in self.different:
            return
        if self.mbids.get(a) and self.mbids.get(b) and not self.mbids[a] & self.mbids[b]:
            return
        ra, rb = self.root(a), self.root(b)
        if ra != rb:
            self.parent[rb] = ra
            self.reasons[ra] |= self.reasons.pop(rb, set())
        self.reasons[ra].add(reason)

    def link_all(self, buckets, reason):
        for names in buckets.values():
            for other in names[1:]:
                self.link(names[0], other, reason)


def close_pairs(names):
    """Pairs of names whose compact forms are one typo apart: a letter missing, added or
    changed, or two neighbouring letters swapped. Two such forms share a one-letter deletion."""
    by_deletion = defaultdict(set)
    for n in names:
        c = compact(n)
        if len(c) >= TYPO_MIN_LENGTH:
            for d in {c} | {c[:k] + c[k + 1:] for k in range(len(c))}:
                by_deletion[d].add(n)
    pairs = set()
    for bucket in by_deletion.values():
        ordered = sorted(bucket)
        pairs |= {(a, b) for k, a in enumerate(ordered) for b in ordered[k + 1:]
                  if compact(a) != compact(b)}
    return pairs


def partial_pairs(names):
    """(single word, full name) when exactly one name of several words ends with that word."""
    by_surname = defaultdict(list)
    singles = []
    for n in names:
        w = words(n)
        if len(w) == 1 and len(w[0]) >= 3:
            singles.append(n)
        elif len(w) > 1:
            by_surname[surname(n)[0]].append(n)
    out = []
    for n in singles:
        full = by_surname.get(words(n)[0], [])
        if len({turned(f) for f in full}) == 1:
            out.append((n, full[0]))
    return out


def find_groups(counts, mbids, different):
    """Likely duplicates among the names of `counts` ({name: tracks}).

    mbids: {name: set of MusicBrainz ids}; different: set of frozenset({key(a), key(b)})
    never grouped. Returns [(names, reasons)] with names by decreasing count (ties: the
    'First Last' and longer spellings first), the groups counting the most first."""
    names = sorted(counts)
    groups = Groups(names, mbids, different)
    by_form, by_id = defaultdict(list), defaultdict(list)
    for n in names:
        for f in forms(n):
            by_form[f].append(n)
        for i in mbids.get(n, ()):
            by_id[i].append(n)
    groups.link_all(by_form, NORMALIZED)
    groups.link_all(by_id, MUSICBRAINZ)
    for a, b in sorted(close_pairs(names)):
        groups.link(a, b, TYPO)
    for a, b in partial_pairs(names):
        groups.link(a, b, PARTIAL)

    members = defaultdict(list)
    for n in names:
        members[groups.root(n)].append(n)
    out = [(sorted(m, key=lambda n: (-counts[n], "," in n, -len(n), n)), groups.reasons[r])
           for r, m in members.items() if len(m) > 1]
    return sorted(out, key=lambda g: (-sum(counts[n] for n in g[0]), g[0][0]))
