"""Trakt API sync with retry logic and batching."""

import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .models import AnimeEntry, SyncCheckpoint
from .trakt_client import TraktAPIError, TraktClient
from .trakt_data import ConflictResolver, iso_to_datetime

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 50
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_BASE_SECONDS = 5
MAX_BACKOFF_SECONDS = 60


def datetime_to_iso(dt: datetime | None) -> str | None:
    """Convert datetime to ISO 8601 format for Trakt API."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TraktSyncer:
    """Sync data to Trakt with retry logic and batch processing."""

    def __init__(self, client: TraktClient, conflict_resolver: ConflictResolver):
        self.client = client
        self.conflict_resolver = conflict_resolver

    def sync(
        self,
        anime_list: list[AnimeEntry],
        ratings_data: dict | None = None,
        history_data: dict | None = None,
        sync_ratings: bool = True,
        sync_history: bool = True,
        dry_run: bool = False,
        output_dir: Path | None = None,
    ) -> dict:
        """Sync data to Trakt.

        Args:
            anime_list: List of mapped AnimeEntry objects.
            ratings_data: Pre-generated ratings JSON (optional).
            history_data: Pre-generated history JSON (optional).
            sync_ratings: Sync ratings.
            sync_history: Sync watch history.
            dry_run: Preview changes without syncing.
            output_dir: Directory to save failed batches.

        Returns:
            Sync results summary.
        """
        if not self.client.is_authenticated:
            raise RuntimeError("Trakt client not authenticated")

        results = self._init_results()

        try:
            if sync_ratings and ratings_data:
                if dry_run:
                    self._dry_run_ratings(ratings_data, results)
                else:
                    self._sync_ratings(ratings_data, results)

            if sync_history and history_data and not results["stopped_early"]:
                if dry_run:
                    self._dry_run_history(history_data, results)
                else:
                    self._sync_history(history_data, results)

        except KeyboardInterrupt:
            logger.warning("Sync interrupted by user")
            results["stopped_early"] = True
            results["errors"].append("Interrupted by user (Ctrl+C)")

        finally:
            self._save_failed_batches(results, output_dir)

        return results

    def _init_results(self) -> dict:
        """Initialize results dictionary."""
        return {
            "history_added": 0,
            "history_existing": 0,
            "ratings_added": 0,
            "ratings_existing": 0,
            "errors": [],
            "failed_batches": [],
            "stopped_early": False,
        }

    def _sync_ratings(self, ratings_data: dict, results: dict) -> None:
        """Sync ratings in batches."""
        all_items = [
            (item_type, item)
            for item_type in ("shows", "movies")
            for item in ratings_data.get(item_type, [])
        ]

        if not all_items:
            return

        total_batches = (len(all_items) + BATCH_SIZE - 1) // BATCH_SIZE
        consecutive_failures = 0

        for i in range(0, len(all_items), BATCH_SIZE):
            batch = all_items[i : i + BATCH_SIZE]
            batch_data = defaultdict(list)
            for item_type, item in batch:
                batch_data[item_type].append(item)

            batch_num = i // BATCH_SIZE + 1
            response = self._sync_batch(
                self.client.sync_ratings,
                dict(batch_data),
                batch_num,
                total_batches,
                consecutive_failures,
                results,
                "ratings",
            )

            if response is None:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    results["stopped_early"] = True
                    logger.error("Stopping ratings sync due to consecutive failures.")
                    return
            else:
                consecutive_failures = 0
                added = response.get("added", {})
                not_found = response.get("not_found", {})
                results["ratings_added"] += added.get("shows", 0) + added.get("movies", 0)
                results["ratings_existing"] += len(not_found.get("shows", []))
                results["ratings_existing"] += len(not_found.get("movies", []))
                logger.info(
                    f"Batch {batch_num}/{total_batches}: "
                    f"{added.get('shows', 0) + added.get('movies', 0)} ratings added"
                )

    def _sync_history(self, history_data: dict, results: dict) -> None:
        """Sync watch history in batches."""
        consecutive_failures = 0

        for item_type in ("shows", "movies"):
            items = history_data.get(item_type, [])
            if not items:
                continue

            total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

            for i in range(0, len(items), BATCH_SIZE):
                batch_data = {item_type: items[i : i + BATCH_SIZE]}
                batch_num = i // BATCH_SIZE + 1

                response = self._sync_batch(
                    self.client.sync_history,
                    batch_data,
                    batch_num,
                    total_batches,
                    consecutive_failures,
                    results,
                    f"history/{item_type}",
                )

                if response is None:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        results["stopped_early"] = True
                        logger.error("Stopping history sync due to consecutive failures.")
                        return
                else:
                    consecutive_failures = 0
                    added = response.get("added", {})
                    not_found = response.get("not_found", {})
                    results["history_added"] += added.get("episodes", 0) + added.get("movies", 0)
                    results["history_existing"] += len(not_found.get("shows", []))
                    results["history_existing"] += len(not_found.get("movies", []))
                    logger.info(
                        f"Batch {batch_num}/{total_batches} ({item_type}): "
                        f"{added.get('episodes', 0)} episodes, {added.get('movies', 0)} movies"
                    )

    def _sync_batch(
        self,
        sync_func,
        batch_data: dict,
        batch_num: int,
        total_batches: int,
        consecutive_failures: int,
        results: dict,
        batch_type: str,
    ) -> dict | None:
        """Sync a single batch with retry on server error."""
        try:
            return sync_func(batch_data)

        except TraktAPIError as e:
            if e.status_code >= 500:
                backoff = min(
                    BACKOFF_BASE_SECONDS * (2**consecutive_failures),
                    MAX_BACKOFF_SECONDS,
                )
                logger.warning(
                    f"Batch {batch_num}/{total_batches}: Server error {e.status_code}, "
                    f"waiting {backoff}s ({consecutive_failures + 1}/{MAX_CONSECUTIVE_FAILURES})"
                )
                time.sleep(backoff)

                try:
                    response = sync_func(batch_data)
                    logger.info(f"Batch {batch_num}/{total_batches}: Retry successful")
                    return response
                except TraktAPIError as retry_e:
                    logger.error(
                        f"Batch {batch_num}/{total_batches}: Retry failed ({retry_e.status_code})"
                    )
            else:
                logger.error(f"Batch {batch_num}/{total_batches}: API error {e.status_code}")

            results["failed_batches"].append({"type": batch_type, "data": batch_data})
            results["errors"].append(f"{batch_type} batch {batch_num} failed")
            return None

        except Exception as e:
            logger.error(f"Batch {batch_num}/{total_batches}: Unexpected error: {e}")
            results["failed_batches"].append({"type": batch_type, "data": batch_data})
            results["errors"].append(f"{batch_type} batch {batch_num} failed: {e}")
            return None

    def _dry_run_ratings(self, ratings_data: dict, results: dict) -> None:
        """Log dry run info for ratings."""
        count = len(ratings_data.get("shows", [])) + len(ratings_data.get("movies", []))
        logger.info(f"[DRY RUN] Would sync {count} ratings")
        results["ratings_added"] = count

    def _dry_run_history(self, history_data: dict, results: dict) -> None:
        """Log dry run info for history."""
        ep_count = sum(
            sum(len(s.get("episodes", [])) for s in show.get("seasons", []))
            for show in history_data.get("shows", [])
        )
        movie_count = len(history_data.get("movies", []))
        logger.info(f"[DRY RUN] Would sync {ep_count} episodes and {movie_count} movies")
        results["history_added"] = ep_count + movie_count

    def _save_failed_batches(self, results: dict, output_dir: Path | None) -> None:
        """Save failed batches to file."""
        if not results["failed_batches"] or not output_dir:
            return

        failed_path = output_dir / "failed_batches.json"
        with open(failed_path, "w", encoding="utf-8") as f:
            json.dump(results["failed_batches"], f, indent=2)
        logger.info(f"Saved {len(results['failed_batches'])} failed batches to {failed_path}")


def load_checkpoint(path: Path) -> SyncCheckpoint | None:
    """Load sync checkpoint from file."""
    if not path.exists():
        return None

    try:
        with open(path) as f:
            data = json.load(f)
        return SyncCheckpoint(
            last_processed_index=data.get("last_processed_index", 0),
            synced_ratings=data.get("synced_ratings", []),
            synced_history=data.get("synced_history", []),
            errors=data.get("errors", []),
            timestamp=iso_to_datetime(data.get("timestamp")),
        )
    except (json.JSONDecodeError, OSError):
        return None


def save_checkpoint(checkpoint: SyncCheckpoint, path: Path) -> None:
    """Save sync checkpoint to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_processed_index": checkpoint.last_processed_index,
        "synced_ratings": checkpoint.synced_ratings,
        "synced_history": checkpoint.synced_history,
        "errors": checkpoint.errors,
        "timestamp": datetime_to_iso(checkpoint.timestamp or datetime.now()),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
