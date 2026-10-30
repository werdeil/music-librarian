# Dashboard

`watch --listen [HOST:]PORT` serves a dashboard on that address while it watches the Inbox: the passes running and the recent ones with their output, the schedule, the Inbox and the review questions awaiting an answer. The compose file serves it on port 8080 (`WEB_PORT` in `.env` to change the host's port); without a host, every interface is listened on. `MUSIC_LIBRARIAN_LISTEN` sets the address when `--listen` is not given.

The pages read the run history in the state folder and the Inbox root on each request. The passes the dashboard asks for are queued and run by `watch` itself, between its other jobs, so they never compete with them for the lock. When `watch` runs without `--apply`, a badge at the top says the dashboard is read-only.

## Pages

- **Overview**: the passes running now, the schedule of `watch` (how often the Inbox is read, the nightly `organize` and the weekly `enrich`) with each command's last run and next slot, the items waiting in the Inbox and in `_failed/`, the number of questions each review still awaits, as the Reviews page counts them, and the ten latest runs. The Reviews tab of the menu shows the total of those questions on every page.
- **Library**: the albums of the metadata cache, one per folder holding tracks, with their cover (`folder.jpg`, else the first embedded one, else a placeholder), searched by artist, album, title or folder without case or accents. A library never scanned shows nothing until a `scan`; a file moved or deleted since it was cached is left out. The list is built in the background whenever the cache changes, so a page never waits on it: right after `watch` starts, it says it is still being worked out until the first build ends, and after a change it shows the previous list until the new one is ready.
- **An album**: its fields shared by every track (album artist, album, year, genre, number of discs, compilation flag) and a grid of its tracks (disc, shown only for an album numbered beyond one disc, number, title, artist, composer), read from the files themselves, with each track's lyrics: present, looked up in vain on a date, or neither. See below for editing them. **Look up the album's lyrics** queues a dry run of `enrich.lyrics` on its folder with `--retry`, which looks up the tracks found in vain without waiting for `LYRICS_RETRY_DAYS`; the run's page offers to write what it found.
- **Lyrics not found**, linked from the library: the albums whose tracks were looked up in vain within `LYRICS_RETRY_DAYS`, the most tracks first, with their last lookup.
- **Reviews**: the questions split and dedupe cannot settle, see below.
- **History**: the runs kept in the history (the 200 latest), filtered by command, or to the problems only.
- **A run**: its mode, who started it (`watch`, the command line), its duration and exit code, its summary, and its whole output, each line coloured by its tag (`ERROR`, `CONFLICT` and `FAILED` in red, `SKIPPED`, `WARNING`, `REFUSED`, `UNKNOWN`, `GUARDED`, `REVIEW`, `STOP` and `LOCKED` in yellow). While the run goes on, the new lines are appended every 2 seconds, and the page reloads with the summary once it ends. "Plain text" gives the output as a file.

Times are the container's (`TZ` in `.env`).

## Running a pass

The overview has a form per command: `import`, `organize` (`--since` ticked by default), `enrich` (`--rescan`, `--no-split`, `--no-dedupe`), `scan` (`--force`), `split` and `dedupe`. Each starts as a dry run and opens the run's page, which follows it live once `watch` gets to it; while a pass is running, the request waits in the queue, listed on the overview.

The page of a dry run started from the dashboard then offers **Apply this pass**: the same command with the same options, applied. It is a new pass, which plans again from the library as it then is. The page of an applied pass that left a journal offers **Undo this pass**, again as a dry run first. Nothing is applied from the dashboard unless `watch` runs with `--apply`: without it, the dashboard runs dry runs only.

A request that finds the lock taken (a command run by hand, `docker compose run`) is not kept, and its page says so. The queue lives in `watch`: a restart drops the requests not yet started. The `--review` modes of `split` and `dedupe` ask questions and stay on the command line.

## Editing an album

Change any field on the album's page, then **Preview the changes**: the dashboard queues a dry run of `tag` (see `music-librarian tag`) with one edit per field changed, an album field applying to every track. The run's page lists the tags to write and the moves they lead to; **Apply this pass** then writes them and files the tracks where their new tags put them, all in one journal, which **Undo this pass** replays backwards. A field left as shown is not rewritten, and an album field whose tracks differ shows "various values", left alone unless something is typed in. The genre field offers the validated genres of `state/genres.txt`, and a genre the list knows takes its spelling.

Each edit carries the values the page showed: a track whose field changed in between, by another pass or another tagger, is refused (`REFUSED`) rather than overwritten.

## Reviews

The Reviews page asks the questions of `split --review` and `dedupe --review` (see [splitting.md](splitting.md) and [dedupe.md](dedupe.md)) one at a time, in the same order and with the same suggestion, highlighted:

- a credit holding several names: **Split**, **Keep whole**, or the names to write separated by `;`;
- a group of likely duplicates, each spelling with an album that carries it: the spelling to write them all as, picked among them or typed, or **Different artists**;
- a genre the validated list does not know, the one most tracks carry first: a name of a validated genre, added to its line of the list (the one iTunes suggests is picked, when `enrich.genres` looked the album up with `GENRE_LOOKUP=itunes`), or a validated genre of its own.

**Later** moves to the next question, and the page that follows an answer offers to undo it. Each answer is written at once to `state/split-decisions.txt`, `state/artist-aliases.txt` or the genre list (`state/genres.txt`, or `MUSIC_LIBRARIAN_GENRES`), as in the terminal, so the review stops and resumes at will, and an answer that settles questions further down skips them. The queues are built from the metadata cache in the background, again whenever it changes, like the album list: until the first build ends, the counts and the questions say they are still being worked out. Answers rewrite no tag: **Dry run with these answers** queues `split`, `dedupe` or `enrich.genres` over the library, whose page then offers to apply it, and the weekly `enrich` applies them too.

## Statuses

| Status | Meaning |
|---|---|
| Running | no end recorded yet, and its process is alive, or ran in another container (`docker compose run`) |
| OK | exit code 0 |
| To check | exit code 2: something was skipped, refused or failed; the output says what |
| Stopped | exit code 4: stopped before the end (`docker stop`, Ctrl-C), the rest is done by the next pass |
| Failed | any other exit code, such as 5 for a failed Lyrion carry-over |
| Interrupted | the pass crashed (its output ends with the traceback) or its process was killed, with no end recorded |

A pass whose process is gone is shown interrupted as soon as the dashboard sees it, when it ran in the same container; `watch` also marks the unfinished runs of its container as interrupted when it starts. A pass that found the lock taken is not kept in the history at all.

## Languages

The pages are in French or English, after the browser's preferred language, English by default. The output of the passes stays in English.

## Security

The dashboard has **no authentication**: it is meant for a trusted home network. Anyone who can reach its port sees the library's paths, the names in the Inbox and the output of every pass, and can run any command, applied when `watch` runs with `--apply`. Each form carries a token of the page, so another website open in a browser of the network cannot post to it. Never expose the port to the Internet (no port forwarding, no public reverse proxy); for remote access, join the home network through a VPN such as WireGuard or Tailscale.

## Endpoints

| Route | Returns |
|---|---|
| `/` | the overview |
| `/runs`, `/runs?command=enrich`, `/runs?problems=1` | the history |
| `/runs/<id>` | a run |
| `/runs/<id>/log.txt` | its output as plain text |
| `/runs/<id>/log.json?from=<offset>&kind=<kind>` | the complete lines written since byte `offset`, each with its style (`error`, `warning`, `section` or empty), the offset to ask from next, and the run's status; `kind` is the style of the line before `offset`, which an indented line continues |
| `POST /passes` | queues a dry run (`command`, its options, and `journal` for `undo`), or the pass applied with `apply=1`; redirects to the request |
| `POST /runs/<id>/apply` | queues a dashboard dry run again, applied |
| `/library?q=<words>&page=<n>` | the albums, searched |
| `/library/album?folder=<folder>` | an album, its folder relative to the library root; `POST` queues a dry run of its edits |
| `/library/cover?folder=<folder>` | the album's cover, or a placeholder without one |
| `POST /library/album/lyrics` | queues a dry run of the lyrics lookup of the album `folder` |
| `/library/lyrics` | the albums whose lyrics were not found |
| `/reviews`, `/reviews/split?from=<n>`, `/reviews/dedupe?from=<n>`, `/reviews/genres?from=<n>` | the reviews, from the question at position `n`; `POST` records an answer, `POST …/undo` takes it back |
| `/requests/<id>` | waits for a queued request, then redirects to its run |
| `/health` | `{"status": "ok", "version": …}`, for the compose healthcheck |

## For contributors

The package is `web/`: `app.py` (the pages, read on each request, and the forms), `passes.py` (the queue `watch` runs, and how each command is built from the options of `watch`), `library.py` (the albums from the cache, an album's fields, the edits of its form), `reviews.py` (the split, dedupe and genre reviews and their routes), `server.py` (waitress in a thread of `watch`), `i18n.py`, `templates/` and `static/`. Flask is only imported with `--listen`.

- Every string shown lives in `web/i18n.py`, in `fr` and `en` with the same keys; a test checks they match.
- The Content Security Policy is `default-src 'self'`: no inline script or style, everything from `static/`.
- The page scripts are plain JavaScript with no framework or build step, linted by `npx --yes eslint@9 src/music_librarian/web/static/*.js` (the CI lint job).
