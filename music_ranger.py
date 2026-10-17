#!/usr/bin/env python3
"""
music_ranger — range une bibliothèque musicale à la façon d'Apple Music :
    <Artiste de l'album>/<Album>/<NN Titre>.ext
    Compilations/<Album>/<NN Titre>.ext            (flag compilation)
    .../<D-NN Titre>.ext                           (album multi-disques)

Dry-run par défaut. --apply pour exécuter réellement.

Exemples :
  # Vérifier toute la bibliothèque (rien n'est modifié)
  music_ranger.py --root /media/werdeil/internal_drive/Musique

  # Importer un album déposé dans l'Inbox
  music_ranger.py --root /media/werdeil/internal_drive/Musique /chemin/Inbox/MonAlbum --apply

Codes retour : 0 = OK, 2 = conflits ou fichiers ignorés (voir sortie).
"""
import argparse
import os
import shutil
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

from mutagen import File as MFile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

AUDIO_EXT = {".m4a", ".mp3", ".flac"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
MIN_TRUNC = 20  # longueur mini pour tolérer un nom tronqué par Apple Music


# ---------- utilitaires ----------

def nfc(s):
    return unicodedata.normalize("NFC", s)


def is_junk(p: Path):
    return p.name in JUNK_NAMES or p.name.startswith("._")


def first(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def to_int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_pair(s):
    """'3/12' -> (3, 12) ; '3' -> (3, None)"""
    if not s:
        return None, None
    parts = str(s).split("/")
    return to_int(parts[0]), (to_int(parts[1]) if len(parts) > 1 else None)


# ---------- lecture des tags ----------

def read_tags(path: Path):
    f = MFile(path)
    if f is None or f.tags is None:
        return None
    t = f.tags

    if isinstance(f, MP4):
        g = lambda k: first(t.get(k))
        trk = (t.get("trkn") or [(0, 0)])[0]
        dsk = (t.get("disk") or [(0, 0)])[0]
        cpil = t.get("cpil")
        if isinstance(cpil, list):
            cpil = cpil[0] if cpil else False
        return dict(
            albumartist=g("aART"), artist=g("\xa9ART"), album=g("\xa9alb"),
            title=g("\xa9nam"), track=trk[0] or None, disc=dsk[0] or None,
            disctotal=dsk[1] or None, compilation=bool(cpil),
        )

    if isinstance(f, MP3):
        g = lambda k: first(t[k].text) if k in t else None
        tr, _ = parse_pair(g("TRCK"))
        d, dt = parse_pair(g("TPOS"))
        return dict(
            albumartist=g("TPE2"), artist=g("TPE1"), album=g("TALB"),
            title=g("TIT2"), track=tr, disc=d, disctotal=dt,
            compilation=(g("TCMP") == "1"),
        )

    if isinstance(f, FLAC):
        g = lambda k: first(t.get(k))
        tr, _ = parse_pair(g("tracknumber"))
        d, dt = parse_pair(g("discnumber"))
        dt = dt or to_int(g("disctotal") or g("totaldiscs"))
        return dict(
            albumartist=g("albumartist") or g("album artist"), artist=g("artist"),
            album=g("album"), title=g("title"), track=tr, disc=d, disctotal=dt,
            compilation=(g("compilation") == "1"),
        )

    return None


# ---------- calcul du chemin cible ----------

def clean(s, is_dir=False):
    # caractères remplacés par Apple Music
    # constatés : / : ? " | ’ ´   — par analogie : ‘ “ ” * < > \
    for ch in '/:?"|*<>\\\u2019\u2018\u201c\u201d\u00b4':
        s = s.replace(ch, "_")
    s = nfc(s).strip()
    if s.startswith("."):
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
    return Path(top) / album / f"{prefix}{clean(tags['title'])}{ext.lower()}"


def same_component(cur, tgt, strict_case):
    cur, tgt = nfc(cur), nfc(tgt)
    if not strict_case:
        cur, tgt = cur.casefold(), tgt.casefold()
    if cur == tgt:
        return True
    # Apple Music tronque parfois les noms longs : on tolère un préfixe
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


# ---------- programme principal ----------

def main():
    ap = argparse.ArgumentParser(description="Range la musique façon Apple Music.")
    ap.add_argument("--root", required=True, help="racine de la bibliothèque")
    ap.add_argument("paths", nargs="*", help="dossiers à traiter (défaut : --root)")
    ap.add_argument("--apply", action="store_true", help="exécuter (sinon dry-run)")
    ap.add_argument("--compilations-dir", default="Compilations")
    ap.add_argument("--strict-case", action="store_true",
                    help="renommer aussi les différences de casse seules")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    scan = [Path(p).resolve() for p in args.paths] or [root]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] racine : {root}")

    moves = []                    # (src, dst)
    seen_targets = {}             # dst -> src
    dir_targets = defaultdict(set)  # dossier source -> dossiers cibles
    stats = defaultdict(int)
    junk = []

    for base in scan:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if is_junk(p):
                    junk.append(p)
                    continue
                if p.suffix.lower() not in AUDIO_EXT:
                    continue
                stats["analysés"] += 1
                try:
                    tags = read_tags(p)
                except Exception as e:
                    print(f"IGNORÉ   (lecture impossible : {e})  {p}")
                    stats["ignorés"] += 1
                    continue
                tgt = target_rel(tags, p.suffix, args.compilations_dir) if tags else None
                if tgt is None:
                    print(f"IGNORÉ   (tags insuffisants)  {p}")
                    stats["ignorés"] += 1
                    continue

                try:
                    cur_rel = p.relative_to(root)
                except ValueError:
                    cur_rel = None  # fichier hors bibliothèque (Inbox)

                if cur_rel is not None and is_in_place(cur_rel, tgt, args.strict_case):
                    stats["en place"] += 1
                    dir_targets[p.parent].add(p.parent)
                    continue

                dst = root / tgt
                key = nfc(str(dst)).casefold()
                if key in seen_targets or (dst.exists() and not dst.samefile(p)):
                    other = seen_targets.get(key, dst)
                    print(f"CONFLIT  {p}\n         → {dst}\n         (déjà pris par {other})")
                    stats["conflits"] += 1
                    continue
                seen_targets[key] = p
                moves.append((p, dst))
                dir_targets[p.parent].add(dst.parent)

    # Fichiers annexes (folder.jpg, .lrc…) : suivent l'album si toutes les
    # pistes du dossier partent vers le même dossier cible.
    extra = []
    for src_dir, tdirs in dir_targets.items():
        if len(tdirs) != 1:
            continue
        tdir = next(iter(tdirs))
        if tdir == src_dir:
            continue
        for p in src_dir.iterdir():
            if p.is_file() and not is_junk(p) and p.suffix.lower() not in AUDIO_EXT:
                dst = tdir / p.name
                if dst.exists():
                    print(f"CONFLIT  (annexe déjà présente)  {dst}")
                    stats["conflits"] += 1
                else:
                    extra.append((p, dst))

    for src, dst in moves + extra:
        print(f"DÉPLACER {src}\n         → {dst}")

    if args.apply:
        for src, dst in moves + extra:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        for j in junk:
            j.unlink(missing_ok=True)
        # nettoyage des dossiers vidés (jamais la racine ni les dossiers passés en argument)
        protected = {root, *scan}
        for base in scan:
            for dirpath, _, _ in sorted(os.walk(base), key=lambda x: -len(x[0])):
                d = Path(dirpath)
                if d in protected:
                    continue
                try:
                    d.rmdir()
                    stats["dossiers supprimés"] += 1
                except OSError:
                    pass

    stats["à déplacer"] = len(moves)
    stats["annexes à déplacer"] = len(extra)
    stats["fichiers parasites"] = len(junk)
    print("\n--- Résumé ---")
    for k in ("analysés", "en place", "à déplacer", "annexes à déplacer",
              "ignorés", "conflits", "fichiers parasites", "dossiers supprimés"):
        if stats[k]:
            print(f"{k:>20} : {stats[k]}")

    sys.exit(2 if stats["conflits"] or stats["ignorés"] else 0)


if __name__ == "__main__":
    main()
