"""Tests for ID mapper."""

import json
from unittest.mock import Mock, patch

import pytest

from src.id_mapper import IDMapper, IDMapperError, create_unmapped_report
from src.models import AnimeEntry, AnimeType, EpisodeType, MappedIds, WatchedEpisode


@pytest.fixture
def mock_mapping_data():
    """Sample mapping data."""
    return {
        "11061": {
            "tvdb_id": 267440,
            "imdb_id": "tt2560140",
            "tmdb_show_id": 1429,
            "tvdb_season": 1,
            "tvdb_epoffset": 0,
        },
        "10083": {
            "tmdb_movie_id": 372058,
            "imdb_id": "tt5311514",
        },
        "4563": {
            "tvdb_id": 79481,
            "imdb_id": "tt0877057",
            "tmdb_show_id": 13916,
            "tvdb_season": 1,
            "tvdb_epoffset": 0,
        },
        "12345": {
            "tvdb_id": 99999,
            "tvdb_season": 2,
            "tvdb_epoffset": 12,
        },
    }


@pytest.fixture
def mapper_with_cache(tmp_path, mock_mapping_data):
    """Create mapper with pre-populated cache."""
    cache_path = tmp_path / "anime_ids.json"
    with open(cache_path, "w") as f:
        json.dump(mock_mapping_data, f)

    return IDMapper(cache_path=cache_path, auto_download=False)


@pytest.fixture
def sample_anime():
    """Sample anime entry."""
    return AnimeEntry(
        anidb_id=11061,
        title="Shingeki no Kyojin",
        title_english="Attack on Titan",
        anime_type=AnimeType.TV,
        total_episodes=25,
        watched_episodes=[
            WatchedEpisode(episode_number=1, episode_type=EpisodeType.REGULAR),
            WatchedEpisode(episode_number=2, episode_type=EpisodeType.REGULAR),
            WatchedEpisode(episode_number=1, episode_type=EpisodeType.SPECIAL),
        ],
    )


class TestMappedIds:
    """Tests for MappedIds model."""

    def test_has_any_id_with_tvdb(self):
        """Has any ID when TVDB is present."""
        ids = MappedIds(tvdb_id=12345)
        assert ids.has_any_id

    def test_has_any_id_with_imdb(self):
        """Has any ID when IMDB is present."""
        ids = MappedIds(imdb_id="tt1234567")
        assert ids.has_any_id

    def test_has_any_id_empty(self):
        """Has no ID when empty."""
        ids = MappedIds()
        assert not ids.has_any_id

    def test_is_movie_with_tmdb_movie(self):
        """Is movie when tmdb_movie_id is set."""
        ids = MappedIds(tmdb_movie_id=12345)
        assert ids.is_movie

    def test_is_movie_with_tvdb(self):
        """Not a movie when tvdb_id is set."""
        ids = MappedIds(tvdb_id=12345, tmdb_movie_id=67890)
        assert not ids.is_movie

    def test_get_trakt_ids_prefers_tvdb(self):
        """Trakt IDs prefer TVDB."""
        ids = MappedIds(tvdb_id=12345, imdb_id="tt1234567", tmdb_show_id=99999)
        trakt_ids = ids.get_trakt_ids()

        assert trakt_ids["tvdb"] == 12345
        assert trakt_ids["imdb"] == "tt1234567"
        assert "tmdb" not in trakt_ids  # Not included when TVDB present

    def test_get_trakt_ids_falls_back_to_tmdb(self):
        """Trakt IDs fall back to TMDB when no TVDB."""
        ids = MappedIds(tmdb_movie_id=12345)
        trakt_ids = ids.get_trakt_ids()

        assert "tvdb" not in trakt_ids
        assert trakt_ids["tmdb"] == 12345


