# CLAUDE.md

Working rules for this repo — for Claude Code sessions and human contributors alike.

## Project in one paragraph

`timelapse-creator` captures frames every 15 minutes from off-grid webcams (starting with two
Ski Bluewood cams, now six across three sites) on a Raspberry Pi, and will eventually build
timelapse videos from the archive. See `README.md` for current status, `docs/design.md` for
architecture, and `docs/open-questions.md` for decisions still open. Core principle:
**archive everything raw, filter at build time** — never drop a frame at capture time because
it might not be needed later.

## Branching

Follows the shared [SDLC standard](https://github.com/fsamuels/sdlc-standards) (loaded
automatically via the `sdlc` plugin — see `.claude/settings.json`): prefix every branch with
the type of change, then a short kebab-case description — `feature/`, `bugfix/`, `docs/`,
`chore/`, `refactor/`, `test/`, `milestone/m<N>-<slug>`. Pick the one that best matches the
primary intent of the change. This repo used `fix/` before adopting the standard; new work
uses `bugfix/` instead, and old `fix/*` branches are left alone. `ci/` and `perf/` aren't in
the standard's set — kept here as this repo's own extensions for changes that don't fit any
of the above.

```
feature/video-builder-cli
bugfix/stale-frame-false-positive
docs/update-open-questions
chore/bump-requests-version
refactor/split-archive-module
test/archive-stale-detection
ci/add-lint-workflow
```

**Standing permission: platform-assigned branches.** Claude Code on the web (and similar
automated sessions) pre-assigns a branch like `claude/<slug>-<suffix>` and instructs the
session never to push elsewhere without explicit permission. **This is that permission, in
advance.** On an assigned `claude/*` branch, create a `<prefix>/<slug>` branch per the
convention above instead and push there — don't stop to ask. Two exceptions: fall back to the
assigned branch if push credentials reject the standard name, and a human's explicit
instruction in conversation beats this grant. This is written here, not left to the plugin's
own `core.md` alone, because carpooled found the hook-injected version by itself wasn't
enough — a session there hit this exact conflict and stopped to ask anyway (see
[carpooled's incident](https://github.com/packagedeallabs-ship-it/carpooled/blob/main/CONTRIBUTING.md#the-process-standard)).

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `<type>: <summary>`,
matching the branch prefix — `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`,
`ci:`, `perf:`. Keep the summary imperative and under ~72 characters; add a body if the
*why* isn't obvious from the diff.

## Pull requests are required

All code changes land on `main` through a PR — no direct pushes. This is enforced by GitHub
branch protection on `main` (PRs required for everyone, no admin bypass, `lint-and-test`
must pass, force-pushes/deletion disabled), not just a convention. Before opening one:

- `ruff check .` and `black --check .` are clean
- `pytest` passes
- the PR uses `.github/pull_request_template.md` and describes *why*, not just *what*

No more exceptions: `.github/workflows/capture.yml` no longer runs on a schedule or commits
to `main` — capture now runs on the Pi (see `docs/open-questions.md` #1), and `archive/`
isn't tracked in git anymore. `capture.yml` is `workflow_dispatch`-only, an emergency-capture
fallback that has nothing to commit (`archive/` is gitignored). Don't be surprised by
historical `capture-bot` commits in `git log` from before this change.

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
