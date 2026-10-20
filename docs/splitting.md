# Splitting credits

`split` rewrites the composer and artist tags that hold several names, such as `John Lennon/Paul McCartney` or `Maurice White, Verdine White`, as `John Lennon; Paul McCartney`: `;` is the separator Lyrion splits a tag on, so each name becomes a contributor of its own. The code is in [`split/`](../src/music_librarian/split).

A comma or a slash is not always a separator (`AC/DC`, `Earth, Wind & Fire`, `Bach, Johann Sebastian`), so a credit is only split when the library or a decision vouches for it.

## Which credits are split

A credit is one of the names of a tag, the tag being cut on `;` first. A credit holding a `,` or a `/` is decided in this order:

1. **A decision** from the decisions file, if there is one for it.
2. **Kept** when it is a known name as a whole. Every album artist of the library is a known name, so `AC/DC` and `Earth, Wind & Fire` are kept as soon as one album is filed under them.
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

A decision wins over the library, and matches the credit case and spaces aside. Lines starting with `#` are comments. A pass with `--apply` writes the credits newly awaiting a decision at the end of the file, marked `?`, so each credit is only asked about once.

## When it runs

- `music-librarian split` over the whole library, or over the folders given as arguments.
- `import`, on the folders it has just filed, after their enrichment (`--no-split` to skip it).
- `enrich`, over the whole library, after the other tools (`--no-split` to skip it): the decisions taken in the file are applied by the next weekly pass.

A pass over a few folders still needs the names of the whole library: a full `--apply` pass keeps them in `state/split-names.json`, read by the partial passes.

## Undoing

Every tag change is written to the journal with its old value, and `music-librarian undo JOURNAL` restores it. A tag that changed since the pass is not restored and is reported as `REFUSED`.