class TestIDMapper:
    """Tests for IDMapper class."""

    def test_get_mapping_found(self, mapper_with_cache):
        """Get mapping for existing anime."""
        mapping = mapper_with_cache.get_mapping(11061)

        assert mapping is not None
        assert mapping.tvdb_id == 267440
        assert mapping.imdb_id == "tt2560140"
        assert mapping.tmdb_show_id == 1429

    def test_get_mapping_not_found(self, mapper_with_cache):
        """Return None for missing anime."""
        mapping = mapper_with_cache.get_mapping(99999)
        assert mapping is None

    def test_get_mapping_movie(self, mapper_with_cache):
        """Get mapping for movie."""
        mapping = mapper_with_cache.get_mapping(10083)

        assert mapping is not None
        assert mapping.tmdb_movie_id == 372058
        assert mapping.is_movie

    def test_map_anime(self, mapper_with_cache, sample_anime):
        """Map anime entry."""
        result = mapper_with_cache.map_anime(sample_anime)

        assert result is sample_anime  # Same object
        assert result.mapped_ids is not None
        assert result.mapped_ids.tvdb_id == 267440
        assert result.is_mapped

    def test_map_all(self, mapper_with_cache):
        """Map multiple anime entries."""
        anime_list = [
            AnimeEntry(anidb_id=11061, title="Attack on Titan"),
            AnimeEntry(anidb_id=10083, title="Your Name"),
            AnimeEntry(anidb_id=99999, title="Unknown Anime"),
        ]

        mapped, unmapped = mapper_with_cache.map_all(anime_list)

        assert len(mapped) == 2
        assert len(unmapped) == 1
        assert unmapped[0].anidb_id == 99999

    def test_map_episode_to_trakt_regular(self, mapper_with_cache):
        """Map regular episode to Trakt."""
        ids = MappedIds(tvdb_id=12345, tvdb_season=1, tvdb_epoffset=0)
        episode = WatchedEpisode(episode_number=5, episode_type=EpisodeType.REGULAR)

        season, ep = mapper_with_cache.map_episode_to_trakt(episode, ids)

        assert season == 1
        assert ep == 5

    def test_map_episode_to_trakt_with_offset(self, mapper_with_cache):
        """Map episode with offset to Trakt."""
        mapping = mapper_with_cache.get_mapping(12345)  # Has offset 12
        episode = WatchedEpisode(episode_number=1, episode_type=EpisodeType.REGULAR)

        season, ep = mapper_with_cache.map_episode_to_trakt(episode, mapping)

        assert season == 2
        assert ep == 13  # 1 + 12

    def test_map_episode_to_trakt_special(self, mapper_with_cache):
        """Map special episode to Season 0."""
        ids = MappedIds(tvdb_id=12345, tvdb_season=1, tvdb_epoffset=0)
        episode = WatchedEpisode(episode_number=3, episode_type=EpisodeType.SPECIAL)

        season, ep = mapper_with_cache.map_episode_to_trakt(episode, ids)

        assert season == 0  # Specials always Season 0
        assert ep == 3

    def test_cache_loading(self, tmp_path, mock_mapping_data):
        """Cache is loaded on init."""
        cache_path = tmp_path / "anime_ids.json"
        with open(cache_path, "w") as f:
            json.dump(mock_mapping_data, f)

        mapper = IDMapper(cache_path=cache_path, auto_download=False)

        # Should be loaded without network call
        assert mapper.get_mapping(11061) is not None

    def test_no_cache_raises_error(self, tmp_path):
        """Error when no cache and auto_download disabled."""
        cache_path = tmp_path / "nonexistent.json"
        mapper = IDMapper(cache_path=cache_path, auto_download=False)

        with pytest.raises(IDMapperError, match="not loaded"):
            mapper.get_mapping(11061)

    @patch("httpx.Client")
    def test_download_database(self, mock_client_class, tmp_path, mock_mapping_data):
        """Download database from network."""
        cache_path = tmp_path / "anime_ids.json"

        # Mock HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_mapping_data
        mock_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=None)
        mock_client_class.return_value = mock_client

        mapper = IDMapper(cache_path=cache_path, auto_download=False)
        mapper.download_database()

        # Should have downloaded and cached
        assert cache_path.exists()
        assert mapper.get_mapping(11061) is not None

    def test_get_stats(self, mapper_with_cache):
        """Get database statistics."""
        stats = mapper_with_cache.get_stats()

        assert stats["total_entries"] == 4
        assert stats["with_tvdb"] == 3
        assert stats["with_imdb"] == 3
        assert stats["with_tmdb_movie"] == 1


class TestCreateUnmappedReport:
    """Tests for create_unmapped_report function."""

    def test_create_report(self):
        """Create report for unmapped anime."""
        unmapped = [
            AnimeEntry(
                anidb_id=99999,
                title="Unknown Anime",
                title_english="Unknown Anime EN",
                anime_type=AnimeType.TV,
                total_episodes=12,
                watched_episodes=[
                    WatchedEpisode(episode_number=1),
                    WatchedEpisode(episode_number=2),
                ],
            ),
        ]

        report = create_unmapped_report(unmapped)

        assert len(report) == 1
        assert report[0]["anidb_id"] == 99999
        assert report[0]["title"] == "Unknown Anime"
        assert report[0]["watched"] == 2
        assert "anidb_url" in report[0]
