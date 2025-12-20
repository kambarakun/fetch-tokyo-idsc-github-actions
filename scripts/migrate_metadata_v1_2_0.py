#!/usr/bin/env python3
"""Migrate metadata from v1.1.0 to v1.2.0.

This script adds the quality field to existing metadata files.
v1.2.0 introduces data quality tracking with validation results.

Usage:
    python scripts/migrate_metadata_v1_2_0.py [--dry-run] [--no-backup]
"""

import argparse
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from src.utils.version import parse_version

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def migrate_metadata_file(metadata_path: Path, dry_run: bool = False, backup: bool = True) -> bool:
    """Migrate a single metadata file from v1.1.0 to v1.2.0.

    Args:
        metadata_path: Path to metadata JSON file
        dry_run: If True, only simulate the migration
        backup: If True, create a backup before modifying

    Returns:
        True if migration was successful or already at v1.2.0
    """
    try:
        # Read existing metadata
        with metadata_path.open("r", encoding="utf-8") as f:
            metadata = json.load(f)

        current_version = metadata.get("metadata_version", "unknown")

        # Skip if already v1.2.0 or higher
        # Use tuple comparison to avoid issues like "1.10.0" >= "1.2.0" returning False
        try:
            if parse_version(current_version) >= parse_version("1.2.0"):
                logger.debug(f"Skipping {metadata_path.name}: already at {current_version}")
                return True
        except ValueError:
            # If version cannot be parsed, it's not a valid semantic version
            logger.warning(f"Invalid version format {current_version} in {metadata_path.name}, skipping")
            return False

        # Only migrate from v1.1.0
        if current_version != "1.1.0":
            logger.warning(f"Unexpected version {current_version} in {metadata_path.name}, skipping")
            return False

        # Add quality field (v1.2.0 addition)
        # For existing data, we set validation_status to "skipped" since no validation was performed
        validation_timestamp = datetime.now(UTC).isoformat()
        metadata["quality"] = {
            "validation_timestamp": validation_timestamp,
            "validation_status": "skipped",
            "issues": [],
        }

        # Update metadata version
        metadata["metadata_version"] = "1.2.0"

        if dry_run:
            logger.info(f"[DRY RUN] Would migrate {metadata_path.name}")
            return True

        # Create backup
        if backup:
            backup_path = metadata_path.with_suffix(".json.v1.1.0.bak")
            shutil.copy2(metadata_path, backup_path)
            logger.debug(f"Created backup: {backup_path.name}")

        # Write migrated metadata
        with metadata_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        logger.info(f"Migrated: {metadata_path.name}")
        return True

    except (OSError, json.JSONDecodeError):
        logger.exception(f"Failed to migrate {metadata_path.name}")
        return False


def migrate_all(metadata_dir: Path, dry_run: bool = False, backup: bool = True) -> dict:
    """Migrate all metadata files in a directory.

    Args:
        metadata_dir: Path to .metadata directory
        dry_run: If True, only simulate the migration
        backup: If True, create backups before modifying

    Returns:
        Migration statistics dict
    """
    if not metadata_dir.exists():
        logger.error(f"Metadata directory not found: {metadata_dir}")
        return {"total": 0, "migrated": 0, "skipped": 0, "failed": 0}

    metadata_files = list(metadata_dir.glob("*.json"))
    total = len(metadata_files)

    logger.info(f"Found {total} metadata files in {metadata_dir}")

    migrated = 0
    skipped = 0
    failed = 0

    for metadata_file in metadata_files:
        try:
            # Read to check version
            with metadata_file.open("r", encoding="utf-8") as f:
                metadata = json.load(f)
            version = metadata.get("metadata_version", "unknown")

            # Use tuple comparison to avoid issues like "1.10.0" >= "1.2.0" returning False
            try:
                if parse_version(version) >= parse_version("1.2.0"):
                    skipped += 1
                    continue
            except ValueError:
                # Invalid version format will be handled by migrate_metadata_file
                pass

            if migrate_metadata_file(metadata_file, dry_run=dry_run, backup=backup):
                migrated += 1
            else:
                failed += 1

        except (OSError, json.JSONDecodeError):
            logger.exception(f"Error processing {metadata_file.name}")
            failed += 1

    logger.info(f"Migration complete: {migrated} migrated, {skipped} skipped, {failed} failed")

    return {
        "total": total,
        "migrated": migrated,
        "skipped": skipped,
        "failed": failed,
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate metadata from v1.1.0 to v1.2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Migrate all metadata files (with backups)
  python scripts/migrate_metadata_v1_2_0.py

  # Dry run (simulate without making changes)
  python scripts/migrate_metadata_v1_2_0.py --dry-run

  # Migrate without creating backups
  python scripts/migrate_metadata_v1_2_0.py --no-backup
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without making changes",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create backup files",
    )
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=Path("data/processed/.metadata"),
        help="Path to metadata directory (default: data/processed/.metadata)",
    )

    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN MODE ===")

    logger.info("Migrating metadata from v1.1.0 to v1.2.0")
    logger.info(f"Target directory: {args.metadata_dir}")
    logger.info(f"Backup: {'disabled' if args.no_backup else 'enabled'}")

    stats = migrate_all(
        args.metadata_dir,
        dry_run=args.dry_run,
        backup=not args.no_backup,
    )

    # Summary
    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"Total files:    {stats['total']}")
    print(f"Migrated:       {stats['migrated']}")
    print(f"Skipped:        {stats['skipped']}")
    print(f"Failed:         {stats['failed']}")
    print("=" * 60)

    if args.dry_run:
        print("\nThis was a DRY RUN. No files were modified.")
        print("Run without --dry-run to perform actual migration.")

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
