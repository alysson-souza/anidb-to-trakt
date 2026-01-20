"""Tests for Trakt exporter."""

import json
from datetime import datetime

import pytest

from src.id_mapper import IDMapper
from src.models import (
    AnimeEntry,
    AnimeRating,
    AnimeType,
    EpisodeType,
    SyncCheckpoint,
    WatchedEpisode,
)
from src.trakt_exporter import (
    TraktExporter,
    datetime_to_iso,
    iso_to_datetime,
    load_checkpoint,
    save_checkpoint,
)


@pytest.fixture
def mock_id_mapper(tmp_path):
    """Create mock ID mapper with test data."""
    cache_path = tmp_path / "anime_ids.json"
    mapping_data = {
        "11061": {
            "tvdb_id": 267440,
            "imdb_id": "tt2560140",
            "tvdb_season": 1,
            "tvdb_epoffset": 0,
        },
        "10083": {
            "tmdb_movie_id": 372058,
            "imdb_id": "tt5311514",
        },
        "4563": {
            "tvdb_id": 79481,
            "tvdb_season": 1,
            "tvdb_epoffset": 0,
        },
    }
    with open(cache_path, "w") as f:
        json.dump(mapping_data, f)

    mapper = IDMapper(cache_path=cache_path, auto_download=False)
    return mapper


@pytest.fixture
def sample_anime_list(mock_id_mapper):
    """Create sample anime list with mappings."""
    anime_list = [
        AnimeEntry(
            anidb_id=11061,
            title="Shingeki no Kyojin",
            title_english="Attack on Titan",
            anime_type=AnimeType.TV,
            total_episodes=25,
            watched_episodes=[
                WatchedEpisode(
                    episode_number=1,
                    episode_type=EpisodeType.REGULAR,
                    watched_at=datetime(2023, 4, 1, 18, 0),
                ),
                WatchedEpisode(
                    episode_number=2,
                    episode_type=EpisodeType.REGULAR,
                    watched_at=datetime(2023, 4, 2, 18, 0),
                ),
                WatchedEpisode(
                    episode_number=1,
                    episode_type=EpisodeType.SPECIAL,
                    watched_at=datetime(2023, 4, 16, 14, 0),
                ),
            ],
            rating=AnimeRating(
                score=9,
                rated_at=datetime(2023, 4, 15, 20, 30),
            ),
        ),
        AnimeEntry(
            anidb_id=10083,
            title="Kimi no Na wa.",
            title_english="Your Name.",
            anime_type=AnimeType.MOVIE,
            total_episodes=1,
            watched_episodes=[
                WatchedEpisode(
                    episode_number=1,
                    episode_type=EpisodeType.REGULAR,
                    watched_at=datetime(2022, 5, 20, 10, 0),
                ),
            ],
            rating=AnimeRating(
                score=10,
                rated_at=datetime(2022, 5, 20, 12, 0),
            ),
        ),
    ]

    # Map IDs
    mock_id_mapper.map_all(anime_list)
    return anime_list


class TestDatetimeConversion:
    """Tests for datetime conversion functions."""

    def test_datetime_to_iso(self):
        """Convert datetime to ISO format."""
        dt = datetime(2023, 4, 15, 20, 30, 0)
        result = datetime_to_iso(dt)
        assert result == "2023-04-15T20:30:00.000Z"

    def test_datetime_to_iso_none(self):
        """None datetime returns None."""
        assert datetime_to_iso(None) is None

    def test_iso_to_datetime(self):
        """Parse ISO datetime string."""
        result = iso_to_datetime("2023-04-15T20:30:00.000Z")
        assert result == datetime(2023, 4, 15, 20, 30, 0)

    def test_iso_to_datetime_none(self):
        """None string returns None."""
        assert iso_to_datetime(None) is None

    def test_iso_to_datetime_empty(self):
        """Empty string returns None."""
        assert iso_to_datetime("") is None


