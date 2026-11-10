"""Where the journals say a file went: the moves of every pass, followed from one to the next."""
import json
from pathlib import Path


def destinations(state_dir: Path):
    """{path a file was moved from: where it ended up}, over the journals in order."""
    origins = {}
    for journal in sorted((state_dir / "journal").glob("*.jsonl")):
        try:
            lines = journal.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") == "move":
                src, dst = record["src"], record["dst"]
                origins.setdefault(dst, set()).update(origins.pop(src, set()) | {src})
    return {src: dst for dst, sources in origins.items() for src in sources if src != dst}
