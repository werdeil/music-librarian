# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A command-line tool, shipped as a single Docker container, that keeps a music library filed the way Apple Music does, imports new music from a drop folder (the Inbox), fills in missing ReplayGain, covers and lyrics, splits the composer and artist credits that hold several names, and unifies one artist spelled several ways. Commands: `organize`, `import`, `enrich`, `split`, `dedupe`, `scan` (the metadata cache), `watch` (the container's long-running process) and `undo`. It can carry play counts and ratings over into a Lyrion Music Server database when files move. See the README for what each command does and `docs/` for the details.

## Commands

The package targets **Python 3.12** and installs as `music_librarian` from `src/`. The tests need ffmpeg.

```bash
pip install -r requirements-dev.txt -e .

python -m pytest -q          # tests (CI: tests job)
pylint src tests             # lint, must stay at 10/10 (CI: lint job)
docker compose build         # the image, which runs the tests again inside it (CI: image job)

music-librarian organize --root /path/to/library     # dry run
music-librarian import --inbox /path/to/inbox --root /path/to/library
```

Pylint is configured in `pyproject.toml`: docstring-required checks and `broad-exception-caught` are disabled, lines go up to 110 characters. Match those norms rather than fighting them.

## Architecture

[docs/architecture.md](docs/architecture.md) maps the package: `core/` (log, journal, lock and stop handling, state), `organize/`, `importer.py`, `enrich/`, `split/`, `dedupe/`, `lyrics/` (one module per provider), `media/`, `integrations/lyrion.py`, `scan.py`, `watch.py` and `cli.py`. The naming rules are in [docs/naming-rules.md](docs/naming-rules.md), the credit split in [docs/splitting.md](docs/splitting.md), the artist aliases in [docs/dedupe.md](docs/dedupe.md), the Lyrion carry-over in [docs/lyrion.md](docs/lyrion.md).

Imports inside the package are absolute (`from music_librarian.core.lock import STOP`). The tests mirror the package under `tests/` and run with `--import-mode=importlib`.

## Behaviour that must not regress

- **Dry run by default.** Without `--apply`, no command changes anything in the library or the Inbox; only the metadata cache and the run history in the state folder are written. Every move goes through the journal (`core/journal.py`), so a pass can always be undone with `undo`.
- **Messages are in English**, and each log line starts with an 8-character tag (`MOVE`, `CONFLICT`, `SKIPPED`, `ERROR`, `STOP`, `LOCKED`…) so the output lines up; summaries read `key: value`.
- **Tests never touch a real library.** They build their audio files with ffmpeg in a temporary folder (`tests/conftest.py`), and never reach a real Lyrion database or a lyrics provider.

## Documentation

**`README.md` and `README.fr.md` are kept in lockstep**: a change to one lands in the other in the same PR. The pages under `docs/` are in English only, and every link to them from the French README is flagged "(en anglais)".

## Code style & comments

**Comments are the exception, not the norm.** The default is no comment: intent belongs in naming and small functions, not in prose. When you touch a block, delete the comments in it that the rules below don't justify.

A comment (or docstring) must earn its place. The only things that justify one:

- **A constraint the code cannot show**: a locking or ordering rule, an invariant that a refactor could silently break.
- **An external quirk**: Apple Music's naming, Lyrion's URL encoding, a provider blocking default user agents. Things no amount of local reading would reveal.
- **An accepted risk**: a deliberate trade-off a reviewer would otherwise flag.
- **A public contract**: a docstring stating arguments, return shape and side effects, when they are not obvious from the name.

Rules for the comments that do survive:

- **Two lines max** for any code comment, block or inline. If the rationale needs a paragraph, it belongs in the commit message or the PR description.
- **No motivation, no problem-story in the code.** State the surviving constraint in a clause and stop, never the symptom it fixes or why the value is what it is. That is the commit message's job.
- **Timeless: describe the state, not the change.** Write "the lock is reentrant within a process", never "made the lock reentrant".
- **Never narrate the obvious**: what the next line does, section banners, restating a name.

## Commits and pull requests

**Commit subjects and PR titles follow Conventional Commits: `type(scope): summary`.** PRs are squash-merged, so the title becomes the commit subject on `main` and a line of the release notes. Types: `feat`, `fix`, `refactor`, `docs`, `chore`. The scope is the area touched (`organize`, `import`, `watch`, `enrich`, `split`, `dedupe`, `lyrics`, `lyrion`, `docker`, `ci`, `deps`), dropped when the change is repo-wide. Lowercase after the colon, imperative, no trailing period, short enough to read in `git log --oneline`. The `Pull request` workflow fails a title that breaks this form and labels the PR with its type and the areas its files touch (`.github/labeler.yml`); the release notes are grouped by those labels (`.github/release.yml`).

**PR titles, PR descriptions and commit messages are written in English.** Only the READMEs are bilingual.

**When opening a PR, follow `.github/pull_request_template.md`**: fill in the problem, the solution and how it was tested, and work through its checklist.

**Keep a PR body short.** "Problem" is the state of things without the PR, in a paragraph, never the investigation that found it. "Solution" is what the branch does about it. "How it was tested" covers only what the checklist doesn't: the verification specific to this change, and anything left unverified. Measurements, tables and alternatives considered belong in the commit message. **The three sections together stay under 200 words**: count them.

**Markdown is never hard-wrapped: one paragraph is one line, however long.** That holds for the files in this repo (CLAUDE.md, the READMEs, `docs/`, the PR template) and for PR, issue and review bodies alike. Only prose joins up: code fences, tables and list items keep their line breaks. The commit message is the exception: it stays wrapped at about 72 columns because git shows it verbatim.

**Never post progress commentary on a PR.** A comment is for something the maintainer has to act on or could not get from the branch, not "pushed a follow-up" or "fixed the regression". Pushing commits is how progress is reported; the commit messages carry the reasoning, and the description carries the current state.

**A PR describes the branch as it now stands, not how it got there.** Before calling a PR ready, every time, re-read the title and body against `git diff origin/main...HEAD` (with the `origin/`: a local `main` can lag). The title is the squash-merge commit subject, so it has to name what finally landed.

**Assign every PR to its author as soon as it is opened**: the GitHub account that opened it, read from the PR itself. When the tool that opens the PR takes no assignee, set it straight after through the issue API (a PR is an issue).

**Never merge a PR** on this repo: merging is the maintainer's call, always. Prepare and push the branch, open or update the PR, and leave the merge to a human.
