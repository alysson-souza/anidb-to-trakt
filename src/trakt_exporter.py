"""Trakt data exporter - JSON generation and file export."""

import json
import logging
from collections import defaultdict
from pathlib import Path

from .id_mapper import IDMapper
from .models import AnimeEntry, ConflictResolution
from .trakt_client import TraktClient
from .trakt_data import ConflictResolver, TraktDataFetcher, iso_to_datetime
from .trakt_sync import (
    TraktSyncer,
    datetime_to_iso,
    load_checkpoint,
    save_checkpoint,
)

logger = logging.getLogger(__name__)

# Re-export for backwards compatibility
__all__ = [
    "TraktExporter",
    "load_checkpoint",
    "save_checkpoint",
    "datetime_to_iso",
    "iso_to_datetime",
]


class TraktExporter:
    """Export AniDB data to Trakt format and manage syncing."""

    def __init__(
        self,
        id_mapper: IDMapper,
        client: TraktClient | None = None,
    ):
        """Initialize the exporter.

        Args:
            id_mapper: IDMapper instance for ID lookups.
            client: TraktClient instance for API calls (optional).
        """
        self.id_mapper = id_mapper
        self.client = client

        # Initialize data fetcher and conflict resolver
        self._data_fetcher = TraktDataFetcher(client) if client else None
        self._conflict_resolver = ConflictResolver(id_mapper, self._data_fetcher)
        self._syncer = TraktSyncer(client, self._conflict_resolver) if client else None

    def resolve_conflicts(
        self,
        anime_list: list[AnimeEntry],
        fetch_existing: bool = True,
    ) -> list[ConflictResolution]:
        """Compare AniDB data with existing Trakt data and resolve conflicts."""
        return self._conflict_resolver.resolve(anime_list, fetch_existing)

    def generate_history_json(self, anime_list: list[AnimeEntry]) -> dict:
        """Generate Trakt history JSON format."""
        shows = []
        movies = []

        for anime in anime_list:
            if not anime.is_mapped or not anime.watched_episodes:
                continue

            ids = anime.mapped_ids
            trakt_ids = ids.get_trakt_ids()

            if ids.is_movie:
                movies.append(self._build_movie_history(anime, trakt_ids))
            else:
                shows.append(self._build_show_history(anime, ids, trakt_ids))

        result = {}
        if shows:
            result["shows"] = shows
        if movies:
            result["movies"] = movies
        return result

    def _build_movie_history(self, anime: AnimeEntry, trakt_ids: dict) -> dict:
        """Build movie history entry."""
        dates = [ep.watched_at for ep in anime.watched_episodes if ep.watched_at]
        watched_at = min(dates) if dates else None

        entry = {"ids": trakt_ids}
        if watched_at:
            entry["watched_at"] = datetime_to_iso(watched_at)
        return entry

    def _build_show_history(self, anime: AnimeEntry, ids, trakt_ids: dict) -> dict:
        """Build show history entry with seasons/episodes."""
        seasons_data: dict[int, list] = defaultdict(list)

        for ep in anime.watched_episodes:
            trakt_season, trakt_ep = self.id_mapper.map_episode_to_trakt(ep, ids)
            ep_entry = {"number": trakt_ep}
            if ep.watched_at:
                ep_entry["watched_at"] = datetime_to_iso(ep.watched_at)
            seasons_data[trakt_season].append(ep_entry)

        return {
            "ids": trakt_ids,
            "seasons": [
                {"number": num, "episodes": eps} for num, eps in sorted(seasons_data.items())
            ],
        }

    def generate_ratings_json(
        self,
        anime_list: list[AnimeEntry],
        resolutions: list[ConflictResolution] | None = None,
    ) -> dict:
        """Generate Trakt ratings JSON format."""
        shows = []
        movies = []

        resolution_map = {r.anime.anidb_id: r for r in (resolutions or [])}

        for anime in anime_list:
            if not anime.is_mapped or not anime.rating:
                continue

            # Skip if conflict resolution says keep Trakt rating
            if anime.anidb_id in resolution_map:
                res = resolution_map[anime.anidb_id]
                if not res.keep_anidb_rating and res.rating_conflict:
                    continue

            ids = anime.mapped_ids
            entry = {
                "ids": ids.get_trakt_ids(),
                "rating": anime.rating.score,
            }
            if anime.rating.rated_at:
                entry["rated_at"] = datetime_to_iso(anime.rating.rated_at)

            if ids.is_movie:
                movies.append(entry)
            else:
                shows.append(entry)

        result = {}
        if shows:
            result["shows"] = shows
        if movies:
            result["movies"] = movies
        return result

    def export_to_files(
        self,
        anime_list: list[AnimeEntry],
        output_dir: Path,
        resolutions: list[ConflictResolution] | None = None,
    ) -> dict[str, Path]:
        """Export data to JSON files."""
        output_dir.mkdir(parents=True, exist_ok=True)
        files = {}

        history_data = self.generate_history_json(anime_list)
        if history_data:
            path = output_dir / "trakt_history.json"
            self._write_json(history_data, path)
            files["history"] = path

        ratings_data = self.generate_ratings_json(anime_list, resolutions)
        if ratings_data:
            path = output_dir / "trakt_ratings.json"
            self._write_json(ratings_data, path)
            files["ratings"] = path

        return files

    def _write_json(self, data: dict, path: Path) -> None:
        """Write JSON to file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Wrote {path.name} to {path}")

    def sync_to_trakt(
        self,
        anime_list: list[AnimeEntry],
        sync_history: bool = True,
        sync_ratings: bool = True,
        dry_run: bool = False,
        checkpoint=None,
        output_dir: Path | None = None,
    ) -> dict:
        """Sync data directly to Trakt."""
        if not self._syncer or not self.client or not self.client.is_authenticated:
            raise RuntimeError("Trakt client not authenticated")

        # Resolve conflicts first
        resolutions = self.resolve_conflicts(anime_list)

        # Generate data
        ratings_data = self.generate_ratings_json(anime_list, resolutions) if sync_ratings else None
        history_data = self.generate_history_json(anime_list) if sync_history else None

        # Sync using the syncer
        return self._syncer.sync(
            anime_list=anime_list,
            ratings_data=ratings_data,
            history_data=history_data,
            sync_ratings=sync_ratings,
            sync_history=sync_history,
            dry_run=dry_run,
            output_dir=output_dir,
        )
