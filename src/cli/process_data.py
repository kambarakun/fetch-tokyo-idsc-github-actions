#!/usr/bin/env python3
"""データ処理統合スクリプト

UTF-8変換 + 正規化を一度に実行するメインスクリプト。

Usage:
    # 全ファイルを処理
    uv run process-data --all

    # 特定ファイルを処理(1個)
    uv run process-data --files data/raw/sentinel_weekly_gender_2025_01.csv

    # 複数ファイルを処理
    uv run process-data --files file1.csv file2.csv file3.csv

    # ドライラン
    uv run process-data --all --dry-run

    # 詳細ログ
    uv run process-data --all --verbose
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from src.processors.data_processor import DataProcessor, NormalizationResult

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def save_stats(data_dir: Path, stats_data: dict[str, Any]) -> None:
    """処理統計をstats.jsonに保存

    Args:
        data_dir: データディレクトリ
        stats_data: 統計データ
    """
    stats_file = data_dir / "processed" / "stats.json"
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with stats_file.open("w", encoding="utf-8") as f:
            json.dump(stats_data, f, ensure_ascii=False, indent=2)
        logger.info(f"📊 処理統計を保存: {stats_file}")
    except OSError:
        logger.exception(f"処理統計の保存に失敗しました: {stats_file}")
        logger.warning("処理統計の保存に失敗しましたが、データ処理自体は完了しています")


def main() -> None:
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description="東京都感染症データの処理スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 全ファイルを処理
  %(prog)s --all

  # 特定ファイルを処理(1個)
  %(prog)s --files data/raw/sentinel_weekly_gender_2025_01.csv

  # 複数ファイルを処理
  %(prog)s --files file1.csv file2.csv file3.csv

  # ドライラン
  %(prog)s --all --dry-run
        """,
    )

    # 基本オプション
    parser.add_argument("--data-dir", type=str, default="data", help="dataディレクトリのパス(デフォルト: data)")

    parser.add_argument("--dry-run", action="store_true", help="ドライランモード(実際の処理は行わない)")

    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログを出力")

    # 処理モード選択
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--all", action="store_true", help="全ファイルを処理")
    mode_group.add_argument("--files", type=str, nargs="+", help="指定したファイルを処理(1個以上、スペース区切り)")

    args = parser.parse_args()

    # ログレベル設定
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)

    # データディレクトリの確認
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        logger.error(f"データディレクトリが見つかりません: {data_dir}")
        sys.exit(1)

    # ドライラン表示
    if args.dry_run:
        logger.info("🔍 ドライランモード(実際の処理は行いません)")
        # ドライランの場合はここで終了
        logger.info("ドライラン完了")
        return

    # DataProcessor初期化
    processor = DataProcessor(data_dir)

    try:
        if args.all:
            # 全ファイルを処理
            logger.info("=" * 60)
            logger.info("📊 全ファイル処理を開始します")
            logger.info("=" * 60)

            result = processor.process_all()
            print_result("処理", result)

            # 処理結果をstats.jsonに保存
            save_stats(data_dir, result)

            # 最終結果サマリー
            logger.info("\n" + "=" * 60)
            logger.info("✅ 処理完了")
            logger.info("=" * 60)
            logger.info(f"処理結果: {result['succeeded']}/{result['total']} 成功")

            # 失敗があった場合はエラー終了
            if result["failed"] > 0:
                logger.error(f"❌ {result['failed']}件の処理が失敗しました")
                sys.exit(1)

        elif args.files:
            # 指定されたファイルを処理(1個以上)
            file_paths = [Path(f) for f in args.files]

            # 存在しないファイルをチェック
            missing_files = [fp for fp in file_paths if not fp.exists()]
            if missing_files:
                logger.error("以下のファイルが見つかりません:")
                for missing_file in missing_files:
                    logger.error(f"  - {missing_file}")
                sys.exit(1)

            logger.info("=" * 60)
            logger.info(f"📄 ファイル処理: {len(file_paths)}ファイル")
            logger.info("=" * 60)

            succeeded = 0
            failed = 0
            skipped = 0
            errors = []
            raw_dir = data_dir / "raw"

            for file_path in file_paths:
                # data/raw/配下のファイルのみ処理(堅牢なチェック)
                try:
                    file_path.resolve().relative_to(raw_dir.resolve())
                except ValueError:
                    logger.warning(f"⚠️ スキップ: {raw_dir}/配下ではありません - {file_path}")
                    skipped += 1
                    continue

                logger.info(f"処理中: {file_path.name}")
                file_result: NormalizationResult = processor.process_file(file_path)

                if file_result.success:
                    succeeded += 1
                    logger.info(f"  ✅ 成功: {len(file_result.output_files)}ファイル生成")
                else:
                    failed += 1
                    errors.append({"file": file_path.name, "error": file_result.error})
                    logger.error(f"  ❌ 失敗: {file_result.error}")

            # 処理対象ファイル数(スキップを除く)
            total = len(file_paths) - skipped

            # 処理結果をstats.jsonに保存
            stats_data = {
                "total": total,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "errors": errors,
            }
            save_stats(data_dir, stats_data)

            # 最終結果サマリー
            logger.info("\n" + "=" * 60)
            logger.info("✅ 処理完了")
            logger.info("=" * 60)
            logger.info(f"処理結果: {succeeded}/{total} 成功")
            if skipped > 0:
                logger.info(f"スキップ: {skipped}ファイル")

            # 失敗があった場合はエラー終了
            if failed > 0:
                logger.error(f"❌ {failed}件の処理が失敗しました")
                sys.exit(1)

    except KeyboardInterrupt:
        logger.warning("\n処理が中断されました")
        sys.exit(1)
    except Exception:
        logger.exception("予期しないエラーが発生しました")
        sys.exit(1)


def print_result(operation: str, result: dict):
    """処理結果を表示

    Args:
        operation: 処理名
        result: 処理結果辞書
    """
    logger.info(f"\n{operation}結果:")
    logger.info(f"  総ファイル数: {result['total']}")
    logger.info(f"  ✅ 成功: {result['succeeded']}")
    logger.info(f"  ❌ 失敗: {result['failed']}")

    if result["errors"]:
        logger.warning(f"\nエラー詳細 ({len(result['errors'])}件):")
        for error in result["errors"][:5]:  # 最初の5件のみ表示
            logger.warning(f"  - {error['file']}: {error['error']}")

        if len(result["errors"]) > 5:
            logger.warning(f"  ... 他 {len(result['errors']) - 5}件")


if __name__ == "__main__":
    main()
