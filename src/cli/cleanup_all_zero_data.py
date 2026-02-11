#!/usr/bin/env python3
"""
全て0のデータファイルを削除するクリーンアップスクリプト

未発表の週や月のデータ(全てのカウントが0)を検出して削除します。
対応するメタデータファイルも同時に削除されます。
"""

import argparse
import logging
import sys
from pathlib import Path

from src.managers.storage_manager import StorageManager


def setup_logging(verbose: bool = False) -> logging.Logger:
    """ロギングのセットアップ"""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )
    return logging.getLogger(__name__)


def find_all_zero_files(storage: StorageManager, base_path: Path, logger: logging.Logger) -> list[Path]:
    """全て0のデータファイルを検出

    Args:
        storage: ストレージマネージャー
        base_path: データディレクトリのパス
        logger: ロガー

    Returns:
        全て0のファイルパスのリスト
    """
    csv_files = list(base_path.glob("*.csv"))
    all_zero_files = []

    logger.info(f"CSVファイルをスキャン中... 総数: {len(csv_files)}")

    for csv_file in csv_files:
        try:
            data = csv_file.read_bytes()
            if storage._is_all_zero_data(data):
                all_zero_files.append(csv_file)
                logger.debug(f"全て0のファイル検出: {csv_file.name}")
        except Exception as e:
            logger.warning(f"ファイル読み込みエラー {csv_file.name}: {e}")
            continue

    return all_zero_files


def delete_files(files: list[Path], storage: StorageManager, logger: logging.Logger, dry_run: bool = False) -> int:
    """ファイルとメタデータを削除

    Args:
        files: 削除するファイルのリスト
        storage: ストレージマネージャー
        logger: ロガー
        dry_run: Trueの場合、実際には削除しない

    Returns:
        削除されたファイル数
    """
    deleted_count = 0

    for file_path in files:
        try:
            # メタデータパスを取得
            metadata_path = storage.metadata_dir / f"{file_path.stem}.json"

            if dry_run:
                logger.info(f"[DRY RUN] 削除対象: {file_path.name}")
                if metadata_path.exists():
                    logger.info(f"[DRY RUN] メタデータ削除対象: {metadata_path.name}")
            else:
                # CSVファイルを削除
                file_path.unlink()
                logger.info(f"削除: {file_path.name}")

                # メタデータファイルを削除
                if metadata_path.exists():
                    metadata_path.unlink()
                    logger.debug(f"メタデータ削除: {metadata_path.name}")

            deleted_count += 1

        except Exception as e:
            logger.error(f"削除エラー {file_path.name}: {e}")
            continue

    return deleted_count


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="全て0のデータファイルを削除するクリーンアップスクリプト")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/raw"),
        help="データディレクトリのパス(デフォルト: data/raw)",
    )
    parser.add_argument("--dry-run", action="store_true", help="削除せずに対象ファイルのみ表示")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログを出力")
    parser.add_argument("-y", "--yes", action="store_true", help="確認なしで削除実行")

    args = parser.parse_args()
    logger = setup_logging(args.verbose)

    # データディレクトリの存在確認
    if not args.data_dir.exists():
        logger.error(f"データディレクトリが存在しません: {args.data_dir}")
        return 1

    # ストレージマネージャー初期化
    config = {"auto_commit": False, "keep_shift_jis": True}
    storage = StorageManager(args.data_dir, config)

    # 全て0のファイルを検出
    logger.info("=" * 60)
    logger.info("全て0のデータファイルをスキャン中...")
    logger.info("=" * 60)

    all_zero_files = find_all_zero_files(storage, args.data_dir, logger)

    if not all_zero_files:
        logger.info("全て0のデータファイルは見つかりませんでした。")
        return 0

    # 結果を表示
    logger.info("")
    logger.info(f"全て0のファイルが {len(all_zero_files)} 件見つかりました:")
    logger.info("")

    for i, file_path in enumerate(all_zero_files, 1):
        logger.info(f"  {i:3d}. {file_path.name}")

    logger.info("")
    logger.info("=" * 60)

    # dry-runモードの場合はここで終了
    if args.dry_run:
        logger.info("[DRY RUN] 実際の削除は行いませんでした。")
        logger.info("実際に削除するには、--dry-run フラグを外して実行してください。")
        return 0

    # 確認プロンプト
    if not args.yes:
        logger.warning(f"\n{len(all_zero_files)} 件のファイル(とメタデータ)を削除しますか？")
        response = input("削除を実行しますか？ (yes/no): ").strip().lower()
        if response not in ["yes", "y"]:
            logger.info("削除をキャンセルしました。")
            return 0

    # 削除実行
    logger.info("")
    logger.info("削除を実行中...")
    deleted_count = delete_files(all_zero_files, storage, logger, dry_run=False)

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"削除完了: {deleted_count} 件のファイルを削除しました。")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
