"""AniDB to Trakt ID mapper using Kometa-Team/Anime-IDs database."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from .models import AnimeEntry, MappedIds, WatchedEpisode

logger = logging.getLogger(__name__)

# Kometa-Team/Anime-IDs database URL
ANIME_IDS_URL = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json"

# Default cache location
DEFAULT_CACHE_DIR = Path(__file__).parent.parent / "data"
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "anime_ids.json"

# Cache expiry (7 days)
CACHE_EXPIRY_DAYS = 7


class IDMapperError(Exception):
    """Error in ID mapping operations."""

    pass


class IDMapper:
    """Map AniDB IDs to Trakt-compatible IDs (TVDB, IMDB, TMDB)."""

    def __init__(
        self,
        cache_path: Path | None = None,
        auto_download: bool = True,
    ):
        """Initialize the ID mapper.

        Args:
            cache_path: Path to cache file. Uses default if None.
            auto_download: Download database if not cached.
        """
        self.cache_path = cache_path or DEFAULT_CACHE_FILE
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._mappings: dict = {}
        self._loaded = False
        self._auto_download = auto_download

    def _load_cache(self) -> bool:
        """Load mappings from cache file.

        Returns:
            True if cache was loaded successfully.
        """
        if not self.cache_path.exists():
            return False

        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)

            # Check if cache has metadata
            if isinstance(data, dict) and "_meta" in data:
                meta = data["_meta"]
                cached_at = datetime.fromisoformat(meta.get("cached_at", ""))
                if datetime.now() - cached_at > timedelta(days=CACHE_EXPIRY_DAYS):
                    logger.info("Cache expired, will refresh")
                    return False
                self._mappings = {k: v for k, v in data.items() if k != "_meta"}
            else:
                # Raw data without metadata
                self._mappings = data

            self._loaded = True
            logger.info(f"Loaded {len(self._mappings)} mappings from cache")
            return True

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to load cache: {e}")
            return False

    def _save_cache(self) -> None:
        """Save mappings to cache file with metadata."""
        data = dict(self._mappings)
        data["_meta"] = {
            "cached_at": datetime.now().isoformat(),
            "source": ANIME_IDS_URL,
        }

        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved {len(self._mappings)} mappings to cache")

    def download_database(self, force: bool = False) -> None:
        """Download the Anime-IDs database.

        Args:
            force: Force download even if cache exists.

        Raises:
            IDMapperError: If download fails.
        """
        if not force and self._load_cache():
            return

        logger.info(f"Downloading Anime-IDs database from {ANIME_IDS_URL}")

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(ANIME_IDS_URL)
                response.raise_for_status()
                data = response.json()

        except httpx.HTTPError as e:
            raise IDMapperError(f"Failed to download database: {e}") from e
        except json.JSONDecodeError as e:
            raise IDMapperError(f"Invalid JSON in database: {e}") from e

        self._mappings = data
        self._loaded = True
        self._save_cache()

    def _ensure_loaded(self) -> None:
        """Ensure mappings are loaded."""
        if self._loaded:
            return

        if self._load_cache():
            return

        if self._auto_download:
            self.download_database()
        else:
            raise IDMapperError("Mappings not loaded and auto_download is disabled")

    def get_mapping(self, anidb_id: int) -> MappedIds | None:
        """Get Trakt-compatible IDs for an AniDB ID.

        Args:
            anidb_id: AniDB anime ID.

        Returns:
            MappedIds object or None if not found.
        """
        self._ensure_loaded()

        # Database keys can be strings
        str_id = str(anidb_id)
        if str_id not in self._mappings:
            return None

        entry = self._mappings[str_id]

        return MappedIds(
            tvdb_id=entry.get("tvdb_id"),
            imdb_id=entry.get("imdb_id"),
            tmdb_show_id=entry.get("tmdb_show_id"),
            tmdb_movie_id=entry.get("tmdb_movie_id"),
            tvdb_season=entry.get("tvdb_season", 1),
            tvdb_epoffset=entry.get("tvdb_epoffset", 0),
        )

    def map_anime(self, anime: AnimeEntry) -> AnimeEntry:
        """Add ID mappings to an anime entry.

        Args:
            anime: AnimeEntry to map.

        Returns:
            The same AnimeEntry with mapped_ids populated.
        """
        anime.mapped_ids = self.get_mapping(anime.anidb_id)
        return anime

    def map_all(self, anime_list: list[AnimeEntry]) -> tuple[list[AnimeEntry], list[AnimeEntry]]:
        """Map IDs for a list of anime.

        Args:
            anime_list: List of AnimeEntry objects.

        Returns:
            Tuple of (mapped_anime, unmapped_anime).
        """
        mapped = []
        unmapped = []

        for anime in anime_list:
            self.map_anime(anime)
            if anime.is_mapped:
                mapped.append(anime)
            else:
                unmapped.append(anime)

        logger.info(f"Mapped {len(mapped)}/{len(anime_list)} anime ({len(unmapped)} unmapped)")
        return mapped, unmapped

    def map_episode_to_trakt(
        self,
        episode: WatchedEpisode,
        mapped_ids: MappedIds,
    ) -> tuple[int, int]:
        """Map AniDB episode to Trakt season/episode.

        Uses tvdb_season and tvdb_epoffset for mapping.
        Specials (S/C/T/P/O episodes) map to Season 0.

        Args:
            episode: WatchedEpisode from AniDB.
            mapped_ids: MappedIds for the anime.

        Returns:
            Tuple of (trakt_season, trakt_episode).
        """
        if episode.is_special:
            # All specials go to Season 0 on Trakt/TVDB
            return 0, episode.episode_number

        # Regular episodes use tvdb_season and offset
        trakt_season = mapped_ids.tvdb_season
        trakt_episode = episode.episode_number + mapped_ids.tvdb_epoffset

        return trakt_season, trakt_episode

    def get_stats(self) -> dict:
        """Get statistics about the mapping database.

        Returns:
            Dictionary with database stats.
        """
        self._ensure_loaded()

        total = len(self._mappings)
        with_tvdb = sum(1 for v in self._mappings.values() if v.get("tvdb_id"))
        with_imdb = sum(1 for v in self._mappings.values() if v.get("imdb_id"))
        with_tmdb_show = sum(1 for v in self._mappings.values() if v.get("tmdb_show_id"))
        with_tmdb_movie = sum(1 for v in self._mappings.values() if v.get("tmdb_movie_id"))

        return {
            "total_entries": total,
            "with_tvdb": with_tvdb,
            "with_imdb": with_imdb,
            "with_tmdb_show": with_tmdb_show,
            "with_tmdb_movie": with_tmdb_movie,
        }


def create_unmapped_report(unmapped: list[AnimeEntry]) -> list[dict]:
    """Create a report of unmapped anime for manual review.

    Args:
        unmapped: List of unmapped AnimeEntry objects.

    Returns:
        List of dictionaries with unmapped anime info.
    """
    report = []
    for anime in unmapped:
        report.append(
            {
                "anidb_id": anime.anidb_id,
                "title": anime.title,
                "title_english": anime.title_english,
                "type": anime.anime_type.name,
                "episodes": anime.total_episodes,
                "watched": anime.watched_count,
                "rating": anime.rating.score if anime.rating else None,
                "anidb_url": f"https://anidb.net/anime/{anime.anidb_id}",
            }
        )
    return report
