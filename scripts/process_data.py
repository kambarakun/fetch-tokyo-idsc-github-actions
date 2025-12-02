#!/usr/bin/env python3
"""データ処理統合スクリプト

UTF-8変換 + 正規化を一度に実行するメインスクリプト。

Usage:
    # 全ファイルを処理
    uv run python scripts/process_data.py --all

    # 特定ファイルのみ処理
    uv run python scripts/process_data.py --file data/raw/sentinel_weekly_gender_2025_01.csv

    # ドライラン
    uv run python scripts/process_data.py --all --dry-run

    # 詳細ログ
    uv run python scripts/process_data.py --all --verbose
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.processors.data_processor import DataProcessor

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def main():  # noqa: PLR0912, PLR0915
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description="東京都感染症データの処理スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 全ファイルを処理
  %(prog)s --all

  # 特定ファイルのみ処理
  %(prog)s --file data/raw/sentinel_weekly_gender_2025_01.csv

  # ドライラン
  %(prog)s --all --dry-run
        """,
    )

    # 基本オプション
    parser.add_argument("--data-dir", type=str, default="data", help="dataディレクトリのパス（デフォルト: data）")

    parser.add_argument("--dry-run", action="store_true", help="ドライランモード（実際の処理は行わない）")

    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログを出力")

    # 処理モード選択
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--all", action="store_true", help="全ファイルを処理")
    mode_group.add_argument("--file", type=str, help="特定ファイルのみ処理")

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
        logger.info("🔍 ドライランモード（実際の処理は行いません）")
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
            stats_file = data_dir / "processed" / "stats.json"
            stats_file.parent.mkdir(parents=True, exist_ok=True)
            with stats_file.open("w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"📊 処理統計を保存: {stats_file}")

            # 最終結果サマリー
            logger.info("\n" + "=" * 60)
            logger.info("✅ 処理完了")
            logger.info("=" * 60)
            logger.info(f"処理結果: {result['succeeded']}/{result['total']} 成功")

            # 失敗があった場合はエラー終了
            if result["failed"] > 0:
                logger.error(f"❌ {result['failed']}件の処理が失敗しました")
                sys.exit(1)

        elif args.file:
            # 特定ファイルのみ処理
            file_path = Path(args.file)
            if not file_path.exists():
                logger.error(f"ファイルが見つかりません: {file_path}")
                sys.exit(1)

            logger.info("=" * 60)
            logger.info(f"📄 ファイル処理: {file_path.name}")
            logger.info("=" * 60)

            # ファイルがraw/にある場合のみ処理
            if file_path.parent.name == "raw":
                result = processor.process_file(file_path)
                if result.success:
                    logger.info(f"✅ 処理成功: {len(result.output_files)}ファイル生成")
                    for output_file in result.output_files:
                        logger.info(f"  - {output_file.name}")
                else:
                    logger.error(f"❌ 処理失敗: {result.error}")
            else:
                logger.error("ファイルはdata/raw/配下である必要があります")
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
