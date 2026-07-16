# CLAUDE.md

Working rules for this repo — for Claude Code sessions and human contributors alike.

## Project in one paragraph

`timelapse-creator` captures frames from two off-grid Ski Bluewood webcams every 15 minutes
and will eventually build timelapse videos from the archive. See `README.md` for current
status, `docs/design.md` for architecture, and `docs/open-questions.md` for decisions still
open. Core principle: **archive everything raw, filter at build time** — never drop a frame
at capture time because it might not be needed later.

## Branching

Prefix every branch with the type of change, then a short kebab-case description:

```
feature/video-builder-cli
fix/stale-frame-false-positive
docs/update-open-questions
chore/bump-requests-version
refactor/split-archive-module
test/archive-stale-detection
ci/add-lint-workflow
```

Standard prefixes: `feature/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`, `ci/`,
`perf/`. Pick the one that best matches the primary intent of the change.

Exception: Claude Code web/cloud sessions get a harness-assigned branch name
(`claude/<slug>`) that can't be renamed mid-session — that's fine, no need to match the
scheme above for those.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `<type>: <summary>`,
matching the branch prefix — `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`,
`ci:`, `perf:`. Keep the summary imperative and under ~72 characters; add a body if the
*why* isn't obvious from the diff.

## Pull requests are required

All code changes land on `main` through a PR — no direct pushes. Before opening one:

- `ruff check .` and `black --check .` are clean
- `pytest` passes
- the PR uses `.github/pull_request_template.md` and describes *why*, not just *what*

**One exception:** `.github/workflows/capture.yml` commits frames straight to `main` every
15 minutes (`git add archive && git commit && git push`). That's a data-only automated job,
not a code change — it stays outside the PR requirement. Don't try to route it through PRs
and don't be surprised by `capture-bot` commits in `git log`.

## Code style

- Python, formatted with **black**, linted with **ruff** (config in `pyproject.toml`).
- Type hints are welcome on new/touched code; not a retrofitting project.
- No premature abstraction — this is a small pipeline, keep it that way until the video
  builder actually needs the complexity.

## Testing

- `pytest`, tests live in `tests/`, mirroring the `capture/` package layout.
- Any change to `capture/archive.py`'s hash/stale-detection logic needs a test — that's the
  one piece of this codebase where a silent regression (e.g. archiving duplicate stale
  frames) is easy to ship and hard to notice.
- CI (`.github/workflows/ci.yml`) runs ruff, black --check, and pytest on every PR.

## Docs

Keep `docs/design.md` and `docs/open-questions.md` current as decisions get made — they're
the actual design record for this project, not just onboarding material. Update the
"What's implemented" section of `README.md` when scope changes.
