#!/usr/bin/env python3
"""
import_inbox — importe dans la bibliothèque ce qui est déposé dans l'Inbox.

Pour chaque élément de l'Inbox (un dossier d'album, ou un fichier en vrac) :
  1. conversion FLAC → ALAC (ffmpeg pour l'audio, mutagen pour les tags et la
     pochette), contrôle de la durée (ffprobe) et des tags ;
  2. rangement avec music_ranger, tout ou rien : au moindre problème l'élément
     part entier dans _erreurs/ ;
  3. enrichissement (ReplayGain, pochette, paroles) des seuls dossiers cibles ;
  4. l'élément d'origine (FLAC compris) part dans _traités/AAAA-MM-JJ/, purgé
     après 7 jours.

Dry-run par défaut. --apply pour exécuter réellement.

  import_inbox.py                 # voir ce qui serait fait
  import_inbox.py --apply         # importer

Chemins : --inbox / $MUSIC_RANGER_INBOX, --root / $MUSIC_RANGER_ROOT.
Un seul passage en --apply à la fois (verrou partagé avec music_ranger). Sur
SIGTERM ou Ctrl-C, l'élément en cours de rangement est terminé ; celui en cours
de conversion et les suivants restent dans l'Inbox pour le prochain passage.

Codes retour : 0 = OK, 2 = au moins un élément en erreur ou un enrichissement raté,
3 = un autre passage est en cours, 4 = arrêté avant la fin.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

import music_ranger
from music_ranger import (AUDIO_EXT, EXIT_LOCKED, EXIT_STOPPED, STOP, Locked, Log, is_junk,
                          locked, read_tags)

HERE = Path(__file__).resolve().parent
ERRORS, DONE, WORK = "_erreurs", "_traités", "_en_cours"
RESERVED = {ERRORS, DONE, WORK}
# formats audio reconnus mais non gérés : l'élément part dans _erreurs
OTHER_AUDIO = {".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".wma", ".ape", ".wv",
               ".dsf", ".dff", ".aac", ".m4b", ".mp2", ".mka", ".alac", ".mpc", ".tta"}
# annexes qui suivent l'album dans la bibliothèque ; les autres (.txt, .nfo,
# .log…) restent avec l'original dans _traités/
ANNEX_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".lrc", ".cue"}
# pochettes renommées en folder.jpg (le nom de la bibliothèque, et celui que
# cherche embed_covers.py), sauf si le dossier en a déjà un
COVER = "folder.jpg"
COVER_NAMES = {"cover.jpg", "cover.jpeg", "front.jpg", "front.jpeg"}
MAX_DRIFT = 0.1  # écart de durée toléré entre FLAC et ALAC, en secondes
OK, FAILED, STOPPED = "ok", "erreur", "arrêt"  # issue du traitement d'un élément


class Reject(Exception):
    """Problème sur un élément : il part dans _erreurs."""


class Stopped(Exception):
    """Arrêt demandé pendant la préparation d'un élément : il reste dans l'Inbox."""


# ---------- éléments de l'Inbox ----------

class Item:
    def __init__(self, path: Path):
        self.path = path                      # dossier d'album ou fichier en vrac
        self.name = path.name
        self.is_dir = path.is_dir()

    def files(self):
        if not self.is_dir:
            return [self.path]
        out = []
        for dirpath, dirnames, filenames in os.walk(self.path):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            out += [Path(dirpath) / n for n in sorted(filenames)]
        return out

    def rel(self, p: Path):
        return p.relative_to(self.path) if self.is_dir else Path(p.name)

    def snapshot(self):
        snap = []
        for p in self.files():
            try:
                st = p.stat()
                snap.append((str(p), st.st_size, st.st_mtime))
            except OSError:
                snap.append((str(p), None, None))
        return snap


def list_items(inbox: Path):
    return [Item(p) for p in sorted(inbox.iterdir())
            if p.name not in RESERVED and not p.name.startswith(".")]


def stable_items(items, settle):
    """Garde les éléments dont aucun fichier n'a changé pendant `settle` secondes
    (copie terminée)."""
    if settle <= 0:
        return items, []
    before = {it.name: it.snapshot() for it in items}
    time.sleep(settle)
    ok, busy = [], []
    for it in items:
        (ok if it.snapshot() == before[it.name] else busy).append(it)
    return ok, busy


def free_name(d: Path, name, is_dir=True):
    """d/name, ou d/name (2), (3)… si déjà pris."""
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    p, i = d / name, 2
    while p.exists():
        p = d / f"{stem} ({i}){ext}"
        i += 1
    return p


# ---------- ffprobe / conversion ----------

def probe(p: Path):
    """(codec audio, durée en secondes)"""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,codec_name", "-of", "json", str(p)],
                         capture_output=True, text=True, check=True)
    j = json.loads(out.stdout)
    codec = next((s.get("codec_name") for s in j.get("streams", [])
                  if s.get("codec_type") == "audio"), None)
    return codec, float(j.get("format", {}).get("duration") or 0)


# Vorbis (FLAC) → MP4. Le reste est gardé en champ libre iTunes, sauf
# REPLAYGAIN_* : r128gain recalcule le gain comme pour le reste de la bibliothèque.
MP4_TEXT = {
    "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album artist": "aART",
    "album": "\xa9alb", "date": "\xa9day", "genre": "\xa9gen", "composer": "\xa9wrt",
    "comment": "\xa9cmt", "description": "\xa9cmt", "lyrics": "\xa9lyr",
    "unsyncedlyrics": "\xa9lyr", "grouping": "\xa9grp", "copyright": "cprt",
    "titlesort": "sonm", "artistsort": "soar", "albumsort": "soal",
    "albumartistsort": "soaa", "composersort": "soco",
}
NUMBERS = {"tracknumber", "tracktotal", "totaltracks", "discnumber", "disctotal",
           "totaldiscs", "compilation", "bpm"}


def copy_tags(flac: FLAC, mp4: MP4):
    t = flac.tags or {}
    keys = {k.lower(): t[k] for k in t.keys()}
    for k, v in keys.items():
        if k in MP4_TEXT:
            mp4.tags[MP4_TEXT[k]] = list(mp4.tags.get(MP4_TEXT[k], [])) + list(v)
        elif k not in NUMBERS and not k.startswith("replaygain_"):
            mp4.tags[f"----:com.apple.iTunes:{k.upper()}"] = [
                MP4FreeForm(x.encode("utf-8")) for x in v]

    num = lambda *ks: next((music_ranger.parse_pair(keys[k][0]) for k in ks if k in keys),
                           (None, None))
    trk, trk_total = num("tracknumber")
    disc, disc_total = num("discnumber")
    trk_total = trk_total or num("tracktotal", "totaltracks")[0]
    disc_total = disc_total or num("disctotal", "totaldiscs")[0]
    if trk:
        mp4.tags["trkn"] = [(trk, trk_total or 0)]
    if disc:
        mp4.tags["disk"] = [(disc, disc_total or 0)]
    if keys.get("compilation", ["0"])[0].strip() == "1":
        mp4.tags["cpil"] = True
    bpm = num("bpm")[0]
    if bpm:
        mp4.tags["tmpo"] = [bpm]

    pics = sorted(flac.pictures, key=lambda p: p.type != 3)   # pochette avant d'abord
    if pics:
        fmt = MP4Cover.FORMAT_PNG if pics[0].mime == "image/png" else MP4Cover.FORMAT_JPEG
        mp4.tags["covr"] = [MP4Cover(pics[0].data, imageformat=fmt)]


CHECKED = ("albumartist", "artist", "album", "title", "track", "disc", "disctotal",
           "compilation")


def convert(src: Path, dst: Path):
    """FLAC → ALAC, puis contrôles. Lève Reject si quelque chose cloche."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src),
                        "-map", "0:a:0", "-c:a", "alac", "-map_metadata", "-1",
                        "-fflags", "+bitexact", str(dst)],
                       capture_output=True, text=True)
    if r.returncode:
        raise Reject(f"ffmpeg a échoué sur {src.name} : {r.stderr.strip()[-300:]}")

    flac = FLAC(src)
    mp4 = MP4(dst)
    if mp4.tags is None:
        mp4.add_tags()
    copy_tags(flac, mp4)
    mp4.save()

    _, d_src = probe(src)
    codec, d_dst = probe(dst)
    if codec != "alac" or abs(d_src - d_dst) > MAX_DRIFT:
        raise Reject(f"contrôle raté sur {src.name} : {codec}, "
                           f"durée {d_src:.2f}s → {d_dst:.2f}s")
    a, b = read_tags(src) or {}, read_tags(dst) or {}
    diff = [k for k in CHECKED if a.get(k) != b.get(k)]
    if diff:
        raise Reject(f"tags différents après conversion de {src.name} : {', '.join(diff)}")
    if flac.pictures and "covr" not in MP4(dst).tags:
        raise Reject(f"pochette perdue à la conversion de {src.name}")


