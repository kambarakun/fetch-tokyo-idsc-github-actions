#!/usr/bin/env python3
"""メタデータの検証フィールドを更新するスクリプト.

既存のCSVファイルを読み込み、データ品質検証を実行して
メタデータの verification フィールドを更新する。

Usage:
    # ドライラン (変更内容を表示するだけ)
    uv run verify-metadata --dry-run

    # 実行
    uv run verify-metadata

    # 詳細出力
    uv run verify-metadata --verbose

    # verification が None のファイルのみ対象
    uv run verify-metadata --only-unverified

    # JSON形式で統計を出力 (GitHub Actions連携用)
    # --verbose と併用可能 (ログはstderr、JSONはstdout)
    uv run verify-metadata --output-json --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

from src.managers.storage_manager import StorageManager
from src.validators.quality_validator import QualityValidator

logger = logging.getLogger(__name__)


class VerificationStats(TypedDict):
    """検証結果の統計情報."""

    total: int
    verified: int
    failed: int
    skipped: int
    errors: int


def _process_single_file(
    metadata_path: Path,
    data_dir: Path,
    storage_manager: StorageManager,
    dry_run: bool,
    verbose: bool,
    only_unverified: bool,
    quality_validator: QualityValidator | None = None,
) -> tuple[str, str | None]:
    """単一のメタデータファイルを処理する.

    Args:
        metadata_path: メタデータファイルのパス
        data_dir: データファイルディレクトリのパス
        storage_manager: StorageManagerインスタンス
        dry_run: Trueの場合、変更を保存しない
        verbose: Trueの場合、詳細なログを出力
        only_unverified: Trueの場合、verification が None のファイルのみ対象
        quality_validator: QualityValidatorインスタンス

    Returns:
        (結果タイプ, 検証ステータス)
        結果タイプ: "verified", "failed", "skipped", "error"
        検証ステータス: "verified", "failed", または None (エラー/スキップ時)
    """
    with metadata_path.open(encoding="utf-8") as f:
        metadata = json.load(f)

    # only_unverified モードの場合、既に検証済みならスキップ
    # ただし既存の検証ステータスは返す (統計や再集計に使用可能)
    if only_unverified and metadata.get("verification") is not None:
        existing_status = metadata["verification"].get("status")
        if verbose:
            logger.debug(f"Skipped (already {existing_status}): {metadata_path.name}")
        return "skipped", existing_status

    # 対応するCSVファイルのパス (パストラバーサル対策)
    csv_filename = metadata.get("filename", metadata_path.stem + ".csv")
    # ファイル名のみを抽出してパストラバーサルを防止
    csv_filename = Path(csv_filename).name
    data_file = data_dir / csv_filename

    if not data_file.exists():
        logger.warning(f"CSV file not found: {data_file}")
        return "error", None

    # CSVファイルを読み込み
    data = data_file.read_bytes()

    # 検証を実行 (公開API validate_file を使用)
    verification: dict[str, Any] = storage_manager.validate_file(data_file, data)

    # quality は verification 配下ではなく metadata 直下 (top-level) へ格納する。
    # (Metadata モデル / スキーマ / storage_manager・data_processor 経路と構造を統一)
    metadata_quality: dict[str, Any] | None = None

    if quality_validator is not None:
        data_type = metadata.get("data_type")
        if not isinstance(data_type, str):
            data_type = ""

        try:
            quality = quality_validator.validate(csv_filename, data_type, {})
        except (OSError, ValueError) as e:
            verification.setdefault("checks", {})["gender_sum_consistency"] = False
            verification.setdefault("errors", []).append(f"[gender_sum_consistency] Validation failed: {e!s}")
            verification["status"] = "failed"
        else:
            metadata_quality = quality

            issues = quality.get("issues", [])
            has_gender_sum_errors = False
            if isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    if issue.get("check_type") != "gender_sum_consistency":
                        continue

                    details = issue.get("details")
                    affected_count = details.get("affected_count", 0) if isinstance(details, dict) else 0
                    issue_status = issue.get("validation_status")
                    message = issue.get("message", "Validation failed")
                    if not isinstance(message, str):
                        message = str(message)

                    if issue_status == "failed" or (issue_status == "completed" and affected_count > 0):
                        has_gender_sum_errors = True
                        verification.setdefault("errors", []).append(f"[gender_sum_consistency] {message}")

            verification.setdefault("checks", {})["gender_sum_consistency"] = not has_gender_sum_errors
            if has_gender_sum_errors:
                verification["status"] = "failed"

    status: str = verification["status"]

    if dry_run:
        logger.info(f"[DRY-RUN] Would verify: {metadata_path.name} -> {status}")
        if verbose:
            for check, passed in verification["checks"].items():
                logger.info(f"  - {check}: {'PASS' if passed else 'FAIL'}")
            for error in verification.get("errors", []):
                logger.info(f"  - ERROR: {error}")
            for warning in verification.get("warnings", []):
                logger.info(f"  - WARNING: {warning}")
    else:
        # メタデータを更新
        metadata["verification"] = verification
        if metadata_quality is not None:
            metadata["quality"] = metadata_quality
        with metadata_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        if verbose:
            logger.info(f"Verified: {metadata_path.name} -> {status}")

    # 検証結果に基づいて結果タイプを返す
    if status == "verified":
        return "verified", status
    return "failed", status


def run_verification(
    metadata_dir: Path,
    data_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
    only_unverified: bool = False,
) -> VerificationStats:
    """メタデータの検証を一括実行する.

    Args:
        metadata_dir: メタデータディレクトリのパス
        data_dir: データファイルディレクトリのパス
        dry_run: Trueの場合、変更を保存しない
        verbose: Trueの場合、詳細なログを出力
        only_unverified: Trueの場合、verification が None のファイルのみ対象

    Returns:
        検証結果の統計情報
    """
    stats: VerificationStats = {
        "total": 0,
        "verified": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }

    # StorageManager のインスタンスを作成 (検証ロジックを再利用)
    config: dict[str, Any] = {"auto_commit": False}
    storage_manager = StorageManager(base_path=data_dir, config=config)
    quality_validator = QualityValidator(data_dir)

    metadata_files = sorted(metadata_dir.glob("*.json"))
    # hash_index.json は除外
    metadata_files = [f for f in metadata_files if f.name != "hash_index.json"]

    stats["total"] = len(metadata_files)
    logger.info(f"Found {stats['total']} metadata files to verify")

    for metadata_path in metadata_files:
        try:
            # status: 検証ステータス ("verified", "failed", None)
            # - 新規検証時: 検証結果のステータス
            # - スキップ時: 既存の検証ステータス (将来の統計拡張用に保持)
            result_type, status = _process_single_file(
                metadata_path=metadata_path,
                data_dir=data_dir,
                storage_manager=storage_manager,
                quality_validator=quality_validator,
                dry_run=dry_run,
                verbose=verbose,
                only_unverified=only_unverified,
            )
            # 型安全なカウント更新 (TypedDictのリテラルキー制約に対応)
            if result_type == "verified":
                stats["verified"] += 1
            elif result_type == "failed":
                stats["failed"] += 1
            elif result_type == "skipped":
                stats["skipped"] += 1
                # 注: status には既存の検証ステータスが入っている
                # 将来的に skipped_verified/skipped_failed の統計が必要な場合に使用
                _ = status  # 現在は未使用だが意図的に保持
            else:
                stats["errors"] += 1

        except (json.JSONDecodeError, OSError, KeyError):
            stats["errors"] += 1
            logger.exception(f"Error processing {metadata_path.name}")

    return stats


def main() -> int:
    """メイン関数."""
    parser = argparse.ArgumentParser(description="メタデータの検証フィールドを更新する")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="変更を保存せずに、検証結果を表示する",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="詳細なログを出力する",
    )
    parser.add_argument(
        "--only-unverified",
        action="store_true",
        help="verification が None のファイルのみ対象にする",
    )
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=Path("data/raw/.metadata"),
        help="メタデータディレクトリのパス (default: data/raw/.metadata)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/raw"),
        help="データファイルディレクトリのパス (default: data/raw)",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="JSON形式で統計を出力する (GitHub Actions連携用)",
    )
    args = parser.parse_args()

    # ログ設定 (stderrに出力、--output-jsonと併用時もJSONを汚染しない)
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    if not args.metadata_dir.exists():
        logger.error(f"Metadata directory not found: {args.metadata_dir}")
        return 1

    if not args.data_dir.exists():
        logger.error(f"Data directory not found: {args.data_dir}")
        return 1

    if args.dry_run:
        logger.info("=== DRY RUN MODE ===")

    stats = run_verification(
        metadata_dir=args.metadata_dir,
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
        only_unverified=args.only_unverified,
    )

    # 結果サマリー
    logger.info("=== Verification Summary ===")
    logger.info(f"Total files:    {stats['total']}")
    logger.info(f"Verified:       {stats['verified']}")
    logger.info(f"Failed:         {stats['failed']}")
    logger.info(f"Skipped:        {stats['skipped']}")
    logger.info(f"Errors:         {stats['errors']}")

    # JSON出力 (GitHub Actions連携用)
    # 標準出力にJSONを出力し、ワークフローでパース可能にする
    if args.output_json:
        print(json.dumps(stats))

    if args.dry_run and (stats["verified"] + stats["failed"]) > 0:
        logger.info("\nTo apply changes, run without --dry-run")

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
