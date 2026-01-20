"""Tests for report generator."""

import json
from datetime import datetime

import pytest

from src.models import (
    AnimeEntry,
    AnimeRating,
    AnimeType,
    ConflictResolution,
    MappedIds,
    TraktEntry,
    WatchedEpisode,
)
from src.report import (
    generate_csv_report,
    generate_html_report,
    generate_unmapped_json,
)


@pytest.fixture
def sample_anime_list():
    """Create sample anime list for testing."""
    return [
        AnimeEntry(
            anidb_id=11061,
            title="Shingeki no Kyojin",
            title_english="Attack on Titan",
            anime_type=AnimeType.TV,
            total_episodes=25,
            watched_episodes=[
                WatchedEpisode(episode_number=1, watched_at=datetime(2023, 4, 1)),
                WatchedEpisode(episode_number=2, watched_at=datetime(2023, 4, 2)),
            ],
            rating=AnimeRating(score=9, rated_at=datetime(2023, 4, 15)),
            mapped_ids=MappedIds(tvdb_id=267440, imdb_id="tt2560140"),
        ),
        AnimeEntry(
            anidb_id=10083,
            title="Kimi no Na wa.",
            title_english="Your Name.",
            anime_type=AnimeType.MOVIE,
            total_episodes=1,
            watched_episodes=[
                WatchedEpisode(episode_number=1, watched_at=datetime(2022, 5, 20)),
            ],
            rating=AnimeRating(score=10, rated_at=datetime(2022, 5, 20)),
            mapped_ids=MappedIds(tmdb_movie_id=372058, imdb_id="tt5311514"),
        ),
        AnimeEntry(
            anidb_id=99999,
            title="Unknown Anime",
            title_english=None,
            anime_type=AnimeType.TV,
            total_episodes=12,
            watched_episodes=[
                WatchedEpisode(episode_number=1),
            ],
            rating=AnimeRating(score=7),
            # No mapped_ids - unmapped
        ),
    ]


@pytest.fixture
def sample_resolutions(sample_anime_list):
    """Create sample conflict resolutions."""
    return [
        ConflictResolution(
            anime=sample_anime_list[0],
            trakt_entry=TraktEntry(
                trakt_id=1,
                title="Attack on Titan",
                ids={"tvdb": 267440},
                rating=8,
                rated_at=datetime(2024, 1, 1),  # Later than AniDB
            ),
            keep_anidb_rating=True,  # AniDB is older
            rating_conflict=True,
        ),
        ConflictResolution(
            anime=sample_anime_list[1],
            # No Trakt entry - new
        ),
        ConflictResolution(
            anime=sample_anime_list[2],
            # No Trakt entry and unmapped
        ),
    ]


