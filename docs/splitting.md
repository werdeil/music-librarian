# Splitting credits

`split` rewrites the composer and artist tags that hold several names, such as `John Lennon/Paul McCartney` or `Maurice White, Verdine White`, as `John Lennon; Paul McCartney`: `;` is the separator Lyrion splits a tag on, so each name becomes a contributor of its own. The code is in [`split/`](../src/music_librarian/split).

A comma or a slash is not always a separator (`AC/DC`, `Earth, Wind & Fire`, `Bach, Johann Sebastian`), so a credit is only split when the library or a decision vouches for it.

## Which credits are split

A credit is one of the names of a tag, the tag being cut on `;` first. A credit holding a `,` or a `/` is decided in this order:

1. **A decision** from the decisions file, if there is one for it.
2. **Kept** when it is a known name as a whole. Every album artist of the library is a known name, so `AC/DC` and `Earth, Wind & Fire` are kept as soon as one album is filed under them. When its fragments are all known names too, as for an album artist `Iggy Pop, Muse`, it is still kept but reported as `GUARDED`: it looks like a collaboration, and the review asks about it.
3. **Split** when each of its fragments is a known name. The artists and composers that hold neither a comma nor a slash are known names too, so `John Lennon/Paul McCartney` is split once both have a credit of their own somewhere.
4. **Awaiting a decision** otherwise: it is reported as `REVIEW` and left alone.

Names are compared case, accents' Unicode form and extra spaces aside. A name that appears twice in a tag once split is written once.

## Which tags are rewritten

- The composer, on every track.
- The artist, only on tracks with an album artist: `organize` falls back to the artist for the folder name otherwise.
- Only a tag with a single value: a tag that already holds several values is left alone.

Formats and tags are those of [naming-rules.md](naming-rules.md), plus the composer (`©wrt`, `TCOM`, `composer`).

## The decisions file

`state/split-decisions.txt` (or `--decisions FILE`) holds one credit per line:

```
Angus Young/Malcolm Young => split
Bach, Johann Sebastian => keep
Goffin/King, Spector => Gerry Goffin; Carole King; Phil Spector
Holland-Dozier-Holland, Smokey Robinson => ?
```

- `split` cuts the credit on its commas and slashes;
- `keep` leaves it as is, even when its fragments are known names;
- anything else is the names to write, separated by `;`;
- `?` waits for a decision.

A decision wins over the library, and matches the credit case and spaces aside. It also vouches for names: a kept credit becomes a known name, and so do the names a split or rewritten credit gives, which can settle other credits. Lines starting with `#` are comments. A pass with `--apply` writes the credits newly awaiting a decision at the end of the file, marked `?`, so each credit is only asked about once.

## Reviewing one credit at a time

`music-librarian split --review` asks about the credits awaiting a decision, most frequent first, and shows each one with its fragments, the ones already known and an example track:

```
REVIEW   [1/534] Angus Young/Malcolm Young  (42 tracks)
         split: Angus Young · Malcolm Young
         e.g. AC_DC/Back in Black/01 Hells Bells.m4a
         [Enter] split · s split · k keep · e edit · n later · u undo · q quit >
```

- **Enter** takes the suggestion: split when a fragment is already known, keep for a slash between short fragments (`AC/DC`) or a comma list whose fragments are single words but the last (`Bach, Johann Sebastian`, `Crosby, Stills, Nash & Young`), split otherwise.
- **e** types the names to write, separated by `;`. **n** leaves the credit for a later review, **u** takes back the previous answer, **q** (or Ctrl-D) stops.

The guarded credits come after the others, marked `album artist, kept unless split here`, with `keep` as the suggestion: Enter keeps them whole, **s** splits them. Either answer is a decision, so the credit is not asked about again. The album artist tag itself is never split.

Each answer is written to the decisions file at once, so a review can stop at any point and resume where it left off. Since an answer vouches for names, the credits it settles further down are not asked about. The review only writes the decisions file: the tags are rewritten by the next `split --apply` or weekly `enrich`. In a terminal it runs with `docker compose run --rm music-librarian split --review` in Docker; the dashboard asks the same questions on its Reviews page (see [web.md](web.md)).

## When it runs

- `music-librarian split` over the whole library, or over the folders given as arguments.
- `import`, on the folders it has just filed, after their enrichment (`--no-split` to skip it).
- `enrich`, over the whole library, after the other tools (`--no-split` to skip it): the decisions taken in the file are applied by the next weekly pass.

The tags come from the metadata cache (`state/metadata.db`), so a pass only opens the files that changed. The known names are those of every file of the library the cache holds, so a pass over a few folders, such as the one `import` runs, still knows the whole library: a name is known as soon as a file crediting it has been read, by `scan`, `enrich`, `import` or any `split` or `dedupe` pass.

## Undoing

Every tag change is written to the journal with its old value, and `music-librarian undo JOURNAL` restores it. A tag that changed since the pass is not restored and is reported as `REFUSED`.