class TestTraktExporter:
    """Tests for TraktExporter class."""

    def test_generate_history_json_shows(self, mock_id_mapper, sample_anime_list):
        """Generate history JSON for TV shows."""
        exporter = TraktExporter(mock_id_mapper)
        history = exporter.generate_history_json(sample_anime_list)

        assert "shows" in history
        assert len(history["shows"]) == 1

        show = history["shows"][0]
        assert show["ids"]["tvdb"] == 267440

        # Check seasons
        assert len(show["seasons"]) == 2  # Season 1 and Season 0 (specials)

        # Find regular season
        season_1 = next(s for s in show["seasons"] if s["number"] == 1)
        assert len(season_1["episodes"]) == 2

        # Find specials season
        season_0 = next(s for s in show["seasons"] if s["number"] == 0)
        assert len(season_0["episodes"]) == 1

    def test_generate_history_json_movies(self, mock_id_mapper, sample_anime_list):
        """Generate history JSON for movies."""
        exporter = TraktExporter(mock_id_mapper)
        history = exporter.generate_history_json(sample_anime_list)

        assert "movies" in history
        assert len(history["movies"]) == 1

        movie = history["movies"][0]
        assert movie["ids"]["tmdb"] == 372058
        assert "watched_at" in movie

    def test_generate_ratings_json(self, mock_id_mapper, sample_anime_list):
        """Generate ratings JSON."""
        exporter = TraktExporter(mock_id_mapper)
        ratings = exporter.generate_ratings_json(sample_anime_list)

        assert "shows" in ratings
        assert "movies" in ratings

        # Check show rating
        show_rating = ratings["shows"][0]
        assert show_rating["ids"]["tvdb"] == 267440
        assert show_rating["rating"] == 9
        assert "rated_at" in show_rating

        # Check movie rating
        movie_rating = ratings["movies"][0]
        assert movie_rating["ids"]["tmdb"] == 372058
        assert movie_rating["rating"] == 10

    def test_generate_ratings_json_respects_resolutions(self, mock_id_mapper, sample_anime_list):
        """Ratings JSON respects conflict resolutions."""
        from src.models import ConflictResolution, TraktEntry

        exporter = TraktExporter(mock_id_mapper)

        # Create resolution that says keep Trakt rating
        resolution = ConflictResolution(
            anime=sample_anime_list[0],
            trakt_entry=TraktEntry(
                trakt_id=12345,
                title="Attack on Titan",
                ids={"tvdb": 267440},
                rating=8,
                rated_at=datetime(2023, 1, 1),  # Earlier than AniDB
            ),
            keep_anidb_rating=False,
            rating_conflict=True,
        )

        ratings = exporter.generate_ratings_json(
            sample_anime_list,
            resolutions=[resolution],
        )

        # Should exclude the show because we're keeping Trakt rating
        show_ids = [s["ids"]["tvdb"] for s in ratings.get("shows", [])]
        assert 267440 not in show_ids

    def test_export_to_files(self, mock_id_mapper, sample_anime_list, tmp_path):
        """Export data to files."""
        exporter = TraktExporter(mock_id_mapper)
        output_dir = tmp_path / "output"

        files = exporter.export_to_files(sample_anime_list, output_dir)

        assert "history" in files
        assert "ratings" in files
        assert files["history"].exists()
        assert files["ratings"].exists()

        # Verify JSON content
        with open(files["history"]) as f:
            history = json.load(f)
            assert "shows" in history or "movies" in history

        with open(files["ratings"]) as f:
            ratings = json.load(f)
            assert "shows" in ratings or "movies" in ratings

    def test_generate_history_empty_list(self, mock_id_mapper):
        """Generate history for empty list."""
        exporter = TraktExporter(mock_id_mapper)
        history = exporter.generate_history_json([])

        assert history == {}

    def test_generate_ratings_no_ratings(self, mock_id_mapper):
        """Generate ratings for anime without ratings."""
        anime_list = [
            AnimeEntry(
                anidb_id=11061,
                title="Test",
                watched_episodes=[WatchedEpisode(episode_number=1)],
            ),
        ]
        mock_id_mapper.map_all(anime_list)

        exporter = TraktExporter(mock_id_mapper)
        ratings = exporter.generate_ratings_json(anime_list)

        assert ratings == {}


