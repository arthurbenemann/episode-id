"""Build Jellyfin-compatible output paths and apply renames.

Reference: https://jellyfin.org/docs/general/server/media/shows/

Layout:
    <library_root>/
    └── <Series Title> (<Year>) [tvdbid-<id>]/
        ├── Season 00/                          # specials
        ├── Season 01/                          # zero-padded
        │   ├── <Series Title> - S01E01 - <Title>.mkv
        │   └── <Series Title> - S01E02 - <Title>.mkv
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# Reserved on Windows + cause problems for Jellyfin per its docs.
RESERVED_CHARS = re.compile(r'[<>:"/\\|?*]')

# Repeated whitespace runs created when we strip reserved chars.
COLLAPSE_WS = re.compile(r"\s+")


def sanitize_component(name: str) -> str:
    """Make a string safe for use as a single path component.

    Replaces reserved characters with a space, collapses runs of whitespace,
    strips trailing periods (Windows refuses them), and special-cases the
    `M*A*S*H` → `MASH` pattern that Jellyfin docs call out.
    """
    # Special case the Jellyfin docs mention: collapse `A*B*C` runs first so we
    # don't end up with `A B C` (which would also be valid, but Jellyfin
    # specifically suggests the joined form).
    cleaned = re.sub(r"(?<=\w)\*(?=\w)", "", name)
    cleaned = RESERVED_CHARS.sub(" ", cleaned)
    cleaned = COLLAPSE_WS.sub(" ", cleaned).strip()
    # Windows hates trailing dots and spaces.
    cleaned = cleaned.rstrip(". ")
    return cleaned


@dataclass(frozen=True)
class SeriesInfo:
    title: str
    year: int | None = None
    tvdb_id: int | None = None


@dataclass(frozen=True)
class RenamePlan:
    """One proposed file move."""

    source: Path
    destination: Path
    confidence: float
    needs_review: bool

    def __str__(self) -> str:  # pragma: no cover - debug only
        flag = " [REVIEW]" if self.needs_review else ""
        return f"{self.source.name} -> {self.destination}{flag}"


def build_series_folder(
    series: SeriesInfo,
    *,
    include_provider_id: bool = True,
) -> str:
    """Build the top-level series folder name in Jellyfin's preferred form."""
    parts = [sanitize_component(series.title)]
    if series.year is not None:
        parts.append(f"({series.year})")
    if include_provider_id and series.tvdb_id is not None:
        parts.append(f"[tvdbid-{series.tvdb_id}]")
    return " ".join(parts)


def build_season_folder(season: int) -> str:
    """Build the per-season folder name. Always `Season XX`, zero-padded."""
    return f"Season {season:02d}"


def build_episode_filename(
    series: SeriesInfo,
    season: int,
    episode: int,
    episode_title: str,
    extension: str,
    *,
    end_episode: int | None = None,
) -> str:
    """Build the per-episode filename.

    If `end_episode` is provided, builds a multi-episode filename like
    `Series - S01E01-E02 - Title.ext`.
    """
    series_title = sanitize_component(series.title)
    ep_title = sanitize_component(episode_title) if episode_title else ""

    if end_episode is not None and end_episode != episode:
        ep_part = f"S{season:02d}E{episode:02d}-E{end_episode:02d}"
    else:
        ep_part = f"S{season:02d}E{episode:02d}"

    ext = extension.lstrip(".")
    if ep_title:
        return f"{series_title} - {ep_part} - {ep_title}.{ext}"
    return f"{series_title} - {ep_part}.{ext}"


def build_path(
    library_root: Path,
    series: SeriesInfo,
    season: int,
    episode: int,
    episode_title: str,
    extension: str,
    *,
    include_provider_id: bool = True,
    end_episode: int | None = None,
) -> Path:
    """Build the full Jellyfin-compatible destination path for an episode."""
    return (
        library_root
        / build_series_folder(series, include_provider_id=include_provider_id)
        / build_season_folder(season)
        / build_episode_filename(
            series,
            season,
            episode,
            episode_title,
            extension,
            end_episode=end_episode,
        )
    )


def apply_plan(plan: RenamePlan, *, dry_run: bool = True) -> None:
    """Move one file according to a `RenamePlan`.

    - Never overwrites an existing destination.
    - Uses `os.replace` (atomic) on the same filesystem.
    - Falls back to `shutil.move` across filesystems.
    """
    if plan.destination.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {plan.destination}")

    if dry_run:
        log.info("[dry-run] would move %s -> %s", plan.source, plan.destination)
        return

    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(plan.source, plan.destination)
    except OSError:
        # Cross-device — fall back to copy + delete.
        shutil.move(str(plan.source), str(plan.destination))
    log.info("moved %s -> %s", plan.source, plan.destination)


def apply_plans(plans: list[RenamePlan], *, dry_run: bool = True) -> list[Exception]:
    """Apply a batch of plans. Returns any errors encountered, in order.

    Continues on per-file errors rather than aborting the whole batch.
    """
    errors: list[Exception] = []
    for plan in plans:
        try:
            apply_plan(plan, dry_run=dry_run)
        except Exception as exc:
            log.exception("failed to apply %s", plan)
            errors.append(exc)
    return errors
