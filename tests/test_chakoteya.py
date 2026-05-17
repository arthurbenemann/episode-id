"""Tests for the chakoteya provider's HTML parser.

We test against a captured fixture so the test is offline-safe and doesn't
depend on chakoteya.net being up or unblocking our IP.
"""

from __future__ import annotations

from pathlib import Path

from app.providers.chakoteya import ChakoteyaProvider

FIXTURE = Path(__file__).parent / "fixtures" / "chakoteya_index.html"


def test_parse_index_extracts_all_seasons() -> None:
    html = FIXTURE.read_text()
    provider = ChakoteyaProvider()
    entries = list(provider._parse_index(html, "NextGen"))

    assert len(entries) == 7

    by_season: dict[int, list[str]] = {}
    for e in entries:
        by_season.setdefault(e.season, []).append(e.title)

    assert by_season[1] == [
        "Encounter at Farpoint",
        "The Naked Now",
        "Code of Honour",
    ]
    assert by_season[2] == ["The Child", "Where Silence Has Lease"]
    # Season 3 broadcast order: Evolution aired before Ensigns of Command,
    # even though production code 149 < 150. Chakoteya lists in broadcast
    # order, which is what we want.
    assert by_season[3] == ["Evolution", "The Ensigns of Command"]


def test_parse_index_numbers_episodes_in_table_order() -> None:
    """Episode numbers within a season should follow the row order in the
    table (= broadcast order)."""
    html = FIXTURE.read_text()
    provider = ChakoteyaProvider()
    entries = list(provider._parse_index(html, "NextGen"))

    s3 = sorted([e for e in entries if e.season == 3], key=lambda e: e.episode)
    assert s3[0].episode == 1
    assert s3[0].title == "Evolution"
    assert s3[1].episode == 2
    assert s3[1].title == "The Ensigns of Command"


def test_parse_index_builds_absolute_urls() -> None:
    html = FIXTURE.read_text()
    provider = ChakoteyaProvider()
    entries = list(provider._parse_index(html, "NextGen"))

    farpoint = next(e for e in entries if e.title == "Encounter at Farpoint")
    assert farpoint.transcript_url == "http://www.chakoteya.net/NextGen/101.htm"


def test_extract_dialogue_strips_html() -> None:
    html = """
    <html><body>
    <p><b>PICARD:</b> Make it so.</p>
    <p><b>RIKER:</b> Aye, Captain.</p>
    </body></html>
    """
    text = ChakoteyaProvider._extract_dialogue(html)
    assert "Make it so" in text
    assert "Aye, Captain" in text
    assert "<" not in text
    assert ">" not in text
