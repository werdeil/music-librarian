"""Write the ReplayGain track gain of every music file that has none.

The loudness is measured by ffmpeg (EBU R128 integrated loudness, sample peak)
and written as ReplayGain 2.0 tags, referenced to -18 LUFS. Files that already
carry a gain are left alone, as are formats other than MP3, MP4 and FLAC. Each
tagged file keeps its modification time, one second later. A stop request lets
the files being measured finish, then stops (exit code 4).

Usage:
    python -m music_librarian.enrich.replaygain /path/to/music [--dry-run] [--jobs N]

Targets are directories, scanned recursively, as for enrich.covers. With
--dry-run nothing is measured nor written: the files without gain are listed.
"""
import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import mutagen

from music_librarian.core.files import AUDIO_EXT
from music_librarian.core.lock import EXIT_STOPPED, STOP, handle_stop_signals
from music_librarian.enrich.covers import album_folders
from music_librarian.media import tags

REFERENCE_LUFS = -18
FILTERS = ("aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo,"
           "replaygain,ebur128=framelog=verbose")
PEAK = re.compile(r"\] track_peak = ([\d.]+)$", re.M)
LOUDNESS = re.compile(r"Summary:.*?^\s+I:\s+(-?[\d.]+|-inf) LUFS", re.M | re.S)


class MeasureError(Exception):
    """ffmpeg could not measure a file."""


def measure(path):
    """Return (integrated loudness in LUFS, sample peak from 0 to 1) of `path`."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
                        "-map", "0:a:0", "-af", FILTERS, "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace", check=False)
    peak, loudness = PEAK.search(r.stderr), LOUDNESS.search(r.stderr)
    if r.returncode != 0 or not peak or not loudness:
        last = r.stderr.strip().splitlines()[-1:] or ["no output"]
        raise MeasureError(f"ffmpeg failed ({r.returncode}): {last[0]}")
    if loudness.group(1) == "-inf":
        raise MeasureError("no measurable loudness")
    return float(loudness.group(1)), float(peak.group(1))


def tag(path):
    """Measure and tag `path`, returning the gain written."""
    loudness, peak = measure(path)
    gain = REFERENCE_LUFS - loudness
    mtime = os.stat(path).st_mtime
    tags.write_replaygain(path, gain, peak)
    os.utime(path, (os.stat(path).st_atime, mtime + 1))
    return gain, peak


def attempt(path):
    """(gain and peak, None), (None, error), or None once a stop is requested."""
    if STOP.is_set():
        return None
    try:
        return tag(path), None
    except (MeasureError, tags.ReplayGainTagError, mutagen.MutagenError, OSError) as exc:
        return None, str(exc)


def cpus():
    """CPUs this process may run on, which a container can limit."""
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the missing ReplayGain track gains.")
    parser.add_argument("targets", nargs="+", help="Directories to process (wildcards accepted).")
    parser.add_argument("--dry-run", action="store_true", help="List the files without gain, write nothing.")
    parser.add_argument("--jobs", type=int, default=cpus(),
                        help="Files measured in parallel (default: CPU count).")
    args = parser.parse_args(argv)

    todo = [p for _folder, files in album_folders(args.targets) for p in files
            if os.path.splitext(p)[1].lower() in AUDIO_EXT and not tags.has_replaygain(p)]
    if args.dry_run:
        for path in todo:
            print(f"WOULD    {path}")
        print(f"\n{'without gain':>12}: {len(todo)}")
        return 0

    tagged = failed = 0
    with ThreadPoolExecutor(max(1, args.jobs)) as pool:
        for path, outcome in zip(todo, pool.map(attempt, todo)):
            if outcome is None:
                continue
            result, error = outcome
            if error:
                failed += 1
                print(f"ERROR    {path}: {error}", flush=True)
            else:
                tagged += 1
                print(f"GAIN     {path}  {result[0]:.2f} dB, peak {result[1]:.6f}", flush=True)
    if STOP.is_set():
        print(f"STOP     requested: {len(todo) - tagged - failed} file(s) left for the next pass")
    print(f"\n{'tagged':>12}: {tagged}")
    print(f"{'failed':>12}: {failed}")
    if STOP.is_set():
        return EXIT_STOPPED
    return 1 if failed else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
