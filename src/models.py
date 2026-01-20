"""Data models for AniDB to Trakt conversion."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum


class AnimeType(IntEnum):
    """AniDB anime type codes."""

    UNKNOWN = 0
    TV = 2
    OVA = 3
    MOVIE = 4
    WEB = 6
    TV_SPECIAL = 7


class EpisodeType(IntEnum):
    """Episode type based on prefix."""

    REGULAR = 0  # No prefix, regular episode
    SPECIAL = 1  # S prefix
    CREDITS = 2  # C prefix
    TRAILER = 3  # T prefix
    PARODY = 4  # P prefix
    OTHER = 5  # O prefix


@dataclass
class WatchedEpisode:
    """A watched episode with its watch date."""

    episode_number: int
    episode_type: EpisodeType = EpisodeType.REGULAR
    watched_at: datetime | None = None

    @property
    def is_special(self) -> bool:
        """Check if this is a special episode (not regular)."""
        return self.episode_type != EpisodeType.REGULAR

    @property
    def display_number(self) -> str:
        """Get display number with prefix (e.g., 'S1', 'C1', '1')."""
        prefixes = {
            EpisodeType.REGULAR: "",
            EpisodeType.SPECIAL: "S",
            EpisodeType.CREDITS: "C",
            EpisodeType.TRAILER: "T",
            EpisodeType.PARODY: "P",
            EpisodeType.OTHER: "O",
        }
        return f"{prefixes[self.episode_type]}{self.episode_number}"


@dataclass
class AnimeRating:
    """User rating for an anime."""

    score: int  # 1-10
    rated_at: datetime | None = None
    is_temporary: bool = False  # True if from MyTempVote


@dataclass
class MappedIds:
    """Trakt-compatible IDs for an anime."""

    tvdb_id: int | None = None
    imdb_id: str | None = None
    tmdb_show_id: int | None = None
    tmdb_movie_id: int | None = None
    tvdb_season: int = 1
    tvdb_epoffset: int = 0

    @property
    def has_any_id(self) -> bool:
        """Check if any ID is available."""
        return any(
            [
                self.tvdb_id,
                self.imdb_id,
                self.tmdb_show_id,
                self.tmdb_movie_id,
            ]
        )

    @property
    def is_movie(self) -> bool:
        """Check if this should be treated as a movie."""
        return self.tmdb_movie_id is not None and self.tvdb_id is None

    def get_trakt_ids(self) -> dict:
        """Get IDs in Trakt API format, preferring tvdb > imdb > tmdb."""
        ids = {}
        if self.tvdb_id:
            ids["tvdb"] = self.tvdb_id
        if self.imdb_id:
            ids["imdb"] = self.imdb_id
        if self.tmdb_movie_id and not self.tvdb_id:
            ids["tmdb"] = self.tmdb_movie_id
        elif self.tmdb_show_id and not self.tvdb_id:
            ids["tmdb"] = self.tmdb_show_id
        return ids


@dataclass
class AnimeEntry:
    """A complete anime entry from AniDB export."""

    anidb_id: int
    title: str
    title_english: str | None = None
    anime_type: AnimeType = AnimeType.UNKNOWN
    total_episodes: int = 0
    total_specials: int = 0
    watched_episodes: list[WatchedEpisode] = field(default_factory=list)
    rating: AnimeRating | None = None
    mapped_ids: MappedIds | None = None
    is_hentai: bool = False

    @property
    def display_title(self) -> str:
        """Get the best available title."""
        return self.title_english or self.title

    @property
    def watched_count(self) -> int:
        """Count of watched regular episodes."""
        return sum(1 for ep in self.watched_episodes if not ep.is_special)

    @property
    def watched_special_count(self) -> int:
        """Count of watched special episodes."""
        return sum(1 for ep in self.watched_episodes if ep.is_special)

    @property
    def is_fully_watched(self) -> bool:
        """Check if all regular episodes are watched."""
        if self.total_episodes == 0:
            return False
        return self.watched_count >= self.total_episodes

    @property
    def is_mapped(self) -> bool:
        """Check if this anime has Trakt-compatible IDs."""
        return self.mapped_ids is not None and self.mapped_ids.has_any_id

    @property
    def is_movie(self) -> bool:
        """Check if this is a movie."""
        if self.anime_type == AnimeType.MOVIE:
            return True
        return bool(self.mapped_ids and self.mapped_ids.is_movie)


@dataclass
class TraktEntry:
    """An existing entry from Trakt for comparison."""

    trakt_id: int
    title: str
    ids: dict  # Raw IDs from Trakt
    rating: int | None = None
    rated_at: datetime | None = None
    watched_episodes: list[dict] = field(default_factory=list)  # [{season, episode, watched_at}]
    is_movie: bool = False


@dataclass
class ConflictResolution:
    """Result of comparing AniDB and Trakt data."""

    anime: AnimeEntry
    trakt_entry: TraktEntry | None = None

    # Rating conflict
    keep_anidb_rating: bool = True
    rating_conflict: bool = False

    # Episode conflicts (episodes to add to Trakt)
    episodes_to_sync: list[WatchedEpisode] = field(default_factory=list)

    @property
    def is_new(self) -> bool:
        """Check if this anime doesn't exist on Trakt."""
        return self.trakt_entry is None

    @property
    def has_rating_conflict(self) -> bool:
        """Check if there's a rating conflict."""
        return self.rating_conflict

    @property
    def conflict_indicator(self) -> str:
        """Get conflict indicator for reports."""
        if self.is_new:
            return "➕ New"
        if not self.rating_conflict:
            return "✅ No conflict"
        if self.keep_anidb_rating:
            return "✅ Keep AniDB"
        return "⏭️ Keep Trakt"


@dataclass
class SyncCheckpoint:
    """Checkpoint for resumable syncs."""

    last_processed_index: int = 0
    synced_ratings: list[int] = field(default_factory=list)  # AniDB IDs
    synced_history: list[int] = field(default_factory=list)  # AniDB IDs
    errors: list[dict] = field(default_factory=list)
    timestamp: datetime | None = None
