"""The DPSV (dynamic played/skipped value) of the Alternative Play Count plugin, from the counts.

The plugin moves the value at each event, within -100..100: a play adds (100 - |value|) / 8, a
skip takes (100 - |value|) / 4, at least 1, rounded half away from zero. The table keeps the
totals and the last dates, not the order of the events: every event of the kind that came last
is replayed after the others, which is exact for a track never skipped only.
"""


def rounded(value):
    return int(value + 0.5) if value >= 0 else int(value - 0.5)


def played(value):
    return value if value >= 100 else min(rounded(value + max((100 - abs(value)) / 8, 1)), 100)


def skipped(value):
    return value if value <= -100 else max(rounded(value - max((100 - abs(value)) / 4, 1)), -100)


def compute(plays, skips, last_played=0, last_skipped=0):
    plays, skips = max(int(plays or 0), 0), max(int(skips or 0), 0)
    events = [(skipped, skips), (played, plays)]
    if (last_skipped or 0) > (last_played or 0):
        events.reverse()
    value = 0
    for event, times in events:
        for _ in range(times):
            value = event(value)
    return value
