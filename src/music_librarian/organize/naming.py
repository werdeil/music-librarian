"""Target paths the way Apple Music names them, and when a file is already in place."""
import unicodedata
from pathlib import Path

MIN_TRUNC = 20  # minimum length to accept a name truncated by Apple Music


def nfc(s):
    return unicodedata.normalize("NFC", s)


def clean(s, is_dir=False, lead=True):
    """`lead=False`: the leading `.` is kept, for a title preceded by the track
    number (Apple Music only replaces it at the start of the whole name)."""
    # characters Apple Music replaces
    # observed: / : ? " | ’ ´   — by analogy: ‘ “ ” * < > \
    for ch in '/:?"|*<>\\’‘“”´':
        s = s.replace(ch, "_")
    s = nfc(s).strip()
    if lead and s.startswith("."):
        s = "_" + s[1:]
    if is_dir and s.endswith("."):
        s = s[:-1] + "_"
    return s or "_"


def target_rel(tags, ext, comp_dir):
    if not tags.get("title"):
        return None
    if tags["compilation"]:
        top = comp_dir
    else:
        top = clean(tags["albumartist"] or tags["artist"] or "Unknown Artist", True)
    album = clean(tags["album"] or "Unknown Album", True)

    prefix = ""
    if tags["track"]:
        prefix = f"{tags['track']:02d} "
        if (tags["disctotal"] or 0) > 1 or (tags["disc"] or 0) > 1:
            prefix = f"{tags['disc'] or 1}-{tags['track']:02d} "
    title = clean(tags["title"], lead=not prefix)
    return Path(top) / album / f"{prefix}{title}{ext.lower()}"


def same_component(cur, tgt, strict_case):
    cur, tgt = nfc(cur), nfc(tgt)
    if not strict_case:
        cur, tgt = cur.casefold(), tgt.casefold()
    if cur == tgt:
        return True
    # Apple Music sometimes truncates long names: a prefix is accepted
    return len(cur) >= MIN_TRUNC and tgt.startswith(cur)


def is_in_place(cur_rel: Path, tgt_rel: Path, strict_case):
    if len(cur_rel.parts) != len(tgt_rel.parts):
        return False
    for c, t in zip(cur_rel.parts[:-1], tgt_rel.parts[:-1]):
        if not same_component(c, t, strict_case):
            return False
    if cur_rel.suffix.lower() != tgt_rel.suffix.lower():
        return False
    return same_component(cur_rel.stem, tgt_rel.stem, strict_case)
