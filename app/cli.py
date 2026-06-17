"""Command-line interface for episode-id (Milestone 1).

Usage:
    episode-id --folder ~/rips/TNG-S03 --show tng --season 3 --tvdb-id 71470 \\
        --series-title "Star Trek: The Next Generation" --year 1987 \\
        --library-root /media/tv

By default the CLI runs in dry-run mode and prints the proposed Jellyfin layout
without touching any files. Pass `--apply` to actually move them.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from app.config import settings
from app.core import extractor, matcher, renamer
from app.db import TranscriptCache
from app.providers.base import SubtitleProvider
from app.providers.registry import PROVIDER_NAMES, create_provider

app = typer.Typer(
    add_completion=False,
    help="Identify TV episode MKVs by subtitle fuzzy-matching and rename them for Jellyfin.",
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _gather_files(folder: Path) -> list[Path]:
    files = extractor.find_mkv_files(folder)
    if not files:
        console.print(f"[red]No .mkv files found under {folder}[/red]")
        raise typer.Exit(code=1)
    return files


def _extract_samples(files: list[Path]) -> list[matcher.FileSample]:
    samples: list[matcher.FileSample] = []
    with console.status("[bold]Extracting subtitles[/]") as status:
        for f in files:
            status.update(f"Extracting subtitles from [cyan]{f.name}[/cyan]")
            try:
                result = extractor.extract_subtitles(f)
            except Exception as exc:
                console.print(f"[yellow]warn:[/] failed to extract {f.name}: {exc}")
                continue
            if result is None:
                console.print(
                    f"[yellow]warn:[/] no text subtitles in {f.name} (PGS/VobSub OCR is M5)"
                )
                continue
            dialogue = result.dialogue_after(
                start_ms=settings.sample_start_seconds * 1000,
                line_count=settings.sample_line_count,
            )
            samples.append(matcher.FileSample(path=f, dialogue=dialogue))
    return samples


def _fetch_episodes(
    provider: SubtitleProvider,
    series_key: str,
    season: int,
) -> list[matcher.EpisodeReference]:
    status = f"[bold]Fetching transcripts for {series_key} S{season:02d} via {provider.name}[/]"
    with console.status(status):
        transcripts = provider.fetch_season(series_key, season)
    return [
        matcher.EpisodeReference(
            season=t.season,
            episode=t.episode,
            title=t.title,
            transcript=t.text,
        )
        for t in transcripts
    ]


def _build_plans(
    matches: list[matcher.Match],
    *,
    series: renamer.SeriesInfo,
    library_root: Path,
    include_provider_id: bool,
) -> list[renamer.RenamePlan]:
    plans: list[renamer.RenamePlan] = []
    for m in matches:
        ep = m.best.episode
        destination = renamer.build_path(
            library_root=library_root,
            series=series,
            season=ep.season,
            episode=ep.episode,
            episode_title=ep.title,
            extension=m.file.path.suffix,
            include_provider_id=include_provider_id,
        )
        plans.append(
            renamer.RenamePlan(
                source=m.file.path,
                destination=destination,
                confidence=m.confidence,
                needs_review=m.needs_review,
            )
        )
    return plans


def _print_plans(plans: list[renamer.RenamePlan]) -> None:
    table = Table(
        title="Proposed renames (Jellyfin layout)",
        show_lines=False,
        header_style="bold",
    )
    table.add_column("Source")
    table.add_column("→ Destination")
    table.add_column("Conf", justify="right")
    table.add_column("Review", justify="center")

    for p in plans:
        conf = f"{p.confidence:.0f}"
        conf_style = "green" if p.confidence >= 80 else "yellow" if p.confidence >= 60 else "red"
        review = "[red]●[/red]" if p.needs_review else ""
        # Show the destination relative to its library_root grandparent so the
        # output stays readable in narrow terminals.
        dest_display = str(p.destination)
        table.add_row(
            p.source.name,
            dest_display,
            f"[{conf_style}]{conf}[/{conf_style}]",
            review,
        )

    console.print(table)


@app.command()
def rename(
    folder: Path = typer.Option(
        ...,
        "--folder",
        "-f",
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Folder of MKV files to identify (subfolders are searched too).",
    ),
    show: str | None = typer.Option(
        None,
        "--show",
        "-s",
        help="Series key for the chakoteya provider (e.g. 'tng' for Star Trek: TNG).",
    ),
    provider: str = typer.Option(
        "chakoteya",
        "--provider",
        "-p",
        help="Transcript provider: 'chakoteya' (Star Trek) or 'opensubtitles' (any show).",
    ),
    season: int = typer.Option(..., "--season", "-S", help="Season number."),
    series_title: str = typer.Option(
        ...,
        "--series-title",
        help="Display title used in output filenames (e.g. 'Star Trek The Next Generation').",
    ),
    year: int | None = typer.Option(
        None,
        "--year",
        help="Series first-aired year, included in the series folder name.",
    ),
    tvdb_id: int | None = typer.Option(
        None,
        "--tvdb-id",
        help="TVDB id, embedded as [tvdbid-NNN] in the series folder.",
    ),
    library_root: Path = typer.Option(
        None,
        "--library-root",
        help="Output root (defaults to LIBRARY_ROOT env or /media/tv).",
    ),
    no_provider_id: bool = typer.Option(
        False,
        "--no-provider-id",
        help="Omit the [tvdbid-NNN] suffix even if --tvdb-id is given.",
    ),
    apply_changes: bool = typer.Option(
        False,
        "--apply",
        help="Actually move the files. Default is dry-run.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Identify episodes in FOLDER and rename them into Jellyfin layout."""
    _setup_logging(verbose)
    lib_root = library_root or settings.library_root

    provider_name = provider.lower()
    if provider_name not in PROVIDER_NAMES:
        console.print(
            f"[red]Unknown provider '{provider}'. Known: {', '.join(PROVIDER_NAMES)}[/red]"
        )
        raise typer.Exit(code=1)
    if provider_name == "opensubtitles":
        if tvdb_id is None:
            console.print("[red]--provider opensubtitles requires --tvdb-id[/red]")
            raise typer.Exit(code=1)
        series_key = str(tvdb_id)
    else:
        if show is None:
            console.print("[red]--show is required for the chakoteya provider[/red]")
            raise typer.Exit(code=1)
        series_key = show

    console.print(f"[bold]Folder:[/]        {folder}")
    console.print(f"[bold]Show / season:[/] {series_key} S{season:02d} ({provider_name})")
    console.print(f"[bold]Library root:[/]  {lib_root}")
    console.print(f"[bold]Mode:[/]          {'APPLY' if apply_changes else 'dry-run'}\n")

    files = _gather_files(folder)
    samples = _extract_samples(files)
    if not samples:
        console.print("[red]No usable subtitle samples extracted. Aborting.[/red]")
        raise typer.Exit(code=1)

    provider_obj = create_provider(
        provider_name,
        cache=TranscriptCache(settings.cache_db_path),
    )
    episodes = _fetch_episodes(provider_obj, series_key, season)
    console.print(f"Loaded [bold]{len(episodes)}[/] episodes from {provider_name}.\n")

    matches = matcher.match(samples, episodes)

    series = renamer.SeriesInfo(title=series_title, year=year, tvdb_id=tvdb_id)
    plans = _build_plans(
        matches,
        series=series,
        library_root=lib_root,
        include_provider_id=not no_provider_id,
    )

    _print_plans(plans)

    if not apply_changes:
        console.print(
            "\n[dim]Dry-run only. Re-run with [bold]--apply[/bold] to perform these moves.[/dim]"
        )
        return

    errors = renamer.apply_plans(plans, dry_run=False)
    if errors:
        console.print(f"\n[red]Completed with {len(errors)} error(s).[/red]")
        raise typer.Exit(code=2)
    console.print("\n[green]Done.[/green]")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[red]Aborted by user.[/red]")
        sys.exit(130)


if __name__ == "__main__":
    main()
