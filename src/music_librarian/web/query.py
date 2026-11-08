"""The library searched track by track: the conditions a search holds, as its address carries them
("composer:empty", "year:between:1990:1999", "tag.isrc:filled"), and the tracks they keep.

A condition is a field, an operator its kind allows, and the value it needs; text is compared
without case or accents. The library's filters are searches of their own (PRESETS).
"""
import unicodedata
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

TEXT, NUMBER, FLAG, FORMAT = "text", "number", "flag", "format"
FIELDS = {"any": TEXT, "title": TEXT, "artist": TEXT, "albumartist": TEXT, "album": TEXT, "composer": TEXT,
          "genre": TEXT, "grouping": TEXT, "folder": TEXT, "tag": TEXT,
          "year": NUMBER, "track": NUMBER, "disc": NUMBER, "duration": NUMBER,
          "lyrics": FLAG, "cover": FLAG, "replaygain": FLAG, "repeated": FLAG, "compilation": FLAG,
          "format": FORMAT}
GROUPS = (("text", ("any", "title", "artist", "albumartist", "album", "composer", "genre", "grouping",
                    "folder", "tag")),
          ("number", ("year", "track", "disc", "duration")),
          ("file", ("lyrics", "cover", "replaygain", "format")),
          ("state", ("repeated", "compilation")))
OPERATORS = {TEXT: ("contains", "lacks", "is", "isnot", "starts", "filled", "empty", "several"),
             NUMBER: ("eq", "lt", "gt", "between", "filled", "empty"),
             FLAG: ("yes", "no"),
             FORMAT: ("is", "isnot")}
BARE = ("filled", "empty", "several", "yes", "no")   # the operators that take no value
SNAPSHOT_KEYS = {"title": ("title",), "artist": ("artist",), "albumartist": ("albumartist", "album artist"),
                 "album": ("album",), "composer": ("composer",), "genre": ("genre",),
                 "grouping": ("grouping",), "year": ("date", "year"), "track": ("tracknumber",),
                 "disc": ("discnumber",)}
ANY_FIELDS = ("title", "artist", "albumartist", "album", "composer", "genre", "grouping")
PRESETS = {"lyrics": ("lyrics:no",), "repeats": ("repeated:yes",), "compilations": ("compilation:yes",),
           "composer": ("composer:empty",), "genre": ("genre:empty",), "cover": ("cover:no",),
           "replaygain": ("replaygain:no",)}
SHOWN_ALWAYS = ("any", "title", "artist", "album")   # the track list's own columns


