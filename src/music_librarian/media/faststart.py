"""MP4 files with the moov atom ahead of mdat: Lyrion types an ALAC file whose mdat
comes first as alcx (AAC as mp4x) and transcodes it instead of streaming it."""
import contextlib
import os
import subprocess
from pathlib import Path

from mutagen.mp4 import MP4

from music_librarian.media.atoms import leading_mdat
from music_librarian.media.convert import MAX_DRIFT, probe


class RewriteError(Exception):
    """The remux failed or its result does not match the original."""


def packets_md5(p: Path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(p), "-map", "0:a:0",
                          "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def faststart(p: Path):
    """Rewrites p with moov ahead of mdat, audio packets and tags unchanged, keeping its mode.
    Raises RewriteError and leaves p untouched if anything is off."""
    tmp = p.with_name(f".{p.stem}.faststart{p.suffix}")
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(p),
                            "-map", "0:a:0", "-c", "copy", "-map_metadata", "-1",
                            "-fflags", "+bitexact", "-movflags", "+faststart", str(tmp)],
                           capture_output=True, text=True, check=False)
        if r.returncode:
            raise RewriteError(f"ffmpeg failed on {p.name}: {r.stderr.strip()[-300:]}")
        src = MP4(p)
        if src.tags:
            dst = MP4(tmp)
            if dst.tags is None:
                dst.add_tags()
            dst.tags.clear()
            dst.tags.update(src.tags)
            dst.save()
        _check(p, tmp, src)
        st = p.stat()
        os.chmod(tmp, st.st_mode & 0o7777)
        with contextlib.suppress(OSError):
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def _check(p: Path, tmp: Path, src: MP4):
    (c_src, d_src), (c_dst, d_dst) = probe(p), probe(tmp)
    if c_src != c_dst or abs(d_src - d_dst) > MAX_DRIFT:
        raise RewriteError(f"check failed on {p.name}: {c_src} → {c_dst}, "
                           f"duration {d_src:.2f}s → {d_dst:.2f}s")
    if packets_md5(p) != packets_md5(tmp):
        raise RewriteError(f"audio differs after rewriting {p.name}")
    if dict(src.tags or {}) != dict(MP4(tmp).tags or {}):
        raise RewriteError(f"tags differ after rewriting {p.name}")
    if leading_mdat(tmp):
        raise RewriteError(f"moov still after mdat in {p.name}")
