"""Render UI screenshots for the README.

Renders index.html, _results.html, and _applied.html through the project's
real Jinja2 templates with realistic mock data, then converts each to a
PNG with wkhtmltoimage. The partials are wrapped in base.html so the
captures show what the user actually sees on the page.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"
OUT = ROOT / "docs" / "screenshots"


def _url_for(name: str, **params: str) -> str:
    if name == "static":
        path = params.get("path", "").lstrip("/")
        return f"file://{(STATIC / path).resolve()}"
    return "#"


env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
env.globals["url_for"] = _url_for


@dataclass(frozen=True)
class Candidate:
    season: int
    episode: int
    title: str
    confidence: float


@dataclass(frozen=True)
class Match:
    source: str
    destination: str
    confidence: float
    needs_review: bool
    season: int
    episode: int
    episode_title: str
    alternates: tuple[Candidate, ...]


LIB = "/media/tv/Star Trek The Next Generation (1987) [tvdbid-71470]/Season 03"

DEMO_MATCHES = [
    Match(
        source="/rips/TNG-S03/title_t00.mkv",
        destination=f"{LIB}/Star Trek The Next Generation - S03E01 - Evolution.mkv",
        confidence=94.5,
        needs_review=False,
        season=3,
        episode=1,
        episode_title="Evolution",
        alternates=(
            Candidate(3, 2, "The Ensigns of Command", 42.3),
            Candidate(3, 17, "Sins of the Father", 38.1),
        ),
    ),
    Match(
        source="/rips/TNG-S03/title_t01.mkv",
        destination=f"{LIB}/Star Trek The Next Generation - S03E15 - Yesterday's Enterprise.mkv",
        confidence=87.1,
        needs_review=False,
        season=3,
        episode=15,
        episode_title="Yesterday's Enterprise",
        alternates=(
            Candidate(3, 10, "The Defector", 51.8),
            Candidate(3, 26, "The Best of Both Worlds (1)", 47.2),
        ),
    ),
    Match(
        source="/rips/TNG-S03/title_t02.mkv",
        destination=f"{LIB}/Star Trek The Next Generation - S03E10 - The Defector.mkv",
        confidence=72.4,
        needs_review=True,
        season=3,
        episode=10,
        episode_title="The Defector",
        alternates=(Candidate(3, 17, "Sins of the Father", 68.9),),
    ),
    Match(
        source="/rips/TNG-S03/title_t03.mkv",
        destination=f"{LIB}/Star Trek The Next Generation - S03E26 - The Best of Both Worlds (1).mkv",
        confidence=58.2,
        needs_review=True,
        season=3,
        episode=26,
        episode_title="The Best of Both Worlds (1)",
        alternates=(Candidate(3, 25, "Transfigurations", 55.1),),
    ),
]


@dataclass(frozen=True)
class JobProgress:
    files_total: int
    files_done: int
    stage: str


@dataclass(frozen=True)
class Job:
    id: str
    progress: JobProgress


DEMO_JOB = Job(id="demo-screenshot-job", progress=JobProgress(4, 4, "done"))


def _render_in_shell(partial: str, context: dict) -> str:
    """Render `partial` inside base.html so the screenshot shows the page chrome."""
    template = env.from_string(
        '{% extends "base.html" %}{% block content %}{% include "' + partial + '" %}{% endblock %}'
    )
    return template.render(**context)


def _render_index() -> str:
    return env.get_template("index.html").render(
        series_keys=sorted(["tos", "tas", "tng", "ds9", "voy", "ent", "dis"]),
        providers=("chakoteya", "opensubtitles"),
        defaults={
            "media_root": "/media/input/rips/TNG-S03",
            "library_root": "/media/tv",
            "include_provider_id": True,
        },
    )


def _render_results() -> str:
    return _render_in_shell("_results.html", {"job": DEMO_JOB, "matches": DEMO_MATCHES})


def _render_applied() -> str:
    return _render_in_shell(
        "_applied.html",
        {"applied": True, "errors": [], "total": len(DEMO_MATCHES)},
    )


def _to_png(html: str, png: Path) -> None:
    src = png.with_suffix(".html")
    src.write_text(html)
    subprocess.run(
        [
            "wkhtmltoimage",
            "--enable-local-file-access",
            "--width",
            "1000",
            "--javascript-delay",
            "0",
            "--disable-javascript",
            "--quality",
            "92",
            "--quiet",
            str(src),
            str(png),
        ],
        check=True,
    )
    src.unlink()
    print(f"  wrote {png.relative_to(ROOT)}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, html in (
        ("index.jpg", _render_index()),
        ("results.jpg", _render_results()),
        ("applied.jpg", _render_applied()),
    ):
        _to_png(html, OUT / name)


if __name__ == "__main__":
    main()
