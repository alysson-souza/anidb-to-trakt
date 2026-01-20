"""CLI entry point for AniDB to Trakt converter."""

import argparse
import logging
import sys
from pathlib import Path

from .id_mapper import IDMapper
from .parser import AniDBParseError, AniDBParser
from .report import generate_csv_report, generate_html_report, generate_unmapped_json
from .trakt_client import TraktAuthError, TraktClient, interactive_auth
from .trakt_exporter import TraktExporter, load_checkpoint

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def cmd_parse(args: argparse.Namespace) -> int:
    """Parse AniDB export and generate output files."""
    input_path = Path(args.input)
    output_dir = Path(args.output)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info(f"Parsing AniDB export: {input_path}")

    # Parse the export file
    try:
        parser = AniDBParser(input_path)
        anime_list = parser.get_watched_anime(exclude_hentai=args.exclude_hentai)
    except AniDBParseError as e:
        logger.error(f"Failed to parse export: {e}")
        return 1

    logger.info(f"Found {len(anime_list)} watched anime")

    # Get export stats
    stats = parser.get_stats()
    logger.info(
        f"Stats: {stats['total_anime']} total, "
        f"{stats['with_ratings']} rated, "
        f"{stats['with_watched_episodes']} with watch history"
    )

    # Initialize ID mapper
    id_mapper = IDMapper()
    if args.update_mapping:
        logger.info("Forcing ID mapping database refresh...")
        id_mapper.download_database(force=True)

    # Map IDs
    mapped, unmapped = id_mapper.map_all(anime_list)
    logger.info(f"Mapped: {len(mapped)}, Unmapped: {len(unmapped)}")

    if args.dry_run:
        logger.info("[DRY RUN] Would generate output files")
        return 0

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize exporter (without Trakt client for parse-only mode)
    exporter = TraktExporter(id_mapper)

    # Export JSON files
    exporter.export_to_files(mapped, output_dir)

    # Generate reports
    generate_html_report(anime_list, output_path=output_dir / "report.html")
    generate_csv_report(anime_list, output_path=output_dir / "report.csv")

    # Generate unmapped report if there are unmapped anime
    if unmapped:
        generate_unmapped_json(unmapped, output_path=output_dir / "unmapped.json")
        logger.warning(f"{len(unmapped)} anime could not be mapped. See unmapped.json for details.")

    logger.info(f"Output written to: {output_dir}")
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    """Authenticate with Trakt."""
    try:
        client = TraktClient()
    except TraktAuthError as e:
        logger.error(str(e))
        return 1

    if client.is_authenticated:
        print("Already authenticated with Trakt.")
        try:
            profile = client.get_user_profile()
            print(f"Logged in as: {profile.get('username', 'Unknown')}")

            if args.revoke:
                print("\nRevoking access token...")
                client.revoke_token()
                print("Token revoked successfully.")
        except Exception as e:
            logger.error(f"Failed to get profile: {e}")
            if args.revoke:
                client.revoke_token()
        return 0

    # Run interactive auth
    if interactive_auth(client):
        return 0
    return 1


