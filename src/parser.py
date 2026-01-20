"""AniDB XML export parser supporting multiple export formats."""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .models import (
    AnimeEntry,
    AnimeRating,
    AnimeType,
    EpisodeType,
    WatchedEpisode,
)

logger = logging.getLogger(__name__)


class AniDBParseError(Exception):
    """Error parsing AniDB export file."""

    pass


def parse_anidb_date(date_str: str | None) -> datetime | None:
    """Parse AniDB date format: DD.MM.YYYY HH:MM or DD.MM.YYYY.

    Args:
        date_str: Date string in AniDB format or None.

    Returns:
        Parsed datetime or None if parsing fails.
    """
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()

    # Skip placeholder values
    if date_str in ("-", ""):
        return None

    # Try with time first: DD.MM.YYYY HH:MM
    try:
        return datetime.strptime(date_str, "%d.%m.%Y %H:%M")
    except ValueError:
        pass

    # Try without time: DD.MM.YYYY
    try:
        return datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        pass

    # Try ISO format as fallback: YYYY-MM-DD
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        pass

    return None


def parse_episode_number(ep_str: str) -> tuple[int, EpisodeType]:
    """Parse episode number string with optional prefix.

    Args:
        ep_str: Episode string like "1", "S1", "C2", "T1", etc.

    Returns:
        Tuple of (episode_number, episode_type).

    Raises:
        ValueError: If the episode string cannot be parsed.
    """
    ep_str = ep_str.strip().upper()

    prefix_map = {
        "S": EpisodeType.SPECIAL,
        "C": EpisodeType.CREDITS,
        "T": EpisodeType.TRAILER,
        "P": EpisodeType.PARODY,
        "O": EpisodeType.OTHER,
    }

    # Check for prefix
    if ep_str and ep_str[0] in prefix_map:
        prefix = ep_str[0]
        num_str = ep_str[1:]
        if not num_str.isdigit():
            raise ValueError(f"Invalid episode number: {ep_str}")
        return int(num_str), prefix_map[prefix]

    # Regular episode
    if not ep_str.isdigit():
        raise ValueError(f"Invalid episode number: {ep_str}")

    return int(ep_str), EpisodeType.REGULAR


def get_anime_type(type_code: str | None) -> AnimeType:
    """Convert AniDB type code to AnimeType enum.

    Args:
        type_code: Type code string from XML.

    Returns:
        AnimeType enum value.
    """
    if not type_code:
        return AnimeType.UNKNOWN

    try:
        code = int(type_code)
        return AnimeType(code)
    except (ValueError, KeyError):
        return AnimeType.UNKNOWN


def _get_text(element: ET.Element | None, default: str = "") -> str:
    """Safely get text content from an element (handles CDATA)."""
    if element is None:
        return default
    return element.text.strip() if element.text else default


def _get_int(element: ET.Element | None, default: int = 0) -> int:
    """Safely get integer value from an element."""
    text = _get_text(element)
    if not text or text == "-":
        return default
    # Remove thousands separators (e.g., "5.238.534.343.746")
    text = text.replace(".", "").replace(",", "")
    try:
        return round(float(text))
    except ValueError:
        return default


def _detect_format(root: ET.Element) -> str:
    """Detect the export format from root element.

    Returns:
        'singlefile' for xml-singlefile-dataonly
        'plain-new' for xml-plain-new
    """
    if root.tag == "my_anime_list":
        return "singlefile"
    if root.tag == "MyList":
        return "plain-new"
    # Check for lowercase anime elements (singlefile format)
    if root.find("anime") is not None:
        return "singlefile"
    if root.find("Anime") is not None:
        return "plain-new"
    return "plain-new"  # Default


# =============================================================================
# xml-plain-new format parser
# =============================================================================


def _parse_anime_plain_new(anime_elem: ET.Element) -> AnimeEntry | None:
    """Parse anime from xml-plain-new format."""
    anidb_id_elem = anime_elem.find("AnimeID")
    if anidb_id_elem is None or not anidb_id_elem.text:
        return None

    try:
        anidb_id = int(anidb_id_elem.text.strip())
    except ValueError:
        return None

    title = _get_text(anime_elem.find("Name"), f"Unknown Anime {anidb_id}")
    title_english = _get_text(anime_elem.find("NameEnglish")) or None
    anime_type = get_anime_type(_get_text(anime_elem.find("Type")))
    total_episodes = _get_int(anime_elem.find("EpisodeCount"))
    total_specials = _get_int(anime_elem.find("SpecialCount"))
    is_hentai = _get_text(anime_elem.find("IsRestricted")).lower() in ("1", "true", "yes")

    rating = _parse_rating(anime_elem)
    watched_episodes = _parse_watched_episodes_plain_new(anime_elem)

    return AnimeEntry(
        anidb_id=anidb_id,
        title=title,
        title_english=title_english,
        anime_type=anime_type,
        total_episodes=total_episodes,
        total_specials=total_specials,
        watched_episodes=watched_episodes,
        rating=rating,
        is_hentai=is_hentai,
    )


