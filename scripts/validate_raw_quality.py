#!/usr/bin/env python3
"""
既存のrawメタデータに対して品質検証を一括実行するスクリプト

使用方法:
    uv run python scripts/validate_raw_quality.py [--dry-run] [--limit N]
"""

import argparse
import json
import logging
from pathlib import Path

from src.validators.quality_validator import QualityValidator

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="既存のrawメタデータに品質検証を追加")
    parser.add_argument("--dry-run", action="store_true", help="実際には更新せず、結果のみ表示")
    parser.add_argument("--limit", type=int, help="処理するファイル数の上限")
    args = parser.parse_args()

    raw_data_dir = Path("data/raw")
    metadata_dir = raw_data_dir / ".metadata"

    if not metadata_dir.exists():
        logger.error(f"メタデータディレクトリが見つかりません: {metadata_dir}")
        return 1

    # QualityValidatorを初期化
    quality_validator = QualityValidator(raw_data_dir)

    # 統計情報
    total = 0
    updated = 0
    skipped = 0
    has_issues = 0

    # 全メタデータファイルを処理
    metadata_files = list(metadata_dir.glob("*.json"))
    if args.limit:
        metadata_files = metadata_files[: args.limit]

    logger.info(f"📊 品質検証の一括実行: {len(metadata_files)}ファイル")
    logger.info("")

    for json_file in metadata_files:
        if json_file.name == "hash_index.json":
            continue

        total += 1

        try:
            # メタデータを読み込み
            with json_file.open(encoding="utf-8") as f:
                metadata = json.load(f)

            filename = metadata.get("filename")
            data_type = metadata.get("data_type")

            if not filename or not data_type:
                logger.warning(f"⚠️  {json_file.name}: filename or data_type missing")
                skipped += 1
                continue

            # 品質検証を実行
            quality = quality_validator.validate(filename, data_type, {})

            # メタデータを更新
            metadata["quality"] = quality

            # 統計
            if quality["issues"]:
                has_issues += 1
                logger.info(f"🔍 {filename}: {len(quality['issues'])}件の問題を検出")

            if not args.dry_run:
                # メタデータを保存
                with json_file.open("w", encoding="utf-8") as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)

            updated += 1

            # 進捗表示
            if total % 1000 == 0:
                logger.info(f"進捗: {total}/{len(metadata_files)}ファイル処理完了")

        except Exception as e:
            logger.error(f"❌ {json_file.name}: {e}")
            skipped += 1

    logger.info("")
    logger.info("✅ 品質検証完了")
    logger.info(f"   総ファイル数: {total}")
    logger.info(f"   更新: {updated}")
    logger.info(f"   スキップ: {skipped}")
    logger.info(f"   問題検出: {has_issues}")

    if args.dry_run:
        logger.info("")
        logger.info("⚠️  --dry-run モード: メタデータは更新されていません")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
