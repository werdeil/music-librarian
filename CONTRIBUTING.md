# Contributing

Bug reports, ideas and pull requests are welcome. For anything bigger than a fix, open an issue first so we can agree on the approach.

## Setting up

You need Python 3.12 and ffmpeg (the tests build real audio files with it).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -e .
```

r128gain is only needed to run `enrich` for real, not for the tests.

## Checks

Run the same checks as the CI before opening a pull request:

```bash
python -m pytest -q
pylint src tests
```

Both must pass, and pylint must stay at 10/10. The rules and the few disabled checks are explained in `pyproject.toml`.

The CI also builds the Docker image, which runs the tests again inside the container and checks that r128gain works there. You can do the same with `docker compose build`.

## Code

[docs/architecture.md](docs/architecture.md) maps the package. A few conventions:

- Every command is a dry run unless `--apply` is passed, and a dry run changes nothing.
- Every move goes through the journal, so a pass can always be undone.
- Messages, help and comments are in English. Log lines start with an 8-character tag (`MOVE`, `CONFLICT`, `ERROR`…) so they line up.
- Imports inside the package are absolute: `from music_librarian.core.lock import STOP`.
- The tests mirror the package under `tests/`, and new behaviour comes with a test. Tests that touch audio build real files with ffmpeg rather than mocking mutagen.

## Pull requests

- Keep a pull request to one change.
- Give the pull request a title that says what changes for users: release notes are generated from the titles of the merged pull requests.
- Write commit messages in English, with a short summary line and a body explaining why.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
