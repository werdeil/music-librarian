# Dashboard

`watch --listen [HOST:]PORT` serves a dashboard on that address while it watches the Inbox: the passes running and the recent ones with their output, the schedule, the Inbox and the credits awaiting a decision. The compose file serves it on port 8080 (`WEB_PORT` in `.env` to change the host's port); without a host, every interface is listened on. `MUSIC_LIBRARIAN_LISTEN` sets the address when `--listen` is not given.

The pages read the run history and the decisions file in the state folder, and the Inbox root, on each request. The passes the dashboard asks for are queued and run by `watch` itself, between its other jobs, so they never compete with them for the lock. The badge at the top says whether `watch` runs with `--apply`.

## Pages

- **Overview**: the passes running now, the schedule of `watch` (how often the Inbox is read, the nightly `organize` and the weekly `enrich`) with each command's last run and next slot, the items waiting in the Inbox and in `_failed/`, the number of credits awaiting a decision in `split-decisions.txt`, and the ten latest runs.
- **History**: the runs kept in the history (the 200 latest), filtered by command, or to the problems only.
- **A run**: its mode, who started it (`watch`, the command line), its duration and exit code, its summary, and its whole output, each line coloured by its tag (`ERROR`, `CONFLICT` and `FAILED` in red, `SKIPPED`, `WARNING`, `REFUSED`, `UNKNOWN`, `GUARDED`, `REVIEW`, `STOP` and `LOCKED` in yellow). While the run goes on, the new lines are appended every 2 seconds, and the page reloads with the summary once it ends. "Plain text" gives the output as a file.

Times are the container's (`TZ` in `.env`).

## Running a pass

The overview has a form per command: `import`, `organize` (`--since` ticked by default), `enrich` (`--rescan`, `--no-split`, `--no-dedupe`), `scan` (`--force`), `split` and `dedupe`. Each starts as a dry run and opens the run's page, which follows it live once `watch` gets to it; while a pass is running, the request waits in the queue, listed on the overview.

The page of a dry run started from the dashboard then offers **Apply this pass**: the same command with the same options, applied. It is a new pass, which plans again from the library as it then is. The page of an applied pass that left a journal offers **Undo this pass**, again as a dry run first. Nothing is applied from the dashboard unless `watch` runs with `--apply`: without it, the dashboard runs dry runs only.

A request that finds the lock taken (a command run by hand, `docker compose run`) is not kept, and its page says so. The queue lives in `watch`: a restart drops the requests not yet started. The `--review` modes of `split` and `dedupe` ask questions and stay on the command line.

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
| `/requests/<id>` | waits for a queued request, then redirects to its run |
| `/health` | `{"status": "ok", "version": …}`, for the compose healthcheck |

## For contributors

The package is `web/`: `app.py` (the pages, read on each request, and the forms), `passes.py` (the queue `watch` runs, and how each command is built from the options of `watch`), `server.py` (waitress in a thread of `watch`), `i18n.py`, `templates/` and `static/`. Flask is only imported with `--listen`.

- Every string shown lives in `web/i18n.py`, in `fr` and `en` with the same keys; a test checks they match.
- The Content Security Policy is `default-src 'self'`: no inline script or style, everything from `static/`.
- The page scripts are plain JavaScript with no framework or build step, linted by `npx --yes eslint@9 src/music_librarian/web/static/*.js` (the CI lint job).