class TestGenerateHtmlReport:
    """Tests for HTML report generation."""

    def test_generate_html_report(self, sample_anime_list, sample_resolutions):
        """Generate HTML report."""
        html = generate_html_report(sample_anime_list, sample_resolutions)

        assert "<!DOCTYPE html>" in html
        assert "AniDB to Trakt Report" in html

        # Check anime titles are present
        assert "Attack on Titan" in html
        assert "Your Name." in html
        assert "Unknown Anime" in html

        # Check stats
        assert ">3<" in html  # Total count

    def test_generate_html_report_to_file(self, sample_anime_list, tmp_path):
        """Generate HTML report to file."""
        output_path = tmp_path / "report.html"
        generate_html_report(sample_anime_list, output_path=output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "Attack on Titan" in content

    def test_html_report_contains_links(self, sample_anime_list):
        """HTML report contains database links."""
        html = generate_html_report(sample_anime_list)

        # AniDB links
        assert "anidb.net/anime/11061" in html
        assert "anidb.net/anime/10083" in html

        # TVDB link
        assert "thetvdb.com" in html

        # IMDB link
        assert "imdb.com/title/tt2560140" in html

    def test_html_report_status_indicators(self, sample_anime_list, sample_resolutions):
        """HTML report shows correct status indicators."""
        html = generate_html_report(sample_anime_list, sample_resolutions)

        # Conflict indicator
        assert "Keep AniDB" in html

        # New indicator
        assert "New" in html

        # Unmapped indicator
        assert "Unmapped" in html

    def test_html_report_sortable_data(self, sample_anime_list):
        """HTML report includes sortable data attributes."""
        html = generate_html_report(sample_anime_list)

        # Rating data-sort attributes
        assert 'data-sort="9"' in html
        assert 'data-sort="10"' in html

    def test_html_report_empty_list(self):
        """Generate HTML report for empty list."""
        html = generate_html_report([])

        assert "<!DOCTYPE html>" in html
        assert ">0<" in html  # Total count is 0


class TestGenerateCsvReport:
    """Tests for CSV report generation."""

    def test_generate_csv_report(self, sample_anime_list, sample_resolutions):
        """Generate CSV report."""
        rows = generate_csv_report(sample_anime_list, sample_resolutions)

        assert len(rows) == 3

        # Find Attack on Titan row
        aot_row = next(r for r in rows if r["AniDB ID"] == 11061)
        assert aot_row["Title"] == "Attack on Titan"
        assert aot_row["AniDB Rating"] == 9
        assert aot_row["TVDB ID"] == 267440
        assert aot_row["Conflict"] == "Keep AniDB"

    def test_generate_csv_report_to_file(self, sample_anime_list, tmp_path):
        """Generate CSV report to file."""
        output_path = tmp_path / "report.csv"
        generate_csv_report(sample_anime_list, output_path=output_path)

        assert output_path.exists()

        # Read and verify content
        import csv

        with open(output_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 3
        assert rows[0]["Title"]  # Has title column

    def test_csv_report_movie_ids(self, sample_anime_list):
        """CSV report includes TMDB ID for movies."""
        rows = generate_csv_report(sample_anime_list)

        movie_row = next(r for r in rows if r["AniDB ID"] == 10083)
        assert movie_row["TMDB ID"] == 372058

    def test_csv_report_unmapped(self, sample_anime_list):
        """CSV report handles unmapped anime."""
        rows = generate_csv_report(sample_anime_list)

        unmapped_row = next(r for r in rows if r["AniDB ID"] == 99999)
        assert unmapped_row["TVDB ID"] == ""
        assert unmapped_row["IMDB ID"] == ""
        assert "Unmapped" in unmapped_row["Status"]

    def test_csv_report_sorted_by_title(self, sample_anime_list):
        """CSV report is sorted by title."""
        rows = generate_csv_report(sample_anime_list)

        titles = [r["Title"] for r in rows]
        assert titles == sorted(titles, key=str.lower)


class TestGenerateUnmappedJson:
    """Tests for unmapped anime JSON generation."""

    def test_generate_unmapped_json(self, sample_anime_list):
        """Generate unmapped JSON report."""
        unmapped = [a for a in sample_anime_list if not a.is_mapped]
        data = generate_unmapped_json(unmapped)

        assert len(data) == 1
        assert data[0]["anidb_id"] == 99999
        assert data[0]["title"] == "Unknown Anime"
        assert data[0]["rating"] == 7
        assert "anidb_url" in data[0]

    def test_generate_unmapped_json_to_file(self, sample_anime_list, tmp_path):
        """Generate unmapped JSON to file."""
        unmapped = [a for a in sample_anime_list if not a.is_mapped]
        output_path = tmp_path / "unmapped.json"

        generate_unmapped_json(unmapped, output_path=output_path)

        assert output_path.exists()
        with open(output_path) as f:
            data = json.load(f)
        assert len(data) == 1

    def test_unmapped_json_includes_all_fields(self, sample_anime_list):
        """Unmapped JSON includes all expected fields."""
        unmapped = [a for a in sample_anime_list if not a.is_mapped]
        data = generate_unmapped_json(unmapped)

        expected_fields = [
            "anidb_id",
            "title",
            "title_romaji",
            "type",
            "total_episodes",
            "watched_episodes",
            "rating",
            "anidb_url",
        ]

        for field in expected_fields:
            assert field in data[0]

    def test_unmapped_json_empty_list(self):
        """Generate unmapped JSON for empty list."""
        data = generate_unmapped_json([])
        assert data == []

    def test_unmapped_json_no_rating(self):
        """Unmapped anime without rating shows None."""
        anime = AnimeEntry(
            anidb_id=12345,
            title="No Rating Anime",
            anime_type=AnimeType.TV,
        )
        data = generate_unmapped_json([anime])

        assert data[0]["rating"] is None