class TestCheckpoint:
    """Tests for checkpoint save/load."""

    def test_save_and_load_checkpoint(self, tmp_path):
        """Save and load checkpoint."""
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint = SyncCheckpoint(
            last_processed_index=10,
            synced_ratings=[1, 2, 3],
            synced_history=[4, 5],
            errors=[{"error": "test"}],
            timestamp=datetime(2023, 4, 15, 12, 0),
        )

        save_checkpoint(checkpoint, checkpoint_path)
        loaded = load_checkpoint(checkpoint_path)

        assert loaded is not None
        assert loaded.last_processed_index == 10
        assert loaded.synced_ratings == [1, 2, 3]
        assert loaded.synced_history == [4, 5]
        assert loaded.errors == [{"error": "test"}]

    def test_load_nonexistent_checkpoint(self, tmp_path):
        """Load returns None for nonexistent file."""
        checkpoint_path = tmp_path / "nonexistent.json"
        assert load_checkpoint(checkpoint_path) is None

    def test_load_invalid_checkpoint(self, tmp_path):
        """Load returns None for invalid JSON."""
        checkpoint_path = tmp_path / "invalid.json"
        with open(checkpoint_path, "w") as f:
            f.write("not valid json")

        assert load_checkpoint(checkpoint_path) is None


class TestConflictResolution:
    """Tests for conflict resolution logic."""

    def test_resolve_conflicts_new_anime(self, mock_id_mapper, sample_anime_list):
        """New anime gets flagged as new."""
        exporter = TraktExporter(mock_id_mapper)
        # Don't fetch existing data - everything is new
        resolutions = exporter.resolve_conflicts(sample_anime_list, fetch_existing=False)

        assert len(resolutions) == 2
        assert all(r.is_new for r in resolutions)

    def test_conflict_indicator_new(self, mock_id_mapper):
        """Conflict indicator for new anime."""
        from src.models import ConflictResolution

        anime = AnimeEntry(
            anidb_id=11061,
            title="Test",
            rating=AnimeRating(score=9),
        )
        mock_id_mapper.map_anime(anime)

        resolution = ConflictResolution(anime=anime)
        assert resolution.is_new
        assert resolution.conflict_indicator == "➕ New"

    def test_conflict_indicator_keep_anidb(self, mock_id_mapper):
        """Conflict indicator when keeping AniDB rating."""
        from src.models import ConflictResolution, TraktEntry

        anime = AnimeEntry(
            anidb_id=11061,
            title="Test",
            rating=AnimeRating(score=9),
        )
        mock_id_mapper.map_anime(anime)

        resolution = ConflictResolution(
            anime=anime,
            trakt_entry=TraktEntry(
                trakt_id=1,
                title="Test",
                ids={},
                rating=8,
            ),
            keep_anidb_rating=True,
            rating_conflict=True,
        )

        assert resolution.conflict_indicator == "✅ Keep AniDB"

    def test_conflict_indicator_keep_trakt(self, mock_id_mapper):
        """Conflict indicator when keeping Trakt rating."""
        from src.models import ConflictResolution, TraktEntry

        anime = AnimeEntry(
            anidb_id=11061,
            title="Test",
            rating=AnimeRating(score=9),
        )
        mock_id_mapper.map_anime(anime)

        resolution = ConflictResolution(
            anime=anime,
            trakt_entry=TraktEntry(
                trakt_id=1,
                title="Test",
                ids={},
                rating=8,
            ),
            keep_anidb_rating=False,
            rating_conflict=True,
        )

        assert resolution.conflict_indicator == "⏭️ Keep Trakt"
