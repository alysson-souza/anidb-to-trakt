"""Tests for AniDB XML parser."""

from datetime import datetime
from pathlib import Path

import pytest

from src.models import AnimeType, EpisodeType
from src.parser import (
    AniDBParseError,
    AniDBParser,
    get_anime_type,
    parse_anidb_date,
    parse_episode_number,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_EXPORT = FIXTURES_DIR / "sample_export.xml"
SAMPLE_SINGLEFILE = FIXTURES_DIR / "sample_singlefile.xml"


class TestParseAnidbDate:
    """Tests for parse_anidb_date function."""

    def test_date_with_time(self):
        """Parse date with time: DD.MM.YYYY HH:MM"""
        result = parse_anidb_date("15.04.2023 20:30")
        assert result == datetime(2023, 4, 15, 20, 30)

    def test_date_without_time(self):
        """Parse date without time: DD.MM.YYYY"""
        result = parse_anidb_date("01.03.2023")
        assert result == datetime(2023, 3, 1)

    def test_iso_format(self):
        """Parse ISO format as fallback."""
        result = parse_anidb_date("2023-04-15")
        assert result == datetime(2023, 4, 15)

    def test_empty_string(self):
        """Empty string returns None."""
        assert parse_anidb_date("") is None

    def test_none_input(self):
        """None input returns None."""
        assert parse_anidb_date(None) is None

    def test_whitespace_string(self):
        """Whitespace-only string returns None."""
        assert parse_anidb_date("   ") is None

    def test_invalid_format(self):
        """Invalid format returns None."""
        assert parse_anidb_date("not a date") is None
        assert parse_anidb_date("2023/04/15") is None


class TestParseEpisodeNumber:
    """Tests for parse_episode_number function."""

    def test_regular_episode(self):
        """Parse regular episode number."""
        number, ep_type = parse_episode_number("1")
        assert number == 1
        assert ep_type == EpisodeType.REGULAR

    def test_special_episode(self):
        """Parse special episode with S prefix."""
        number, ep_type = parse_episode_number("S1")
        assert number == 1
        assert ep_type == EpisodeType.SPECIAL

    def test_credits_episode(self):
        """Parse credits episode with C prefix."""
        number, ep_type = parse_episode_number("C2")
        assert number == 2
        assert ep_type == EpisodeType.CREDITS

    def test_trailer_episode(self):
        """Parse trailer episode with T prefix."""
        number, ep_type = parse_episode_number("T1")
        assert number == 1
        assert ep_type == EpisodeType.TRAILER

    def test_parody_episode(self):
        """Parse parody episode with P prefix."""
        number, ep_type = parse_episode_number("P3")
        assert number == 3
        assert ep_type == EpisodeType.PARODY

    def test_other_episode(self):
        """Parse other episode with O prefix."""
        number, ep_type = parse_episode_number("O1")
        assert number == 1
        assert ep_type == EpisodeType.OTHER

    def test_lowercase_prefix(self):
        """Lowercase prefix is handled."""
        number, ep_type = parse_episode_number("s5")
        assert number == 5
        assert ep_type == EpisodeType.SPECIAL

    def test_whitespace_trimmed(self):
        """Whitespace is trimmed."""
        number, ep_type = parse_episode_number("  10  ")
        assert number == 10
        assert ep_type == EpisodeType.REGULAR

    def test_invalid_number(self):
        """Invalid number raises ValueError."""
        with pytest.raises(ValueError):
            parse_episode_number("abc")

    def test_invalid_prefix_number(self):
        """Invalid number after prefix raises ValueError."""
        with pytest.raises(ValueError):
            parse_episode_number("Sabc")


class TestGetAnimeType:
    """Tests for get_anime_type function."""

    def test_tv_type(self):
        """Type 2 is TV."""
        assert get_anime_type("2") == AnimeType.TV

    def test_ova_type(self):
        """Type 3 is OVA."""
        assert get_anime_type("3") == AnimeType.OVA

    def test_movie_type(self):
        """Type 4 is Movie."""
        assert get_anime_type("4") == AnimeType.MOVIE

    def test_web_type(self):
        """Type 6 is Web."""
        assert get_anime_type("6") == AnimeType.WEB

    def test_tv_special_type(self):
        """Type 7 is TV Special."""
        assert get_anime_type("7") == AnimeType.TV_SPECIAL

    def test_unknown_type(self):
        """Unknown type code returns UNKNOWN."""
        assert get_anime_type("99") == AnimeType.UNKNOWN

    def test_none_input(self):
        """None input returns UNKNOWN."""
        assert get_anime_type(None) == AnimeType.UNKNOWN

    def test_empty_string(self):
        """Empty string returns UNKNOWN."""
        assert get_anime_type("") == AnimeType.UNKNOWN


class TestAniDBParser:
    """Tests for AniDBParser class."""

    def test_file_not_found(self):
        """Raise error for non-existent file."""
        with pytest.raises(AniDBParseError, match="File not found"):
            AniDBParser("/nonexistent/path.xml")

    def test_parse_sample_export(self):
        """Parse sample export file."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        # Should have 7 entries in the sample
        assert len(anime_list) == 7

    def test_parse_attack_on_titan(self):
        """Parse Attack on Titan entry correctly."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        # Find Attack on Titan
        aot = next(a for a in anime_list if a.anidb_id == 11061)

        assert aot.title == "Shingeki no Kyojin"
        assert aot.title_english == "Attack on Titan"
        assert aot.display_title == "Attack on Titan"
        assert aot.anime_type == AnimeType.TV
        assert aot.total_episodes == 25
        assert aot.total_specials == 5
        assert not aot.is_hentai

        # Rating
        assert aot.rating is not None
        assert aot.rating.score == 9
        assert aot.rating.rated_at == datetime(2023, 4, 15, 20, 30)
        assert not aot.rating.is_temporary

        # Watched episodes (2 regular + 1 special)
        assert len(aot.watched_episodes) == 3
        assert aot.watched_count == 2  # Regular episodes
        assert aot.watched_special_count == 1  # Special episodes

    def test_parse_movie(self):
        """Parse movie entry correctly."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        movie = next(a for a in anime_list if a.anidb_id == 10083)

        assert movie.title == "Kimi no Na wa."
        assert movie.title_english == "Your Name."
        assert movie.anime_type == AnimeType.MOVIE
        assert movie.is_movie
        assert movie.rating.score == 10

    def test_parse_temp_vote(self):
        """Parse temporary vote correctly."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        death_note = next(a for a in anime_list if a.anidb_id == 4563)

        assert death_note.rating is not None
        assert death_note.rating.score == 8
        assert death_note.rating.is_temporary

    def test_parse_restricted_content(self):
        """Parse restricted content flag."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        restricted = next(a for a in anime_list if a.anidb_id == 99999)
        assert restricted.is_hentai

    def test_get_watched_anime(self):
        """Get only anime with watched episodes or ratings."""
        parser = AniDBParser(SAMPLE_EXPORT)
        watched = parser.get_watched_anime()

        # Should exclude "Plan to Watch" (no watched eps, no rating)
        assert len(watched) == 6
        assert all(a.watched_episodes or a.rating for a in watched)

    def test_get_watched_anime_exclude_hentai(self):
        """Exclude hentai from watched list."""
        parser = AniDBParser(SAMPLE_EXPORT)
        watched = parser.get_watched_anime(exclude_hentai=True)

        assert len(watched) == 5
        assert not any(a.is_hentai for a in watched)

    def test_multi_file_episode_earliest_date(self):
        """Use earliest ViewDate for multi-file episodes."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        multi_file = next(a for a in anime_list if a.anidb_id == 77777)

        # Should have 1 episode with the earlier date (05.06.2023)
        assert len(multi_file.watched_episodes) == 1
        assert multi_file.watched_episodes[0].watched_at == datetime(2023, 6, 5, 18, 0)

    def test_get_stats(self):
        """Get export statistics."""
        parser = AniDBParser(SAMPLE_EXPORT)
        stats = parser.get_stats()

        assert stats["total_anime"] == 7
        assert stats["with_ratings"] == 5
        assert stats["with_watched_episodes"] == 6
        assert stats["hentai_count"] == 1

    def test_iter_anime(self):
        """Iterate over anime entries."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_ids = [a.anidb_id for a in parser.iter_anime()]

        assert len(anime_ids) == 7
        assert 11061 in anime_ids  # Attack on Titan
        assert 10083 in anime_ids  # Your Name


class TestWatchedEpisode:
    """Tests for WatchedEpisode model."""

    def test_display_number_regular(self):
        """Regular episode display number."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        aot = next(a for a in anime_list if a.anidb_id == 11061)
        regular_ep = next(ep for ep in aot.watched_episodes if not ep.is_special)

        assert regular_ep.display_number == "1" or regular_ep.display_number == "2"

    def test_display_number_special(self):
        """Special episode display number with prefix."""
        parser = AniDBParser(SAMPLE_EXPORT)
        anime_list = parser.parse()

        aot = next(a for a in anime_list if a.anidb_id == 11061)
        special_ep = next(ep for ep in aot.watched_episodes if ep.is_special)

        assert special_ep.display_number == "S1"


class TestSinglefileFormat:
    """Tests for xml-singlefile-dataonly format parsing."""

    def test_parse_singlefile_format(self):
        """Parse singlefile format correctly."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        # Should have 4 anime entries
        assert len(anime_list) == 4

    def test_singlefile_format_detection(self):
        """Detect singlefile format from root element."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        parser.parse()
        assert parser._format == "singlefile"

    def test_singlefile_anime_parsing(self):
        """Parse anime metadata from singlefile format."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        aot = next(a for a in anime_list if a.anidb_id == 11061)

        assert aot.title == "Shingeki no Kyojin"
        assert aot.title_english == "Attack on Titan"
        assert aot.anime_type == AnimeType.TV
        assert aot.total_episodes == 25
        assert aot.total_specials == 5
        assert not aot.is_hentai

    def test_singlefile_rating_parsing(self):
        """Parse ratings from singlefile format."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        aot = next(a for a in anime_list if a.anidb_id == 11061)

        assert aot.rating is not None
        assert aot.rating.score == 9
        assert aot.rating.rated_at == datetime(2023, 4, 15, 20, 30)

    def test_singlefile_temp_vote(self):
        """Parse temporary vote from singlefile format."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        death_note = next(a for a in anime_list if a.anidb_id == 4563)

        assert death_note.rating is not None
        assert death_note.rating.score == 8
        assert death_note.rating.is_temporary

    def test_singlefile_watched_episodes(self):
        """Parse watched episodes joined from file elements."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        aot = next(a for a in anime_list if a.anidb_id == 11061)

        # Should have 3 watched episodes (2 regular + 1 special)
        assert len(aot.watched_episodes) == 3
        assert aot.watched_count == 2
        assert aot.watched_special_count == 1

    def test_singlefile_special_episodes(self):
        """Parse special episodes from singlefile format."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        aot = next(a for a in anime_list if a.anidb_id == 11061)

        special_ep = next(ep for ep in aot.watched_episodes if ep.is_special)
        assert special_ep.episode_number == 1
        assert special_ep.display_number == "S1"

    def test_singlefile_movie_type(self):
        """Parse movie from singlefile format."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        movie = next(a for a in anime_list if a.anidb_id == 10083)

        assert movie.anime_type == AnimeType.MOVIE
        assert movie.is_movie
        assert movie.rating.score == 10

    def test_singlefile_hentai_flag(self):
        """Parse hentai flag from singlefile format."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        restricted = next(a for a in anime_list if a.anidb_id == 99999)
        assert restricted.is_hentai

    def test_singlefile_multi_file_earliest_date(self):
        """Use earliest ViewDate when multiple files for same episode."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        death_note = next(a for a in anime_list if a.anidb_id == 4563)

        # Episode 1 has two files: 01.03.2023 and 15.02.2023
        # Should use the earlier date (15.02.2023)
        ep1 = next(
            ep for ep in death_note.watched_episodes if ep.episode_number == 1 and not ep.is_special
        )
        assert ep1.watched_at == datetime(2023, 2, 15)

    def test_singlefile_unwatched_files_ignored(self):
        """Ignore files with MyWatched=0."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        anime_list = parser.parse()

        aot = next(a for a in anime_list if a.anidb_id == 11061)

        # Should still only have 3 episodes, unwatched file ignored
        assert len(aot.watched_episodes) == 3

    def test_singlefile_stats(self):
        """Get stats from singlefile format."""
        parser = AniDBParser(SAMPLE_SINGLEFILE)
        stats = parser.get_stats()

        assert stats["total_anime"] == 4
        assert stats["with_ratings"] == 4
        assert stats["hentai_count"] == 1
        assert stats["format"] == "singlefile"
