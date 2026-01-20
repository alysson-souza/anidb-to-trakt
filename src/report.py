"""Report generator for HTML and CSV output."""

import csv
import html
import logging
from datetime import datetime
from pathlib import Path

from .models import AnimeEntry, ConflictResolution

logger = logging.getLogger(__name__)


def _format_date(dt: datetime | None) -> str:
    """Format datetime for display."""
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m")


def _get_status_indicator(anime: AnimeEntry, resolution: ConflictResolution | None) -> str:
    """Get status indicator for an anime entry."""
    if not anime.is_mapped:
        return "⚠️ Unmapped"

    if resolution:
        if resolution.is_new:
            return "➕ New"
        if not resolution.has_rating_conflict and not resolution.episodes_to_sync:
            return "✅ Synced"
        return "🔄 To Sync"

    return "✅ Mapped"


def _generate_links(anime: AnimeEntry) -> dict[str, str]:
    """Generate database links for an anime entry."""
    links = {
        "anidb": f"https://anidb.net/anime/{anime.anidb_id}",
    }

    if anime.mapped_ids:
        ids = anime.mapped_ids
        if ids.tvdb_id:
            links["tvdb"] = f"https://thetvdb.com/dereferrer/series/{ids.tvdb_id}"
        if ids.imdb_id:
            links["imdb"] = f"https://www.imdb.com/title/{ids.imdb_id}"
        if ids.tmdb_movie_id:
            links["tmdb"] = f"https://www.themoviedb.org/movie/{ids.tmdb_movie_id}"
        elif ids.tmdb_show_id:
            links["tmdb"] = f"https://www.themoviedb.org/tv/{ids.tmdb_show_id}"

        # Trakt search link (use anime.is_movie which checks AnimeType first)
        title_encoded = anime.display_title.replace(" ", "+")
        if anime.is_movie:
            links["trakt"] = f"https://trakt.tv/search/movies?query={title_encoded}"
        else:
            links["trakt"] = f"https://trakt.tv/search/shows?query={title_encoded}"

    return links


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AniDB to Trakt Report</title>
    <style>
        :root {{
            --bg-color: #1a1a2e;
            --card-bg: #16213e;
            --text-color: #eee;
            --text-muted: #888;
            --accent: #e94560;
            --success: #4ade80;
            --warning: #fbbf24;
            --border: #333;
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 20px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        h1 {{
            color: var(--accent);
            margin-bottom: 10px;
        }}
        .stats {{
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }}
        .stat {{
            background: var(--card-bg);
            padding: 15px 25px;
            border-radius: 8px;
            border-left: 4px solid var(--accent);
        }}
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            color: var(--accent);
        }}
        .stat-label {{
            color: var(--text-muted);
            font-size: 0.9em;
        }}
        .filters {{
            background: var(--card-bg);
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .filters label {{
            display: flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
        }}
        .filters input[type="text"] {{
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 4px;
            background: var(--bg-color);
            color: var(--text-color);
            width: 200px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--card-bg);
            border-radius: 8px;
            overflow: hidden;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            background: var(--bg-color);
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
        }}
        th:hover {{
            background: #252550;
        }}
        th.sorted-asc::after {{
            content: ' ▲';
        }}
        th.sorted-desc::after {{
            content: ' ▼';
        }}
        tr:hover {{
            background: rgba(233, 69, 96, 0.1);
        }}
        a {{
            color: var(--accent);
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .links {{
            display: flex;
            gap: 8px;
        }}
        .links a {{
            padding: 2px 6px;
            background: var(--bg-color);
            border-radius: 4px;
            font-size: 0.85em;
        }}
        .status-mapped {{
            color: var(--success);
        }}
        .status-unmapped {{
            color: var(--warning);
        }}
        .status-new {{
            color: #60a5fa;
        }}
        .rating {{
            font-weight: bold;
        }}
        .rating-high {{
            color: var(--success);
        }}
        .rating-mid {{
            color: var(--warning);
        }}
        .rating-low {{
            color: var(--accent);
        }}
        .hidden {{
            display: none;
        }}
        .generated {{
            color: var(--text-muted);
            font-size: 0.9em;
            margin-top: 20px;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>AniDB to Trakt Report</h1>

        <div class="stats">
            <div class="stat">
                <div class="stat-value">{total_count}</div>
                <div class="stat-label">Total Anime</div>
            </div>
            <div class="stat">
                <div class="stat-value">{mapped_count}</div>
                <div class="stat-label">Mapped</div>
            </div>
            <div class="stat">
                <div class="stat-value">{unmapped_count}</div>
                <div class="stat-label">Unmapped</div>
            </div>
            <div class="stat">
                <div class="stat-value">{with_ratings}</div>
                <div class="stat-label">With Ratings</div>
            </div>
            <div class="stat">
                <div class="stat-value">{conflicts_count}</div>
                <div class="stat-label">Conflicts</div>
            </div>
        </div>

        <div class="filters">
            <input type="text" id="search" placeholder="Search titles..." oninput="filterTable()">
            <label>
                <input type="checkbox" id="filter-mapped" checked onchange="filterTable()"> Mapped
            </label>
            <label>
                <input type="checkbox" id="filter-unmapped" checked onchange="filterTable()"> Unmapped
            </label>
            <label>
                <input type="checkbox" id="filter-conflicts" onchange="filterTable()"> Conflicts Only
            </label>
            <label>
                <input type="checkbox" id="filter-rated" onchange="filterTable()"> Rated Only
            </label>
        </div>

        <table id="anime-table">
            <thead>
                <tr>
                    <th onclick="sortTable(0)">Title</th>
                    <th onclick="sortTable(1)">Type</th>
                    <th onclick="sortTable(2)">AniDB Rating</th>
                    <th onclick="sortTable(3)">Trakt Rating</th>
                    <th onclick="sortTable(4)">Conflict</th>
                    <th onclick="sortTable(5)">Episodes</th>
                    <th>Links</th>
                    <th onclick="sortTable(7)">Status</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>

        <p class="generated">Generated on {generated_date}</p>
    </div>

    <script>
        let sortCol = -1;
        let sortAsc = true;

        function sortTable(col) {{
            const table = document.getElementById('anime-table');
            const tbody = table.tBodies[0];
            const rows = Array.from(tbody.rows);

            // Toggle direction if same column
            if (sortCol === col) {{
                sortAsc = !sortAsc;
            }} else {{
                sortCol = col;
                sortAsc = true;
            }}

            // Update header classes
            const headers = table.tHead.rows[0].cells;
            for (let h of headers) {{
                h.classList.remove('sorted-asc', 'sorted-desc');
            }}
            headers[col].classList.add(sortAsc ? 'sorted-asc' : 'sorted-desc');

            rows.sort((a, b) => {{
                let aVal = a.cells[col].getAttribute('data-sort') || a.cells[col].textContent;
                let bVal = b.cells[col].getAttribute('data-sort') || b.cells[col].textContent;

                // Try numeric sort
                const aNum = parseFloat(aVal);
                const bNum = parseFloat(bVal);
                if (!isNaN(aNum) && !isNaN(bNum)) {{
                    return sortAsc ? aNum - bNum : bNum - aNum;
                }}

                // String sort
                return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            }});

            rows.forEach(row => tbody.appendChild(row));
        }}

        function filterTable() {{
            const search = document.getElementById('search').value.toLowerCase();
            const showMapped = document.getElementById('filter-mapped').checked;
            const showUnmapped = document.getElementById('filter-unmapped').checked;
            const conflictsOnly = document.getElementById('filter-conflicts').checked;
            const ratedOnly = document.getElementById('filter-rated').checked;

            const rows = document.querySelectorAll('#anime-table tbody tr');

            rows.forEach(row => {{
                const title = row.cells[0].textContent.toLowerCase();
                const isMapped = row.getAttribute('data-mapped') === 'true';
                const hasConflict = row.getAttribute('data-conflict') === 'true';
                const hasRating = row.getAttribute('data-rated') === 'true';

                let show = true;

                // Search filter
                if (search && !title.includes(search)) show = false;

                // Mapped/Unmapped filter
                if (isMapped && !showMapped) show = false;
                if (!isMapped && !showUnmapped) show = false;

                // Conflicts filter
                if (conflictsOnly && !hasConflict) show = false;

                // Rated filter
                if (ratedOnly && !hasRating) show = false;

                row.classList.toggle('hidden', !show);
            }});
        }}
    </script>
</body>
</html>
"""


def _rating_class(score: int | None) -> str:
    """Get CSS class for rating display."""
    if score is None:
        return ""
    if score >= 8:
        return "rating-high"
    if score >= 6:
        return "rating-mid"
    return "rating-low"


def generate_html_report(
    anime_list: list[AnimeEntry],
    resolutions: list[ConflictResolution] | None = None,
    output_path: Path | None = None,
) -> str:
    """Generate HTML report.

    Args:
        anime_list: List of AnimeEntry objects.
        resolutions: Optional conflict resolutions.
        output_path: Path to write HTML file.

    Returns:
        HTML content string.
    """
    # Build resolution lookup
    resolution_map = {}
    if resolutions:
        for r in resolutions:
            resolution_map[r.anime.anidb_id] = r

    # Calculate stats
    total_count = len(anime_list)
    mapped_count = sum(1 for a in anime_list if a.is_mapped)
    unmapped_count = total_count - mapped_count
    with_ratings = sum(1 for a in anime_list if a.rating)
    conflicts_count = sum(1 for r in (resolutions or []) if r.has_rating_conflict)

    # Generate table rows
    rows = []
    for anime in sorted(anime_list, key=lambda a: a.display_title.lower()):
        resolution = resolution_map.get(anime.anidb_id)
        links = _generate_links(anime)
        status = _get_status_indicator(anime, resolution)

        # AniDB rating
        anidb_rating = "-"
        anidb_rating_date = ""
        if anime.rating:
            anidb_rating = str(anime.rating.score)
            anidb_rating_date = f" ({_format_date(anime.rating.rated_at)})"

        # Trakt rating (from resolution)
        trakt_rating = "-"
        trakt_rating_date = ""
        if resolution and resolution.trakt_entry and resolution.trakt_entry.rating:
            trakt_rating = str(resolution.trakt_entry.rating)
            trakt_rating_date = f" ({_format_date(resolution.trakt_entry.rated_at)})"

        # Conflict indicator
        conflict = "-"
        has_conflict = False
        if resolution and resolution.has_rating_conflict:
            conflict = resolution.conflict_indicator
            has_conflict = True
        elif resolution and resolution.is_new and anime.rating:
            conflict = "➕ New"

        # Episode count
        watched = anime.watched_count
        total = anime.total_episodes
        ep_display = f"{watched}/{total}" if total > 0 else str(watched)

        # Build links HTML
        links_html = []
        for name, url in links.items():
            links_html.append(f'<a href="{html.escape(url)}" target="_blank">{name}</a>')

        # Status class
        if "Unmapped" in status:
            status_class = "status-unmapped"
        elif "New" in status:
            status_class = "status-new"
        else:
            status_class = "status-mapped"

        row = f"""<tr data-mapped="{"true" if anime.is_mapped else "false"}"
                      data-conflict="{"true" if has_conflict else "false"}"
                      data-rated="{"true" if anime.rating else "false"}">
            <td>{html.escape(anime.display_title)}</td>
            <td>{anime.anime_type.name}</td>
            <td data-sort="{anime.rating.score if anime.rating else -1}"
                class="rating {_rating_class(anime.rating.score if anime.rating else None)}">
                {anidb_rating}{anidb_rating_date}
            </td>
            <td data-sort="{resolution.trakt_entry.rating if resolution and resolution.trakt_entry and resolution.trakt_entry.rating else -1}">
                {trakt_rating}{trakt_rating_date}
            </td>
            <td>{conflict}</td>
            <td data-sort="{watched}">{ep_display}</td>
            <td class="links">{" ".join(links_html)}</td>
            <td class="{status_class}">{status}</td>
        </tr>"""
        rows.append(row)

    # Generate HTML
    html_content = HTML_TEMPLATE.format(
        total_count=total_count,
        mapped_count=mapped_count,
        unmapped_count=unmapped_count,
        with_ratings=with_ratings,
        conflicts_count=conflicts_count,
        table_rows="\n".join(rows),
        generated_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"Wrote HTML report to {output_path}")

    return html_content


def generate_csv_report(
    anime_list: list[AnimeEntry],
    resolutions: list[ConflictResolution] | None = None,
    output_path: Path | None = None,
) -> list[dict]:
    """Generate CSV report.

    Args:
        anime_list: List of AnimeEntry objects.
        resolutions: Optional conflict resolutions.
        output_path: Path to write CSV file.

    Returns:
        List of row dictionaries.
    """
    # Build resolution lookup
    resolution_map = {}
    if resolutions:
        for r in resolutions:
            resolution_map[r.anime.anidb_id] = r

    rows = []
    for anime in sorted(anime_list, key=lambda a: a.display_title.lower()):
        resolution = resolution_map.get(anime.anidb_id)
        links = _generate_links(anime)
        status = _get_status_indicator(anime, resolution)

        row = {
            "Title": anime.display_title,
            "Title (Romaji)": anime.title,
            "Type": anime.anime_type.name,
            "AniDB ID": anime.anidb_id,
            "AniDB Rating": anime.rating.score if anime.rating else "",
            "AniDB Rated At": _format_date(anime.rating.rated_at) if anime.rating else "",
            "Trakt Rating": "",
            "Trakt Rated At": "",
            "Conflict": "",
            "Watched Episodes": anime.watched_count,
            "Total Episodes": anime.total_episodes,
            "TVDB ID": anime.mapped_ids.tvdb_id if anime.mapped_ids else "",
            "IMDB ID": anime.mapped_ids.imdb_id if anime.mapped_ids else "",
            "TMDB ID": "",
            "Status": status,
            "AniDB URL": links.get("anidb", ""),
            "TVDB URL": links.get("tvdb", ""),
            "Trakt Search URL": links.get("trakt", ""),
        }

        if anime.mapped_ids:
            if anime.mapped_ids.tmdb_movie_id:
                row["TMDB ID"] = anime.mapped_ids.tmdb_movie_id
            elif anime.mapped_ids.tmdb_show_id:
                row["TMDB ID"] = anime.mapped_ids.tmdb_show_id

        if resolution and resolution.trakt_entry:
            if resolution.trakt_entry.rating:
                row["Trakt Rating"] = resolution.trakt_entry.rating
                row["Trakt Rated At"] = _format_date(resolution.trakt_entry.rated_at)
            if resolution.has_rating_conflict:
                row["Conflict"] = "Keep AniDB" if resolution.keep_anidb_rating else "Keep Trakt"

        rows.append(row)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        logger.info(f"Wrote CSV report to {output_path}")

    return rows


def generate_unmapped_json(
    unmapped: list[AnimeEntry],
    output_path: Path | None = None,
) -> list[dict]:
    """Generate JSON file of unmapped anime for manual review.

    Args:
        unmapped: List of unmapped AnimeEntry objects.
        output_path: Path to write JSON file.

    Returns:
        List of unmapped anime data.
    """
    import json

    data = []
    for anime in sorted(unmapped, key=lambda a: a.display_title.lower()):
        data.append(
            {
                "anidb_id": anime.anidb_id,
                "title": anime.display_title,
                "title_romaji": anime.title,
                "type": anime.anime_type.name,
                "total_episodes": anime.total_episodes,
                "watched_episodes": anime.watched_count,
                "rating": anime.rating.score if anime.rating else None,
                "anidb_url": f"https://anidb.net/anime/{anime.anidb_id}",
            }
        )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Wrote unmapped anime to {output_path}")

    return data