# ---------- enrichissement ----------

def enrichers(args):
    """(nom, commande) appelées avec les dossiers cibles en arguments. Les .py
    directement, jamais les wrappers _cron.sh (leur marqueur avancerait)."""
    lyr = Path(args.lyrion_dir)
    py = next((str(p) for p in (lyr / ".venv-cli/bin/python", lyr / ".venv/bin/python")
               if p.exists()), "python3")
    return [
        ("replaygain", [args.r128gain, "-r", "-s", "-p", "1"]),
        ("pochettes", [py, str(lyr / "scripts/embed_covers.py")]),
        ("paroles", [py, str(lyr / "scripts/embed_lyrics.py")]),
    ]


def enrich(args, dirs, log):
    ok = True
    for name, cmd in enrichers(args):
        log(f"ENRICHIR ({name})  {len(dirs)} dossier(s)")
        try:
            r = subprocess.run([*cmd, *map(str, dirs)])
            failed = r.returncode != 0
        except OSError as e:
            failed = True
            log(f"         {e}")
        if failed:
            log(f"ÉCHEC    enrichissement {name} : rattrapé par le passage hebdomadaire")
            ok = False
    return ok


# ---------- traitement d'un élément ----------

def ranger(args, paths, apply, quiet=False):
    argv = ["--root", str(args.root), *map(str, paths)]
    if apply:
        argv.append("--apply")
    if args.state_dir:
        argv += ["--state-dir", args.state_dir]
    if args.syslog and not quiet:
        argv.append("--syslog")
    # interruptible=False : un élément est rangé en entier ou pas du tout
    if not quiet:
        return music_ranger.run(argv, interruptible=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code, report = music_ranger.run(argv, interruptible=False)
    report["output"] = buf.getvalue()
    return code, report


def classify(item):
    """(flac, autres audio acceptés, annexes, laissés, parasites). Lève Reject
    pour un format non géré."""
    flacs, audio, annexes, left, junk = [], [], [], [], []
    for p in item.files():
        ext = p.suffix.lower()
        if is_junk(p):
            junk.append(p)
        elif ext == ".flac":
            flacs.append(p)
        elif ext in AUDIO_EXT:
            if ext == ".m4a":
                codec, _ = probe(p)
                if codec != "alac":
                    raise Reject(f"{p.name} : .m4a en {codec}, pas en ALAC")
            audio.append(p)
        elif ext in OTHER_AUDIO:
            raise Reject(f"{p.name} : format {ext} non géré")
        elif ext in ANNEX_EXT:
            annexes.append(p)
        else:
            left.append(p)
    if not flacs and not audio:
        raise Reject("aucun fichier audio")
    return flacs, audio, annexes, left, junk


def annex_names(item, annexes):
    """Nom relatif de chaque annexe dans la bibliothèque : cover.jpg → folder.jpg."""
    has_cover = {item.rel(p).parent for p in annexes if p.name.lower() == COVER}
    out = {}
    for p in annexes:
        rel = item.rel(p)
        if p.name.lower() in COVER_NAMES and rel.parent not in has_cover:
            has_cover.add(rel.parent)
            rel = rel.with_name(COVER)
        out[p] = rel
    return out


def link_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)        # même disque : instantané, l'original reste intact
    except OSError:
        shutil.copy2(src, dst)


