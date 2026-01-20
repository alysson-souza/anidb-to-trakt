# AniDB to Trakt

Parse AniDB export data and sync watched anime, watch dates, and ratings to Trakt.

## Features

- Parse AniDB `xml-plain-new` export format
- Map AniDB IDs to Trakt-compatible IDs (TVDB/IMDB/TMDB)
- OAuth2 Device Code authentication with Trakt
- Sync watch history and ratings to Trakt
- Conflict resolution (older timestamp wins for ratings)
- Generate HTML/CSV reports for verification

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/anidb-to-trakt.git
cd anidb-to-trakt

# Install dependencies
uv sync
```

## Setup

1. Register an app at https://trakt.tv/oauth/applications
2. Copy `.env.example` to `.env` and add your credentials:
   ```
   TRAKT_CLIENT_ID=your_client_id
   TRAKT_CLIENT_SECRET=your_client_secret
   ```

## Usage

### Export from AniDB

1. Go to https://anidb.net/user/export
2. Select the `xml-plain-new` template
3. Download the export file

### Parse and Generate Reports

```bash
# Parse export and generate JSON/HTML/CSV files
uv run python -m src.main parse export.xml -o ./output

# With verbose output
uv run python -m src.main parse export.xml -o ./output -v
```

### Authenticate with Trakt

```bash
uv run python -m src.main auth
```

Follow the on-screen instructions to authorize the app.

### Sync to Trakt

```bash
# Sync both watch history and ratings
uv run python -m src.main sync export.xml --history --ratings

# Sync only ratings
uv run python -m src.main sync export.xml --ratings

# Dry-run (preview changes without syncing)
uv run python -m src.main sync export.xml --dry-run -v
```

### Options

| Option             | Description                                |
| ------------------ | ------------------------------------------ |
| `--dry-run`        | Parse and validate without writing/syncing |
| `--update-mapping` | Force refresh ID mapping database          |
| `-v/--verbose`     | Detailed output                            |
| `--history`        | Sync watch history                         |
| `--ratings`        | Sync ratings                               |
| `--exclude-hentai` | Skip restricted content                    |
| `--resume`         | Resume from last checkpoint                |

## Output Files

- `trakt_history.json` - Watch history for Trakt API
- `trakt_ratings.json` - Ratings for Trakt API
- `report.html` - Interactive HTML report with sortable tables
- `report.csv` - CSV export for spreadsheets
- `unmapped.json` - Anime that couldn't be mapped to Trakt IDs

## Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_parser.py -v
```

## Conflict Resolution

| Data Type     | Strategy                              |
| ------------- | ------------------------------------- |
| Ratings       | Older timestamp wins                  |
| Watch history | Additive merge, keep older timestamps |
| New anime     | Add to Trakt                          |

## License

MIT