def cmd_sync(args: argparse.Namespace) -> int:
    """Sync data to Trakt."""
    input_path = Path(args.input)
    output_dir = Path(args.output) if args.output else Path("./output")

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check what to sync
    if not args.history and not args.ratings:
        logger.error("Specify --history and/or --ratings to sync")
        return 1

    # Initialize Trakt client
    try:
        client = TraktClient()
    except TraktAuthError as e:
        logger.error(str(e))
        return 1

    if not client.is_authenticated:
        logger.error("Not authenticated. Run 'auth' command first.")
        return 1

    logger.info(f"Parsing AniDB export: {input_path}")

    # Parse the export file
    try:
        parser = AniDBParser(input_path)
        anime_list = parser.get_watched_anime(exclude_hentai=args.exclude_hentai)
    except AniDBParseError as e:
        logger.error(f"Failed to parse export: {e}")
        return 1

    logger.info(f"Found {len(anime_list)} watched anime")

    # Initialize ID mapper
    id_mapper = IDMapper()
    if args.update_mapping:
        logger.info("Forcing ID mapping database refresh...")
        id_mapper.download_database(force=True)

    # Map IDs
    mapped, unmapped = id_mapper.map_all(anime_list)

    if not mapped:
        logger.warning("No anime could be mapped to Trakt IDs")
        return 0

    # Load checkpoint if resuming
    checkpoint = None
    checkpoint_path = output_dir / ".sync_checkpoint.json"
    if args.resume:
        checkpoint = load_checkpoint(checkpoint_path)
        if checkpoint:
            logger.info(f"Resuming from checkpoint (index {checkpoint.last_processed_index})")

    # Initialize exporter with Trakt client
    exporter = TraktExporter(id_mapper, client)

    # Ensure output dir exists for failed batches
    output_dir.mkdir(parents=True, exist_ok=True)

    # Perform sync
    logger.info("Starting sync to Trakt...")
    results = exporter.sync_to_trakt(
        mapped,
        sync_history=args.history,
        sync_ratings=args.ratings,
        dry_run=args.dry_run,
        checkpoint=checkpoint,
        output_dir=output_dir,
    )

    # Report results
    if args.ratings:
        logger.info(
            f"Ratings: {results['ratings_added']} added, "
            f"{results['ratings_existing']} existing/not found"
        )

    if args.history:
        logger.info(
            f"History: {results['history_added']} added, "
            f"{results['history_existing']} existing/not found"
        )

    if results.get("errors"):
        logger.warning(f"Errors encountered: {len(results['errors'])}")
        for error in results["errors"][:5]:  # Show first 5 errors
            logger.warning(f"  - {error}")

    if results.get("stopped_early"):
        logger.error(
            "Sync stopped early due to consecutive server errors. "
            "This may indicate rate limiting or temporary API issues. "
            "Failed batches saved to failed_batches.json for manual retry later."
        )

    if results.get("failed_batches"):
        logger.warning(
            f"{len(results['failed_batches'])} batches failed. "
            f"See {output_dir / 'failed_batches.json'} for details."
        )

    # Generate reports
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Resolve conflicts for report
        resolutions = exporter.resolve_conflicts(anime_list, fetch_existing=False)

        generate_html_report(
            anime_list, resolutions=resolutions, output_path=output_dir / "report.html"
        )
        generate_csv_report(
            anime_list, resolutions=resolutions, output_path=output_dir / "report.csv"
        )

        if unmapped:
            generate_unmapped_json(unmapped, output_path=output_dir / "unmapped.json")

        logger.info(f"Reports written to: {output_dir}")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Parse AniDB exports and sync to Trakt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Parse command
    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse AniDB export and generate JSON/reports",
    )
    parse_parser.add_argument(
        "input",
        help="Path to AniDB export XML file",
    )
    parse_parser.add_argument(
        "-o",
        "--output",
        default="./output",
        help="Output directory (default: ./output)",
    )
    parse_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate without writing files",
    )
    parse_parser.add_argument(
        "--update-mapping",
        action="store_true",
        help="Force refresh ID mapping database",
    )
    parse_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parse_parser.add_argument(
        "--exclude-hentai",
        action="store_true",
        help="Exclude restricted content",
    )

    # Auth command
    auth_parser = subparsers.add_parser(
        "auth",
        help="Authenticate with Trakt",
    )
    auth_parser.add_argument(
        "--revoke",
        action="store_true",
        help="Revoke existing authentication",
    )

    # Sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync data directly to Trakt",
    )
    sync_parser.add_argument(
        "input",
        help="Path to AniDB export XML file",
    )
    sync_parser.add_argument(
        "-o",
        "--output",
        default="./output",
        help="Output directory for reports (default: ./output)",
    )
    sync_parser.add_argument(
        "--history",
        action="store_true",
        help="Sync watch history",
    )
    sync_parser.add_argument(
        "--ratings",
        action="store_true",
        help="Sync ratings",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without syncing",
    )
    sync_parser.add_argument(
        "--update-mapping",
        action="store_true",
        help="Force refresh ID mapping database",
    )
    sync_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    sync_parser.add_argument(
        "--exclude-hentai",
        action="store_true",
        help="Exclude restricted content",
    )
    sync_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    try:
        if args.command == "parse":
            return cmd_parse(args)
        elif args.command == "auth":
            return cmd_auth(args)
        elif args.command == "sync":
            return cmd_sync(args)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting gracefully...")
        return 130  # Standard exit code for SIGINT

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(130)