def to_errors(args, item, reason, log):
    log(f"ERREUR   {item.name} : {reason}")
    if args.apply:
        dst = free_name(args.inbox / ERRORS, item.name, item.is_dir)
        shutil.move(str(item.path), str(dst))
        log(f"         → {dst}")


def process(args, item, log):
    """Renvoie (OK | FAILED | STOPPED, dossiers cibles)."""
    try:
        flacs, audio, annexes, left, junk = classify(item)
    except (Reject, subprocess.CalledProcessError, ValueError) as e:
        to_errors(args, item, e, log)
        return FAILED, set()

    log(f"\n=== {item.name}  ({len(flacs)} FLAC, {len(audio)} ALAC/MP3, "
        f"{len(annexes)} annexe(s))")
    names = annex_names(item, annexes)
    for p, rel in names.items():
        if rel.name != p.name:
            log(f"POCHETTE {item.rel(p)} → {rel.name}")
    for p in left:
        log(f"LAISSÉ   {item.rel(p)} : gardé avec l'original dans {DONE}/")

    if not args.apply:
        for p in flacs:
            log(f"CONVERTIR {item.rel(p)}  → .m4a")
        code, report = ranger(args, [item.path], apply=False, quiet=True)
        if code:
            log(report["output"].rstrip())
            log(f"ERREUR   {item.name} : irait dans {ERRORS}/ (voir ci-dessus)")
            return FAILED, set()
        for src, dst in report["moves"]:
            if src in left:
                continue
            if src.suffix.lower() == ".flac":
                dst = dst.with_suffix(".m4a")
            elif src in names:
                dst = dst.with_name(names[src].name)
            log(f"RANGER   {item.rel(src)}\n         → {dst}")
        return OK, set()

    work = args.inbox / WORK / item.name
    if work.exists():
        log(f"ERREUR   {item.name} : {work} existe déjà (passage interrompu ?), "
            f"élément laissé en place")
        return FAILED, set()

    # 1. Dossier de travail : FLAC convertis, ALAC/MP3 et annexes liés.
    try:
        for p in flacs:
            if STOP.is_set():
                raise Stopped
            log(f"CONVERTIR {item.rel(p)}")
            convert(p, work / item.rel(p).with_suffix(".m4a"))
        for p in audio:
            link_or_copy(p, work / item.rel(p))
        for p, rel in names.items():
            link_or_copy(p, work / rel)
        code, report = ranger(args, [work], apply=False, quiet=True)
        if code:
            log(report["output"].rstrip())
            raise Reject("refusé par music_ranger (voir ci-dessus)")
    except (Reject, Stopped, OSError, subprocess.CalledProcessError, ValueError) as e:
        shutil.rmtree(work, ignore_errors=True)
        with contextlib.suppress(OSError):
            work.parent.rmdir()
        if isinstance(e, Stopped):
            log(f"ARRÊT    {item.name} : laissé dans l'Inbox pour le prochain passage")
            return STOPPED, set()
        to_errors(args, item, e, log)
        return FAILED, set()

    # 2. Rangement réel.
    code, report = ranger(args, [work], apply=True)
    if code:
        log(f"ERREUR   {item.name} : rangement incomplet, élément et {work} laissés "
            f"en place (journal : {report.get('journal')})")
        return FAILED, set()
    targets = {dst.parent for src, dst in report["done"] if dst.suffix.lower() in AUDIO_EXT}

    # 3. L'original part dans _traités/ ; ce qui reste dans le dossier de travail
    #    (annexes qui n'ont pas suivi) le rejoint.
    day = args.inbox / DONE / date.today().isoformat()
    day.mkdir(parents=True, exist_ok=True)
    kept = free_name(day, item.name, item.is_dir)
    for j in junk:
        j.unlink(missing_ok=True)
    shutil.move(str(item.path), str(kept))
    leftovers = [p for p in work.rglob("*") if p.is_file()]
    if leftovers:
        rest = kept / "_non_rangés" if kept.is_dir() else free_name(day, f"{item.name}_non_rangés")
        for p in leftovers:
            link_or_copy(p, rest / p.relative_to(work))
        log(f"NOTE     {len(leftovers)} fichier(s) non rangé(s) gardé(s) dans {rest}")
    shutil.rmtree(work)
    with contextlib.suppress(OSError):
        work.parent.rmdir()
    log(f"TRAITÉ   {item.name} → {kept}")
    return OK, targets


