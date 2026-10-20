<!--
Thanks for contributing! Keep this short: describe the change and confirm the checks. Delete any checklist line that does not apply to your PR. See CONTRIBUTING.md for the conventions and how to run each check.
-->

## Problem

<!-- What is broken, missing or needed: the state of things without this PR. Link any related issue (e.g. Closes #123). -->

## Solution

<!-- What this PR does about it, and anything a reviewer would be surprised by. -->

## How it was tested

<!-- What is specific to this change: commands run on a real or generated library, dry-run output, and anything left unverified. -->

## Checklist

- [ ] The title follows Conventional Commits (`type(scope): summary`), and title and body describe the branch as it now stands
- [ ] `python -m pytest -q` passes, with tests added or updated for the change
- [ ] `pylint src tests` stays at 10/10
- [ ] Without `--apply`, the commands still change nothing, and every move still goes through the journal
- [ ] New messages, help and comments are in English, and log lines keep their 8-character tag
- [ ] Documentation updated if user-facing behaviour changed: `README.md` and `README.fr.md` together, and `docs/*.md`
- [ ] Docker touched → `docker compose build` passes (it runs the tests in the image)