def fold(text):
    """The text as searched: no case, no accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))


def kind_of(name):
    return FIELDS.get(name.split(".", 1)[0] if name.startswith("tag.") else name)


@dataclass(slots=True)
class Track:
    folder: str              # relative to the root
    name: str                # the file's name in it
    values: dict             # {field of SNAPSHOT_KEYS: [values]}
    tags: dict               # every tag of the snapshot, {lowercase key: [values]}
    flags: frozenset         # of lyrics, cover, replaygain, repeated, compilation
    duration: float | None = None
    folded: dict = dataclass_field(default_factory=dict)
    text: str = ""           # what the search box matches, folded
    presets: frozenset = frozenset()

    def __post_init__(self):
        self.folded = {f: [fold(v) for v in self.values.get(f, [])] for f in ANY_FIELDS}
        self.text = fold(" ".join([self.folder, *(self.first(f) for f in ("albumartist", "album", "artist",
                                                                          "title"))]))
        self.presets = frozenset(name for name, search in PRESET_SEARCHES.items() if search.keeps(self))

    @property
    def format(self):
        return Path(self.name).suffix.lower().lstrip(".")

    def first(self, name):
        return next(iter(self.values.get(name, [])), "")


def track_of(folder, name, snap, flags):
    """The Track of a metadata cache snapshot, `flags` saying which of FLAG fields it has."""
    easy = {k.lower(): [v for v in vs if v.strip()] for k, vs in snap["tags"].items()}
    values = {f: [v for k in keys for v in easy.get(k, [])] for f, keys in SNAPSHOT_KEYS.items()}
    return Track(folder, name, values, easy, frozenset(flags), snap.get("duration"))


def number(text):
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


def number_of(track, name):
    if name == "duration":
        return None if track.duration is None else track.duration / 60
    value = track.first(name)
    if name == "year":
        value = value[:4]
    return number(value.split("/")[0]) if value else None


def texts_of(track, name):
    """The folded values a text condition on `name` compares."""
    if name == "any":
        return [v for f in ANY_FIELDS for v in track.folded[f]]
    if name == "folder":
        return [fold(track.folder)]
    if name.startswith("tag."):
        return [fold(v) for v in track.tags.get(name[4:], [])]
    return track.folded[name]


@dataclass(frozen=True)
class Condition:
    field: str        # a key of FIELDS, "tag.<name>" for any tag
    op: str
    value: str = ""
    upto: str = ""    # the upper bound of "between"

    def __str__(self):
        parts = [self.field, self.op] + ([] if self.op in BARE else [self.value])
        return ":".join(parts + ([self.upto] if self.op == "between" else []))

    @property
    def kind(self):
        return kind_of(self.field)

    def keeps(self, track):
        if self.kind == FLAG:
            return (self.field in track.flags) == (self.op == "yes")
        if self.kind == FORMAT:
            return (track.format == fold(self.value).lstrip(".")) == (self.op == "is")
        if self.kind == NUMBER:
            return self._keeps_number(number_of(track, self.field))
        return self._keeps_text(texts_of(track, self.field))

    def _keeps_number(self, n):
        if self.op in ("filled", "empty"):
            return (n is None) == (self.op == "empty")
        if n is None:
            return False
        low = number(self.value)
        if self.op == "between":
            return low <= n <= number(self.upto)
        return {"eq": n == low, "lt": n < low, "gt": n > low}[self.op]

    def _keeps_text(self, values):
        if self.op in ("filled", "empty"):
            return bool(values) == (self.op == "filled")
        if self.op == "several":
            return len(values) > 1 or any(";" in v for v in values)
        word = fold(self.value).strip()
        tests = {"contains": lambda v: word in v, "lacks": lambda v: word in v,
                 "is": lambda v: v.strip() == word, "isnot": lambda v: v.strip() == word,
                 "starts": lambda v: v.startswith(word)}
        found = any(tests[self.op](v) for v in values)
        return not found if self.op in ("lacks", "isnot") else found


def condition(field, op, value="", upto=""):
    """The Condition, or None when the field, the operator or a value it needs is missing or wrong."""
    field = field.strip().lower()
    if field in ("tag", "tag."):
        return None
    kind = kind_of(field)
    if kind is None or op not in OPERATORS[kind]:
        return None
    if op in BARE:
        return Condition(field, op)
    value, upto = value.strip(), upto.strip()
    if not value or (kind == NUMBER and number(value) is None):
        return None
    if op == "between" and number(upto) is None:
        return None
    return Condition(field, op, value, upto if op == "between" else "")


def parse(text):
    """The Condition of its address form "field:op[:value[:upto]]", None when it is not one."""
    parts = text.split(":", 2)
    if len(parts) < 2:
        return None
    field, op, rest = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
    value, upto = rest.split(":", 1) if op == "between" and ":" in rest else (rest, "")
    return condition(field, op, value, upto)


@dataclass(frozen=True)
class Search:
    conditions: tuple = ()
    either: bool = False    # any condition kept, rather than every one

    def keeps(self, track):
        if not self.conditions:
            return True
        test = any if self.either else all
        return test(c.keeps(track) for c in self.conditions)

    @property
    def preset(self):
        """The name of the preset this search is, or None."""
        address = tuple(str(c) for c in self.conditions)
        return next((name for name, held in PRESETS.items() if held == address and not self.either), None)

    def args(self):
        """What the address carries of it: {"c": [conditions], "match": "any"}, empty for none."""
        out = {"c": [str(c) for c in self.conditions]} if self.conditions else {}
        return {**out, "match": "any"} if self.either and self.conditions else out

    def columns(self):
        """The fields its conditions test that the track list shows besides its own columns."""
        return list(dict.fromkeys(c.field for c in self.conditions if c.field not in SHOWN_ALWAYS))


PRESET_SEARCHES = {name: Search(tuple(parse(c) for c in held)) for name, held in PRESETS.items()}


def from_args(args):
    """The Search a request's arguments ask for: the conditions of `c`, then those of the form's
    rows (f, k, o, v, w side by side, the row `drop` names left out), else the preset `only` names."""
    found = [c for c in (parse(text) for text in args.getlist("c")) if c is not None]
    rows = zip(*(args.getlist(key) for key in "fkovw"))
    drop = args.get("drop", type=int)
    for i, (field, tag, op, value, upto) in enumerate(rows):
        name = f"tag.{tag.strip().lower()}" if field == "tag" else field
        if i != drop and (c := condition(name, op, value, upto)) is not None:
            found.append(c)
    if not found and args.get("only") in PRESETS:
        return PRESET_SEARCHES[args["only"]]
    return Search(tuple(dict.fromkeys(found)), args.get("match") == "any")


def cell(track, name):
    """What the track list shows of `name`: its values as text, a bool for a flag, None for none."""
    if FIELDS.get(name) == FLAG:
        return name in track.flags
    if name == "format":
        return track.format
    if name == "duration":
        if track.duration is None:
            return None
        minutes, seconds = divmod(int(track.duration), 60)
        return f"{minutes}:{seconds:02d}"
    if name == "folder":
        return track.folder
    values = track.tags.get(name[4:], []) if name.startswith("tag.") else track.values.get(name, [])
    return "; ".join(values) or None
