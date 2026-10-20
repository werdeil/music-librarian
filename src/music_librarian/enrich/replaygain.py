"""Write the ReplayGain track gain of every music file that has none.

The loudness is measured by ffmpeg (EBU R128 integrated loudness, sample peak)
and written as ReplayGain 2.0 tags, referenced to -18 LUFS. Files that already
carry a gain are left alone. Each tagged file keeps its modification time, one
second later.

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

from music_librarian.core.lock import handle_stop_signals
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
    try:
        return tag(path), None
    except (MeasureError, tags.ReplayGainTagError, OSError) as exc:
        return None, str(exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the missing ReplayGain track gains.")
    parser.add_argument("targets", nargs="+", help="Directories to process (wildcards accepted).")
    parser.add_argument("--dry-run", action="store_true", help="List the files without gain, write nothing.")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                        help="Files measured in parallel (default: CPU count).")
    args = parser.parse_args(argv)

    todo = [p for _folder, files in album_folders(args.targets)
            for p in files if not tags.has_replaygain(p)]
    if args.dry_run:
        for path in todo:
            print(f"WOULD    {path}")
        print(f"\n{'without gain':>12}: {len(todo)}")
        return 0

    failed = 0
    with ThreadPoolExecutor(max(1, args.jobs)) as pool:
        for path, (result, error) in zip(todo, pool.map(attempt, todo)):
            if error:
                failed += 1
                print(f"ERROR    {path}: {error}", flush=True)
            else:
                print(f"GAIN     {path}  {result[0]:.2f} dB, peak {result[1]:.6f}", flush=True)
    print(f"\n{'tagged':>12}: {len(todo) - failed}")
    print(f"{'failed':>12}: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    handle_stop_signals()
    sys.exit(main())