def purge(args, log):
    limit = date.today() - timedelta(days=args.keep_days)
    d = args.inbox / DONE
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        try:
            day = date.fromisoformat(p.name)
        except ValueError:
            continue
        if p.is_dir() and day < limit:
            log(f"PURGER   {p}")
            if args.apply:
                shutil.rmtree(p)


# ---------- programme principal ----------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Importe l'Inbox dans la bibliothèque.")
    ap.add_argument("--inbox", type=Path, default=os.environ.get(
        "MUSIC_RANGER_INBOX", "/media/werdeil/internal_drive/Inbox_Musique"))
    ap.add_argument("--root", type=Path, default=os.environ.get(
        "MUSIC_RANGER_ROOT", "/media/werdeil/internal_drive/Musique"))
    ap.add_argument("--apply", action="store_true", help="exécuter (sinon dry-run)")
    ap.add_argument("--settle", type=float, default=10,
                    help="secondes sans changement avant de traiter un élément")
    ap.add_argument("--keep-days", type=int, default=7, help="durée de garde de _traités/")
    ap.add_argument("--no-enrich", action="store_true", help="sans ReplayGain/pochette/paroles")
    ap.add_argument("--lyrion-dir", default=os.environ.get(
        "LYRION_DASHBOARD_DIR", "/home/werdeil/workspace/git/lyrion-dashboard"))
    ap.add_argument("--r128gain", default=os.environ.get(
        "R128GAIN", "/opt/venvs/r128gain/bin/r128gain"))
    ap.add_argument("--state-dir", default=os.environ.get("MUSIC_RANGER_STATE"))
    ap.add_argument("--syslog", action="store_true",
                    help="envoyer aussi les lignes dans syslog/journald (tag music-import)")
    args = ap.parse_args(argv)
    args.inbox, args.root = args.inbox.resolve(), args.root.resolve()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    log = Log(args.syslog, ident="music-import")
    log(f"[{'APPLY' if args.apply else 'DRY-RUN'}] inbox : {args.inbox}")
    state_dir = music_ranger.state_path(args.state_dir)
    try:
        with locked(state_dir) if args.apply else contextlib.nullcontext():
            return import_all(args, log)
    except Locked as e:
        log(f"VERROU   un autre passage est en cours ({e}), abandon")
        return EXIT_LOCKED


