#!/usr/bin/env python3
"""データ処理状況確認スクリプト

data/ディレクトリ配下の処理状況を確認・表示する。

Usage:
    # 全体の状況を確認
    uv run check-data-status

    # 詳細情報を表示
    uv run check-data-status --verbose

    # JSON形式で出力
    uv run check-data-status --json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="データ処理状況確認スクリプト")

    parser.add_argument("--data-dir", type=str, default="data", help="dataディレクトリのパス(デフォルト: data)")

    parser.add_argument("-v", "--verbose", action="store_true", help="詳細情報を表示")

    parser.add_argument("--json", action="store_true", help="JSON形式で出力")

    args = parser.parse_args()

    # データディレクトリの確認
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"❌ データディレクトリが見つかりません: {data_dir}", file=sys.stderr)
        sys.exit(1)

    # 各ディレクトリの状況を確認
    status = check_status(data_dir, args.verbose)

    if args.json:
        # JSON形式で出力
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        # 人間可読形式で出力
        print_status(status, args.verbose)


def check_status(data_dir: Path, verbose: bool = False) -> dict[str, Any]:
    """データ処理状況をチェック

    Args:
        data_dir: dataディレクトリのパス
        verbose: 詳細情報を含めるか

    Returns:
        処理状況の辞書
    """
    status = {
        "raw": check_directory(data_dir / "raw", verbose),
        "processed": check_directory(data_dir / "processed", verbose),
        "backups": check_directory(data_dir / "backups", verbose),
        "logs": check_directory(data_dir / "logs", verbose),
    }

    # 処理カバー率を計算
    if status["raw"]["file_count"] > 0:
        processed_rate = (
            (status["processed"]["file_count"] / status["raw"]["file_count"]) * 100
            if status["processed"]["file_count"] > 0
            else 0.0
        )
        status["coverage"] = {"processed_rate": processed_rate}
    else:
        status["coverage"] = {"processed_rate": 0.0}

    return status


def check_directory(dir_path: Path, verbose: bool = False) -> dict[str, Any]:
    """ディレクトリの状況をチェック

    Args:
        dir_path: チェック対象ディレクトリ
        verbose: 詳細情報を含めるか

    Returns:
        ディレクトリ情報の辞書
    """
    if not dir_path.exists():
        return {"exists": False, "file_count": 0, "total_size_mb": 0, "files": []}

    # CSVファイルを集計
    csv_files = list(dir_path.rglob("*.csv"))
    total_size = sum(f.stat().st_size for f in csv_files)

    result: dict[str, Any] = {
        "exists": True,
        "file_count": len(csv_files),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
    }

    # 詳細情報
    if verbose:
        result["files"] = [
            {"name": f.name, "size_kb": round(f.stat().st_size / 1024, 2), "path": str(f.relative_to(dir_path))}
            for f in sorted(csv_files)
        ]

    return result


def print_status(status: dict[str, Any], verbose: bool = False) -> None:
    """処理状況を表示

    Args:
        status: 処理状況の辞書
        verbose: 詳細情報を表示するか
    """
    print("\n" + "=" * 70)
    print("📊 東京都感染症データ処理状況")
    print("=" * 70)

    # raw/
    print("\n📁 data/raw/ (生データ - Shift_JIS)")
    print_dir_status(status["raw"], verbose)

    # processed/
    print("\n📝 data/processed/ (処理済み - UTF-8正規化)")
    print_dir_status(status["processed"], verbose)

    # backups/
    print("\n💾 data/backups/ (バックアップ)")
    print_dir_status(status["backups"], verbose)

    # logs/
    print("\n📋 data/logs/ (ログ)")
    print_dir_status(status["logs"], verbose)

    # カバー率
    print("\n" + "=" * 70)
    print("📈 処理カバー率")
    print("=" * 70)
    print(f"処理済み率: {status['coverage']['processed_rate']:.1f}%")

    # 推奨アクション
    print("\n" + "=" * 70)
    print("💡 推奨アクション")
    print("=" * 70)

    if status["raw"]["file_count"] == 0:
        print("⚠️  data/raw/にデータがありません")
        print("   → データ取得スクリプトを実行してください")

    elif status["processed"]["file_count"] == 0:
        print("⚠️  データ処理が必要です")
        print("   → uv run process-data --all")

    elif status["processed"]["file_count"] < status["raw"]["file_count"]:
        print("⚠️  一部のファイルが処理されていません")
        print("   → uv run process-data --all")

    else:
        print("✅ すべての処理が完了しています")

    print()


def print_dir_status(dir_status: dict[str, Any], verbose: bool = False) -> None:
    """ディレクトリの状況を表示

    Args:
        dir_status: ディレクトリ情報の辞書
        verbose: 詳細情報を表示するか
    """
    if not dir_status["exists"]:
        print("  ❌ ディレクトリが存在しません")
        return

    print(f"  ファイル数: {dir_status['file_count']}")
    print(f"  合計サイズ: {dir_status['total_size_mb']} MB")

    if verbose and "files" in dir_status:
        print("  ファイル一覧:")
        for file_info in dir_status["files"][:10]:  # 最初の10件のみ表示
            print(f"    - {file_info['name']} ({file_info['size_kb']} KB)")

        if len(dir_status["files"]) > 10:
            print(f"    ... 他 {len(dir_status['files']) - 10}件")


if __name__ == "__main__":
    main()
