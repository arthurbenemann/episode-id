# Contributing

## Local setup

Requirements: Python 3.12+, `ffmpeg` and `ffprobe` on `PATH`.

```bash
git clone https://github.com/yourname/episode-id.git
cd episode-id
python -m venv .venv && source .venv/bin/activate
make install
make test
```

## Workflow

- Branch from `main`.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit
  messages — `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`,
  `perf:`. The release pipeline generates `CHANGELOG.md` from these.
- Run `make lint && make test` before opening a PR.
- One concern per PR; smaller is better.

## Release flow

Releases are cut from `main`:

```bash
git tag v0.2.0
git push --tags
```

The `release.yml` GitHub Action:
1. Generates release notes from conventional commits via `git-cliff`.
2. Builds a multi-arch Docker image (amd64 + arm64).
3. Publishes to `ghcr.io/<owner>/episode-id`.
4. Creates a GitHub Release with the generated notes attached.

## Project structure

See [CLAUDE.md](CLAUDE.md) for the architecture overview and the
[docs/SPEC.md](docs/SPEC.md) for the full design.

## Reporting issues

For security vulnerabilities, see [SECURITY.md](SECURITY.md). For everything
else, open a regular GitHub issue.
