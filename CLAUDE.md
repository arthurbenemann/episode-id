# Notes for Claude Code

Read [docs/SPEC.md](docs/SPEC.md) first — it contains the full design, output
format requirements, and milestone breakdown.

## Conventions

- Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, etc.).
  `cliff.toml` generates the changelog from these on release.
- Always run `make lint && make test` before committing.
- Output paths must follow [Jellyfin's TV show
  layout](https://jellyfin.org/docs/general/server/media/shows/) — see the
  "Output format" section in `docs/SPEC.md`. The `app/core/renamer.py` module
  is the single source of truth for path construction; route all naming
  decisions through it rather than building paths ad hoc.
- `app/cli.py` is the M1 entrypoint. The FastAPI layer (M2) should import
  from `app/core/*` and `app/providers/*` — not from `cli.py`.

## Architecture

```
app/
├── cli.py            # M1 entrypoint
├── config.py         # pydantic-settings, reads .env
├── core/
│   ├── extractor.py  # ffmpeg + pysubs2 subtitle extraction
│   ├── matcher.py    # rapidfuzz + Hungarian assignment
│   └── renamer.py    # Jellyfin path construction + atomic moves
└── providers/
    ├── base.py       # SubtitleProvider ABC
    └── chakoteya.py  # Star Trek transcripts (no API key required)
```

## Adding a new provider

1. Subclass `SubtitleProvider` in `app/providers/`.
2. Implement `fetch_season(series_key, season) -> list[EpisodeTranscript]`.
3. Add a CLI flag in `cli.py` to select it.
4. Write tests against captured fixtures (don't hit live APIs in CI).

## Testing strategy

- Fast unit tests for `matcher`, `renamer`, and provider parsers (all run
  without network or ffmpeg).
- Integration tests for `extractor` need real MKVs with subtitle streams —
  put a tiny sample in `tests/fixtures/` if you write one.
- The Chakoteya tests use a captured HTML fixture so they pass offline.

## Don't

- Don't add overwrite-by-default behaviour to the renamer. It must always
  refuse to overwrite existing files.
- Don't hardcode subtitle stream selection — the file may have multiple
  English tracks, foreign-only forced subs, etc. Use `pick_best_stream`.
- Don't bypass `sanitize_component` when building filenames. Reserved
  characters break Jellyfin's matching in subtle ways.
