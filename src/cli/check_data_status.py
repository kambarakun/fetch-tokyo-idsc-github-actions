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

from src.cli.check_missing import FILENAME_PATTERN
from src.processors.data_processor import GENDER_SUFFIX_BY_LABEL, detect_gender_sections

# Output expectations follow the processor's actual path for each data type.
DATA_TYPE_OUTPUT_KINDS = {
    "notifiable_weekly": "single",
    "sentinel_weekly_gender": "gender_sections",
    "sentinel_weekly_age": "gender_sections",
    "sentinel_weekly_health_center": "gender_sections",
    "sentinel_weekly_medical_district": "medical_district_sections",
    "sentinel_monthly_gender": "gender_sections",
    "sentinel_monthly_age": "gender_sections",
    "sentinel_monthly_health_center": "gender_sections",
    "sentinel_monthly_medical_district": "medical_district_sections",
}


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
    try:
        status = check_status(data_dir, args.verbose)
    except OSError as exc:
        print(f"❌ データディレクトリの読み取りに失敗しました: {exc}", file=sys.stderr)
        sys.exit(1)

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

    status["coverage"] = check_processing_coverage(data_dir / "raw", data_dir / "processed")

    return status


def expected_processed_outputs(raw_file: Path | str) -> list[str] | None:
    """Return the normalized artifacts the processor can emit for one raw source."""
    raw_path = Path(raw_file)
    match = FILENAME_PATTERN.fullmatch(raw_path.name)
    if match is None:
        return None

    data_type = match.group("data_type")
    output_kind = DATA_TYPE_OUTPUT_KINDS.get(data_type)
    if output_kind is None:
        return None

    suffixes: list[str | None]
    if output_kind == "single":
        suffixes = [None]
    else:
        lines = raw_path.read_text(encoding="shift_jis", errors="replace").splitlines()
        gender_sections = detect_gender_sections(lines)
        if not gender_sections:
            suffixes = [None]
        else:
            suffixes = list(dict.fromkeys(GENDER_SUFFIX_BY_LABEL[section["gender"]] for section in gender_sections))
            if output_kind == "medical_district_sections":
                suffixes = [suffix for suffix in suffixes if suffix != "total"]

    year = match.group("year")
    period = match.group("period")
    outputs = []
    for suffix in suffixes:
        suffix_part = f"_{suffix}" if suffix is not None else ""
        outputs.append(f"normalized_{data_type}{suffix_part}_{year}_{period}.csv")
    return sorted(outputs)


def check_processing_coverage(raw_dir: Path, processed_dir: Path) -> dict[str, Any]:
    """Calculate source-based processing coverage and identify mismatched artifacts."""
    raw_files = sorted(raw_dir.rglob("*.csv")) if raw_dir.exists() else []
    processed_files = sorted(processed_dir.rglob("*.csv")) if processed_dir.exists() else []
    processed_paths = {path.relative_to(processed_dir).as_posix() for path in processed_files}

    expected_paths: set[str] = set()
    incomplete_sources: list[dict[str, Any]] = []
    processed_source_count = 0

    for raw_file in raw_files:
        raw_path = raw_file.relative_to(raw_dir).as_posix()
        if raw_file.parent != raw_dir:
            incomplete_sources.append({"raw_file": raw_path, "missing_outputs": [], "reason": "noncanonical_raw_path"})
            continue

        expected_outputs = expected_processed_outputs(raw_file)
        if expected_outputs is None:
            incomplete_sources.append(
                {"raw_file": raw_path, "missing_outputs": [], "reason": "unsupported_raw_filename"}
            )
            continue
        if not expected_outputs:
            incomplete_sources.append(
                {"raw_file": raw_path, "missing_outputs": [], "reason": "unsupported_gender_sections"}
            )
            continue

        expected_paths.update(expected_outputs)
        missing_outputs = sorted(set(expected_outputs) - processed_paths)
        if not missing_outputs:
            processed_source_count += 1
        else:
            incomplete_sources.append({"raw_file": raw_path, "missing_outputs": missing_outputs})

    raw_source_count = len(raw_files)
    processed_rate = (processed_source_count / raw_source_count * 100) if raw_source_count else 0.0
    orphaned_processed_files = sorted(processed_paths - expected_paths)

    return {
        "processed_rate": processed_rate,
        "raw_source_count": raw_source_count,
        "processed_source_count": processed_source_count,
        "incomplete_source_count": len(incomplete_sources),
        "incomplete_sources": incomplete_sources,
        "orphaned_processed_count": len(orphaned_processed_files),
        "orphaned_processed_files": orphaned_processed_files,
    }


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
    print(
        f"処理済みraw: {status['coverage']['processed_source_count']} / " f"{status['coverage']['raw_source_count']}件"
    )
    print(f"未完了raw: {status['coverage']['incomplete_source_count']}件")
    print(f"rawに対応しない処理済みファイル: {status['coverage']['orphaned_processed_count']}件")

    if verbose and status["coverage"]["incomplete_sources"]:
        print("  未完了raw一覧:")
        for source in status["coverage"]["incomplete_sources"]:
            if source.get("reason") == "unsupported_raw_filename":
                print(f"    - {source['raw_file']} (未対応のファイル名)")
            elif source.get("reason") == "noncanonical_raw_path":
                print(f"    - {source['raw_file']} (raw直下ではないファイル)")
            elif source.get("reason") == "unsupported_gender_sections":
                print(f"    - {source['raw_file']} (処理可能な性別セクションなし)")
            else:
                missing = ", ".join(source["missing_outputs"])
                print(f"    - {source['raw_file']} (欠損: {missing})")

    if verbose and status["coverage"]["orphaned_processed_files"]:
        print("  孤立processed一覧:")
        for path in status["coverage"]["orphaned_processed_files"]:
            print(f"    - {path}")

    # 推奨アクション
    print("\n" + "=" * 70)
    print("💡 推奨アクション")
    print("=" * 70)

    processable_incomplete_sources = [
        source for source in status["coverage"]["incomplete_sources"] if source.get("reason") is None
    ]
    unprocessable_sources = [
        source for source in status["coverage"]["incomplete_sources"] if source.get("reason") is not None
    ]

    if status["raw"]["file_count"] == 0:
        print("⚠️  data/raw/にデータがありません")
        print("   → データ取得スクリプトを実行してください")

    else:
        if processable_incomplete_sources:
            if status["coverage"]["processed_source_count"] == 0:
                print("⚠️  データ処理が必要です")
            else:
                print("⚠️  一部のファイルが処理されていません")
            print("   → uv run process-data --all")

        elif not unprocessable_sources:
            print("✅ すべての処理が完了しています")

        if unprocessable_sources:
            print(f"⚠️  処理できないrawファイルが{len(unprocessable_sources)}件あります")
            if any(source.get("reason") == "unsupported_gender_sections" for source in unprocessable_sources):
                print("   → rawの性別セクションを修正してください")
            if any(source.get("reason") != "unsupported_gender_sections" for source in unprocessable_sources):
                print("   → ファイル名または配置を修正してください")

    if status["coverage"]["orphaned_processed_count"] > 0:
        print("⚠️  rawに対応しない処理済みファイルを確認してください")

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
