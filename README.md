# EpisodeID

Identify TV episode MKV files by fuzzy-matching their embedded subtitles against
canonical episode transcripts, then rename them into a [Jellyfin-compatible
layout](https://jellyfin.org/docs/general/server/media/shows/).

Built for the case where MakeMKV (or any disc ripper) emits files like
`title_t00.mkv`, `title_t04.mkv`, ... in random order and you don't want to
play each one to figure out which episode it is.

## How it works

1. **Extract subtitles** from each MKV with `ffmpeg`.
2. **Fetch canonical transcripts** for the selected show + season from a
   provider (currently `chakoteya.net` for Star Trek; OpenSubtitles support
   coming in a later milestone).
3. **Fuzzy-match** each file's dialogue sample against every candidate
   episode using `rapidfuzz.token_set_ratio`.
4. **Globally assign** files to episodes via the Hungarian algorithm so two
   files can't both be matched to the same episode.
5. **Propose renames** into Jellyfin layout. Dry-run by default; `--apply`
   moves the files.

## Quickstart (CLI, M1)

Requirements: Python 3.12+, `ffmpeg` + `ffprobe` on `PATH`.

```bash
pip install -e .

episode-id \
    --folder ~/rips/TNG-S03 \
    --show tng \
    --season 3 \
    --series-title "Star Trek The Next Generation" \
    --year 1987 \
    --tvdb-id 71470 \
    --library-root /media/tv
```

By default this is a dry-run. Inspect the proposed table, then add `--apply` to
actually move the files.

Supported `--show` values for the Chakoteya provider:
`tos`, `tas`, `tng`, `ds9`, `voy`, `ent`, `dis`.

## Output layout

```
/media/tv/
└── Star Trek The Next Generation (1987) [tvdbid-71470]/
    └── Season 03/
        ├── Star Trek The Next Generation - S03E01 - Evolution.mkv
        ├── Star Trek The Next Generation - S03E02 - The Ensigns of Command.mkv
        └── ...
```

## API (M2)

The same pipeline is exposed over HTTP. Run the server with `make dev` (or
`uvicorn app.main:app --port 8080`) and drive it with curl:

```bash
# Kick off a scan — returns {"job_id": "..."}
curl -s http://localhost:8080/scan -H 'content-type: application/json' -d '{
  "folder": "/abs/path/to/rips/TNG-S03",
  "series_key": "tng",
  "season": 3,
  "series_title": "Star Trek The Next Generation",
  "year": 1987,
  "tvdb_id": 71470,
  "library_root": "/media/tv"
}'

# Poll status
curl -s http://localhost:8080/jobs/$JOB_ID

# Inspect the proposed Jellyfin mapping
curl -s http://localhost:8080/jobs/$JOB_ID/results

# Dry-run (default) or apply
curl -s http://localhost:8080/jobs/$JOB_ID/apply \
    -H 'content-type: application/json' -d '{"confirm": true}'
```

## Roadmap

- [x] **M1** — CLI prototype with Chakoteya provider and Jellyfin renamer
- [x] **M2** — FastAPI wrapper around the same logic
- [ ] **M3** — htmx web UI
- [ ] **M4** — OpenSubtitles provider (works beyond Trek)
- [ ] **M5** — PGS / VobSub OCR for Blu-ray rips without text subs
- [ ] **M6** — CI/CD release pipeline publishing to ghcr.io

See [docs/SPEC.md](docs/SPEC.md) for the full design.

## Development

```bash
make install         # editable install + dev deps
make test            # pytest
make lint            # ruff check + format check
make fmt             # ruff format
```

## License

MIT — see [LICENSE](LICENSE).