def _parse_watched_episodes_plain_new(anime_elem: ET.Element) -> list[WatchedEpisode]:
    """Parse watched episodes from xml-plain-new anime element."""
    watched_episodes: dict[str, WatchedEpisode] = {}

    episodes_elem = anime_elem.find("Episodes")
    if episodes_elem is None:
        return []

    for ep_elem in episodes_elem.findall("Episode"):
        watched_flag = _get_text(ep_elem.find("MyEpWatched"))
        if watched_flag not in ("1", "true", "yes"):
            continue

        ep_no_text = _get_text(ep_elem.find("EpNo"))
        if not ep_no_text:
            continue

        try:
            ep_number, ep_type = parse_episode_number(ep_no_text)
        except ValueError:
            continue

        ep_key = f"{ep_type.value}_{ep_number}"
        view_date = _get_earliest_view_date_plain_new(ep_elem)

        if ep_key in watched_episodes:
            existing = watched_episodes[ep_key]
            if view_date and existing.watched_at:
                if view_date < existing.watched_at:
                    existing.watched_at = view_date
            elif view_date and not existing.watched_at:
                existing.watched_at = view_date
        else:
            watched_episodes[ep_key] = WatchedEpisode(
                episode_number=ep_number,
                episode_type=ep_type,
                watched_at=view_date,
            )

    return list(watched_episodes.values())


def _get_earliest_view_date_plain_new(ep_elem: ET.Element) -> datetime | None:
    """Get earliest ViewDate from xml-plain-new episode element."""
    earliest_date: datetime | None = None

    ep_view_date = parse_anidb_date(_get_text(ep_elem.find("ViewDate")))
    if ep_view_date:
        earliest_date = ep_view_date

    files_elem = ep_elem.find("Files")
    if files_elem is not None:
        for file_elem in files_elem.findall("File"):
            file_view_date = parse_anidb_date(_get_text(file_elem.find("ViewDate")))
            if file_view_date and (earliest_date is None or file_view_date < earliest_date):
                earliest_date = file_view_date

    return earliest_date


# =============================================================================
# xml-singlefile-dataonly format parser
# =============================================================================


def _parse_singlefile_format(root: ET.Element) -> list[AnimeEntry]:
    """Parse xml-singlefile-dataonly format.

    This format has separate sections for anime, episodes, and files.
    We need to join them together.
    """
    # Step 1: Parse all anime entries
    anime_map: dict[int, AnimeEntry] = {}
    for anime_elem in root.findall("anime"):
        entry = _parse_anime_singlefile(anime_elem)
        if entry:
            anime_map[entry.anidb_id] = entry

    logger.debug(f"Parsed {len(anime_map)} anime entries")

    # Step 2: Build episode lookup (EpID -> (AnimeID, EpNo))
    episode_map: dict[int, tuple[int, str]] = {}
    for ep_elem in root.findall("episode"):
        ep_id = _get_int(ep_elem.find("EpID"))
        anime_id = _get_int(ep_elem.find("AnimeID"))
        ep_no = _get_text(ep_elem.find("EpNo"))
        if ep_id and anime_id and ep_no:
            episode_map[ep_id] = (anime_id, ep_no)

    logger.debug(f"Parsed {len(episode_map)} episode entries")

    # Step 3: Parse files and assign watched episodes to anime
    watched_files = 0
    for file_elem in root.findall("file"):
        my_watched = _get_text(file_elem.find("MyWatched"))
        if my_watched != "1":
            continue

        watched_files += 1
        anime_id = _get_int(file_elem.find("AnimeID"))
        ep_id = _get_int(file_elem.find("EpID"))
        view_date = parse_anidb_date(_get_text(file_elem.find("ViewDate")))

        if anime_id not in anime_map:
            continue

        # Get episode number from episode_map
        if ep_id not in episode_map:
            continue

        _, ep_no = episode_map[ep_id]

        try:
            ep_number, ep_type = parse_episode_number(ep_no)
        except ValueError:
            continue

        # Add or update watched episode
        anime = anime_map[anime_id]

        # Find existing episode or create new
        existing_ep = None
        for ep in anime.watched_episodes:
            if ep.episode_type == ep_type and ep.episode_number == ep_number:
                existing_ep = ep
                break

        if existing_ep:
            # Use earliest view date
            if view_date and (existing_ep.watched_at is None or view_date < existing_ep.watched_at):
                existing_ep.watched_at = view_date
        else:
            anime.watched_episodes.append(
                WatchedEpisode(
                    episode_number=ep_number,
                    episode_type=ep_type,
                    watched_at=view_date,
                )
            )

    logger.debug(f"Processed {watched_files} watched files")

    return list(anime_map.values())


