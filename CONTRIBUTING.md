# Contributing

Bug reports, ideas and pull requests are welcome. For anything bigger than a fix, open an issue first so we can agree on the approach.

## Setting up

You need Python 3.12 and ffmpeg (the tests build real audio files with it).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -e .
```

## Checks

Run the same checks as the CI before opening a pull request:

```bash
python -m pytest -q
pylint src tests
```

Both must pass, and pylint must stay at 10/10. The rules and the few disabled checks are explained in `pyproject.toml`.

The CI also builds the Docker image, which runs the tests again inside the container and checks that ffmpeg measures loudness there. You can do the same with `docker compose build`.

## Code

[docs/architecture.md](docs/architecture.md) maps the package. A few conventions:

- Every command is a dry run unless `--apply` is passed, and a dry run changes nothing.
- Every move goes through the journal, so a pass can always be undone.
- Messages, help and comments are in English. Log lines start with an 8-character tag (`MOVE`, `CONFLICT`, `ERROR`…) so they line up.
- Imports inside the package are absolute: `from music_librarian.core.lock import STOP`.
- The tests mirror the package under `tests/`, and new behaviour comes with a test. Tests that touch audio build real files with ffmpeg rather than mocking mutagen.

## Commits and pull requests

Commit subjects and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`.

- Types: `feat`, `fix`, `refactor`, `docs`, `chore`.
- The scope is the area touched (`organize`, `import`, `watch`, `enrich`, `lyrics`, `lyrion`, `docker`, `ci`, `deps`), left out when the change is repo-wide.
- The summary is lowercase, imperative, with no trailing period, and short enough to read in `git log --oneline`. For example: `fix(organize): keep a title's leading dot after the track number`.
- The commit body, in English and wrapped at about 72 columns, explains why.

Pull requests are squash-merged, so the title becomes the commit subject on `main`, and the release notes are generated from those titles. The `Pull request` workflow rejects a title that breaks the form, and labels the pull request with its type and the areas its files touch; the release notes are grouped by those labels.

Keep a pull request to one change, and fill in the template: the problem, the solution, how it was tested, and the checklist.

## Releasing

Every push to `main` that touches more than documentation publishes the image as `ghcr.io/werdeil/music-librarian:dev`. To cut a version, first set `__version__` in `src/music_librarian/__init__.py` through a pull request, since `main` only takes pull requests. Then run the `Release` workflow from the Actions tab on `main`, the version field left empty or set to the same value: it tags `main` as `v0.1.0` and opens a draft release with notes generated from the merged pull requests. Review the notes and publish the draft: the `Publish` workflow then pushes the image as `0.1.0`, `0.1` and `latest` (only `0.1.0` for a pre-release).

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
