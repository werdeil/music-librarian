"""Top-level MP4 atoms, read from their headers only."""
import os
import struct
from pathlib import Path

MP4_EXT = {".m4a"}


def leading_mdat(p: Path):
    """True when the top-level mdat atom comes before moov. Reads only the atom headers."""
    with open(p, "rb") as f:
        while (head := f.read(8)) and len(head) == 8:
            size, kind = struct.unpack(">I4s", head)
            if kind in (b"moov", b"mdat"):
                return kind == b"mdat"
            if size == 1:
                if len(large := f.read(8)) < 8:
                    return False
                size = struct.unpack(">Q", large)[0] - 8
            elif size == 0:
                return False
            f.seek(size - 8, os.SEEK_CUR)
    return False