def import_all(args, log):
    if not args.root.is_dir():
        log(f"ERREUR   bibliothèque introuvable : {args.root}")
        return 2
    if args.apply:
        for d in ERRORS, DONE:
            (args.inbox / d).mkdir(parents=True, exist_ok=True)
    elif not args.inbox.is_dir():
        log("Inbox absente : elle sera créée avec --apply")
        return 0

    purge(args, log)

    if args.apply:
        for p in args.inbox.iterdir():
            if p.is_file() and is_junk(p):
                p.unlink(missing_ok=True)
    items = list_items(args.inbox)
    if not items:
        log("Inbox vide")
        return 0

    items, busy = stable_items(items, args.settle)
    for it in busy:
        log(f"ATTENTE  {it.name} : copie en cours, traité au prochain passage")

    counts, targets = defaultdict(int), set()
    for i, it in enumerate(items):
        if STOP.is_set():
            counts["laissés (arrêt)"] += len(items) - i
            log(f"ARRÊT    demandé : {len(items) - i} élément(s) laissés pour le prochain passage")
            break
        status, t = process(args, it, log)
        counts[status] += 1
        targets |= t

    enrich_ok = True
    if targets and not args.no_enrich:
        log("")
        if STOP.is_set():
            log("ARRÊT    enrichissement sauté : rattrapé par le passage hebdomadaire")
        else:
            enrich_ok = enrich(args, sorted(targets), log)

    log("\n--- Résumé import ---")
    for k, v in (("importés", counts[OK]), ("en erreur", counts[FAILED]),
                 ("laissés (arrêt)", counts[STOPPED] + counts["laissés (arrêt)"]),
                 ("en attente", len(busy)), ("dossiers cibles", len(targets))):
        if v:
            log(f"{k:>20} : {v}")
    if STOP.is_set():
        return EXIT_STOPPED
    return 2 if counts[FAILED] or not enrich_ok else 0


if __name__ == "__main__":
    music_ranger.handle_stop_signals()
    sys.exit(main())
