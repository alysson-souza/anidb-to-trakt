# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AniDB to Trakt is a Python CLI tool that parses AniDB XML exports and syncs watch history/ratings to Trakt. It handles ID mapping between anime databases (AniDB → TVDB/IMDB/TMDB), OAuth2 authentication with Trakt, and conflict resolution when merging data.

## Commands

```bash
# Install dependencies (development)
uv sync

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src --cov-report=term-missing

# Run a specific test file
uv run pytest tests/test_parser.py -v

# Run a specific test
uv run pytest tests/test_parser.py::test_parse_episode_number -v

# Lint and format
uv run ruff check src tests
uv run ruff format src tests

# Set up git hooks (auto-runs lint/format on commit)
uv run pre-commit install

# CLI commands
uv run python -m src.main parse export.xml -o ./output      # Parse and generate reports
uv run python -m src.main auth                               # Authenticate with Trakt
uv run python -m src.main sync export.xml --history --ratings  # Sync to Trakt
```

## Architecture

### Data Flow

```
AniDB XML → parser.py → models.AnimeEntry → id_mapper.py → mapped AnimeEntry
                                                ↓
                                     trakt_exporter.py
                                    ↙              ↘
                           JSON files          trakt_sync.py → Trakt API
                                                       ↑
                                              trakt_client.py (OAuth2)
```

### Module Responsibilities

- **parser.py**: Parses two AniDB XML formats (`xml-plain-new`, `xml-singlefile-dataonly`). Uses `_detect_format()` to auto-select parser. Handles AniDB date format `DD.MM.YYYY HH:MM` and episode prefixes (S=special, C=credits, T=trailer).

- **id_mapper.py**: Downloads and caches the Kometa-Team/Anime-IDs database. Maps AniDB IDs to TVDB/IMDB/TMDB. Episode mapping uses `tvdb_season` and `tvdb_epoffset` fields. Specials always map to Season 0.

- **trakt_client.py**: HTTP client with OAuth2 Device Code flow. Handles token storage in platform-specific config dirs (see `paths.py`). Implements rate limiting (1 POST/sec, ~3 GET/sec) and exponential backoff.

- **trakt_exporter.py**: Orchestrates export/sync. Generates Trakt-formatted JSON. Delegates to `trakt_data.py` for conflict resolution and `trakt_sync.py` for API syncing.

- **trakt_data.py**: Fetches existing Trakt data and resolves conflicts. Rating conflicts use "older timestamp wins" strategy.

- **trakt_sync.py**: Batch syncing with retry logic. Uses 50-item batches, stops after 3 consecutive server failures.

- **report.py**: Generates HTML (sortable tables with vanilla JS) and CSV reports.

### Key Models (models.py)

- `AnimeEntry`: Core data model with AniDB ID, titles, type, episodes, rating, and mapped IDs
- `MappedIds`: TVDB/IMDB/TMDB IDs plus `tvdb_season`/`tvdb_epoffset` for episode mapping
- `EpisodeType`: Enum for episode prefixes (REGULAR, SPECIAL, CREDITS, TRAILER, PARODY, OTHER)
- `ConflictResolution`: Result of comparing AniDB vs Trakt data with `keep_anidb_rating` flag

### Configuration

- Trakt credentials: `.env` file with `TRAKT_CLIENT_ID` and `TRAKT_CLIENT_SECRET`
- Tokens: Platform-specific path (macOS: `~/Library/Application Support/anidb-to-trakt/tokens.json`)
- ID mapping cache: `data/anime_ids.json` (auto-downloaded, 7-day expiry)

## Testing

Tests use pytest with async support. Test fixtures are in `tests/fixtures/`. Mock HTTP responses when testing Trakt API calls.

```bash
# Run with pytest-asyncio (configured in pyproject.toml)
pytest --asyncio-mode=auto
```
