"""Trakt data fetching and conflict resolution."""

import logging
from datetime import datetime

from .id_mapper import IDMapper
from .models import AnimeEntry, ConflictResolution, TraktEntry
from .trakt_client import TraktClient

logger = logging.getLogger(__name__)


def iso_to_datetime(iso_str: str | None) -> datetime | None:
    """Parse ISO 8601 datetime string."""
    if not iso_str:
        return None
    try:
        iso_str = iso_str.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_str.replace("+00:00", ""))
    except ValueError:
        return None


class TraktDataFetcher:
    """Fetch and cache existing Trakt data for comparison."""

    def __init__(self, client: TraktClient):
        self.client = client
        self._ratings: dict = {}
        self._watched: dict = {}

    def fetch(self) -> None:
        """Fetch existing ratings and watched data from Trakt."""
        if not self.client.is_authenticated:
            return

        logger.info("Fetching existing Trakt data...")
        self._fetch_ratings()
        self._fetch_watched()
        logger.info(
            f"Loaded {len(self._ratings)} ratings and {len(self._watched)} watched items from Trakt"
        )

    def _fetch_ratings(self) -> None:
        """Fetch show and movie ratings."""
        try:
            for item in self.client.get_user_ratings("shows"):
                show = item.get("show", {})
                ids = show.get("ids", {})
                if tvdb_id := ids.get("tvdb"):
                    self._ratings[f"show_{tvdb_id}"] = {
                        "rating": item.get("rating"),
                        "rated_at": iso_to_datetime(item.get("rated_at")),
                        "trakt_id": ids.get("trakt"),
                        "title": show.get("title"),
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch show ratings: {e}")

        try:
            for item in self.client.get_user_ratings("movies"):
                movie = item.get("movie", {})
                ids = movie.get("ids", {})
                if tmdb_id := ids.get("tmdb"):
                    self._ratings[f"movie_{tmdb_id}"] = {
                        "rating": item.get("rating"),
                        "rated_at": iso_to_datetime(item.get("rated_at")),
                        "trakt_id": ids.get("trakt"),
                        "title": movie.get("title"),
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch movie ratings: {e}")

    def _fetch_watched(self) -> None:
        """Fetch watched shows."""
        try:
            for item in self.client.get_user_watched("shows"):
                show = item.get("show", {})
                ids = show.get("ids", {})
                if tvdb_id := ids.get("tvdb"):
                    seasons = [
                        {
                            "number": season.get("number"),
                            "episodes": [
                                {"number": ep.get("number"), "plays": ep.get("plays", 1)}
                                for ep in season.get("episodes", [])
                            ],
                        }
                        for season in item.get("seasons", [])
                    ]
                    self._watched[f"show_{tvdb_id}"] = {
                        "seasons": seasons,
                        "trakt_id": ids.get("trakt"),
                        "title": show.get("title"),
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch watched shows: {e}")

    def get_entry(self, anime: AnimeEntry) -> TraktEntry | None:
        """Get existing Trakt entry for an anime."""
        if not anime.mapped_ids:
            return None

        ids = anime.mapped_ids
        if ids.is_movie and ids.tmdb_movie_id:
            key = f"movie_{ids.tmdb_movie_id}"
        elif ids.tvdb_id:
            key = f"show_{ids.tvdb_id}"
        else:
            return None

        rating_data = self._ratings.get(key)
        watched_data = self._watched.get(key)

        if not rating_data and not watched_data:
            return None

        data = rating_data or watched_data or {}
        watched_episodes = []
        if watched_data:
            for season in watched_data.get("seasons", []):
                for ep in season.get("episodes", []):
                    watched_episodes.append(
                        {
                            "season": season.get("number"),
                            "episode": ep.get("number"),
                        }
                    )

        return TraktEntry(
            trakt_id=data.get("trakt_id", 0),
            title=data.get("title", ""),
            ids=ids.get_trakt_ids(),
            rating=rating_data.get("rating") if rating_data else None,
            rated_at=rating_data.get("rated_at") if rating_data else None,
            watched_episodes=watched_episodes,
            is_movie=ids.is_movie,
        )


class ConflictResolver:
    """Resolve conflicts between AniDB and Trakt data."""

    def __init__(self, id_mapper: IDMapper, data_fetcher: TraktDataFetcher | None = None):
        self.id_mapper = id_mapper
        self.data_fetcher = data_fetcher

    def resolve(
        self,
        anime_list: list[AnimeEntry],
        fetch_existing: bool = True,
    ) -> list[ConflictResolution]:
        """Compare AniDB data with existing Trakt data and resolve conflicts.

        Conflict resolution strategy:
        - Ratings: Older timestamp wins
        - Watch history: Additive merge, keep older timestamps
        """
        if fetch_existing and self.data_fetcher:
            self.data_fetcher.fetch()

        resolutions = []
        for anime in anime_list:
            if not anime.is_mapped:
                continue

            trakt_entry = self.data_fetcher.get_entry(anime) if self.data_fetcher else None
            resolution = self._resolve_single(anime, trakt_entry)
            resolutions.append(resolution)

        return resolutions

    def _resolve_single(
        self, anime: AnimeEntry, trakt_entry: TraktEntry | None
    ) -> ConflictResolution:
        """Resolve conflicts for a single anime."""
        resolution = ConflictResolution(anime=anime, trakt_entry=trakt_entry)

        # Rating conflict
        if anime.rating and trakt_entry and trakt_entry.rating:
            resolution.rating_conflict = anime.rating.score != trakt_entry.rating
            if resolution.rating_conflict:
                resolution.keep_anidb_rating = self._older_rating_wins(
                    anime.rating.rated_at, trakt_entry.rated_at
                )
        elif anime.rating:
            resolution.keep_anidb_rating = True

        # Episodes to sync (additive)
        if anime.watched_episodes:
            existing_eps = set()
            if trakt_entry:
                for ep in trakt_entry.watched_episodes:
                    existing_eps.add((ep["season"], ep["episode"]))

            for ep in anime.watched_episodes:
                trakt_season, trakt_ep = self.id_mapper.map_episode_to_trakt(ep, anime.mapped_ids)
                if (trakt_season, trakt_ep) not in existing_eps:
                    resolution.episodes_to_sync.append(ep)

        return resolution

    def _older_rating_wins(self, anidb_date: datetime | None, trakt_date: datetime | None) -> bool:
        """Determine which rating to keep based on timestamps."""
        if anidb_date and trakt_date:
            return anidb_date < trakt_date
        return anidb_date is not None