def _parse_anime_singlefile(anime_elem: ET.Element) -> AnimeEntry | None:
    """Parse anime from xml-singlefile-dataonly format."""
    anidb_id = _get_int(anime_elem.find("AnimeID"))
    if not anidb_id:
        return None

    title = _get_text(anime_elem.find("Name"), f"Unknown Anime {anidb_id}")
    title_english = _get_text(anime_elem.find("NameEnglish")) or None

    # TypeID instead of Type
    anime_type = get_anime_type(_get_text(anime_elem.find("TypeID")))

    # Eps/EpsSpecial instead of EpisodeCount/SpecialCount
    total_episodes = _get_int(anime_elem.find("Eps"))
    total_specials = _get_int(anime_elem.find("EpsSpecial"))

    # Hentai instead of IsRestricted
    is_hentai = _get_text(anime_elem.find("Hentai")) == "1"

    rating = _parse_rating(anime_elem)

    return AnimeEntry(
        anidb_id=anidb_id,
        title=title,
        title_english=title_english,
        anime_type=anime_type,
        total_episodes=total_episodes,
        total_specials=total_specials,
        watched_episodes=[],  # Will be populated from files
        rating=rating,
        is_hentai=is_hentai,
    )


# =============================================================================
# Common functions
# =============================================================================


def _parse_rating(anime_elem: ET.Element) -> AnimeRating | None:
    """Parse rating from anime element (works for both formats)."""
    vote_text = _get_text(anime_elem.find("MyVote"))
    vote_date_text = _get_text(anime_elem.find("MyVoteDate"))
    is_temp = False

    if not vote_text or vote_text == "-":
        vote_text = _get_text(anime_elem.find("MyTempVote"))
        vote_date_text = _get_text(anime_elem.find("MyTempVoteDate"))
        is_temp = True

    if not vote_text or vote_text == "-":
        return None

    try:
        score = round(float(vote_text))
        score = max(1, min(10, score))
    except ValueError:
        return None

    rated_at = parse_anidb_date(vote_date_text)

    return AnimeRating(score=score, rated_at=rated_at, is_temporary=is_temp)


class AniDBParser:
    """Parser for AniDB XML export files.

    Supports:
    - xml-plain-new format
    - xml-singlefile-dataonly format
    """

    def __init__(self, file_path: str | Path):
        """Initialize parser with export file path.

        Args:
            file_path: Path to the AniDB export XML file.
        """
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise AniDBParseError(f"File not found: {self.file_path}")

        self._format: str | None = None
        self._entries: list[AnimeEntry] | None = None

    def parse(self) -> list[AnimeEntry]:
        """Parse the export file and return all anime entries.

        Returns:
            List of AnimeEntry objects.

        Raises:
            AniDBParseError: If parsing fails.
        """
        if self._entries is not None:
            return self._entries

        try:
            tree = ET.parse(self.file_path)
            root = tree.getroot()
        except ET.ParseError as e:
            raise AniDBParseError(f"Failed to parse XML: {e}") from e

        self._format = _detect_format(root)
        logger.info(f"Detected export format: {self._format}")

        if self._format == "singlefile":
            self._entries = _parse_singlefile_format(root)
        else:
            # xml-plain-new format
            anime_list = root.findall(".//Anime")
            if not anime_list:
                anime_list = root.findall("Anime")

            self._entries = []
            for anime_elem in anime_list:
                entry = _parse_anime_plain_new(anime_elem)
                if entry:
                    self._entries.append(entry)

        return self._entries

    def iter_anime(self) -> Iterator[AnimeEntry]:
        """Iterate over anime entries in the export file.

        Yields:
            AnimeEntry objects.
        """
        yield from self.parse()

    def get_watched_anime(self, exclude_hentai: bool = False) -> list[AnimeEntry]:
        """Get only anime with watched episodes or ratings.

        Args:
            exclude_hentai: Skip restricted content if True.

        Returns:
            List of watched anime entries.
        """
        watched = []
        for entry in self.parse():
            if exclude_hentai and entry.is_hentai:
                continue
            if entry.watched_episodes or entry.rating:
                watched.append(entry)
        return watched

    def get_stats(self) -> dict:
        """Get statistics about the export.

        Returns:
            Dictionary with stats like total anime, watched count, etc.
        """
        entries = self.parse()

        total = len(entries)
        with_ratings = sum(1 for e in entries if e.rating)
        with_watched = sum(1 for e in entries if e.watched_episodes)
        total_watched_eps = sum(len(e.watched_episodes) for e in entries)
        hentai_count = sum(1 for e in entries if e.is_hentai)

        return {
            "total_anime": total,
            "with_ratings": with_ratings,
            "with_watched_episodes": with_watched,
            "total_watched_episodes": total_watched_eps,
            "hentai_count": hentai_count,
            "format": self._format,
        }
