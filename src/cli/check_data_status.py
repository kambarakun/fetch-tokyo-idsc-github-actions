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

    # 未処理・改訂後未再処理のrawパスを1行1件で出力 (--json とは併用不可)
    uv run check-data-status --list-needs-processing

    # 未完了 (処理できないものを含む)・改訂後未再処理のrawがあれば終了コード1 (CIのゲート用)
    uv run check-data-status --fail-on-incomplete
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from src.cli.check_missing import FILENAME_PATTERN
from src.processors.data_processor import (
    GENDER_SUFFIX_BY_LABEL,
    NOTIFIABLE_DATA_START_MARKERS,
    SENTINEL_DATA_START_MARKERS,
    detect_gender_sections,
    extract_gender_section_data,
    find_data_start_line,
    section_has_data_rows,
)

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


def main() -> int:
    """メイン処理

    Returns:
        終了コード (--fail-on-incomplete 指定時に未完了 (処理できないものを含む)・改訂後未再処理のrawがあれば1、それ以外は0)
    """
    parser = argparse.ArgumentParser(description="データ処理状況確認スクリプト")

    parser.add_argument("--data-dir", type=str, default="data", help="dataディレクトリのパス(デフォルト: data)")

    parser.add_argument("-v", "--verbose", action="store_true", help="詳細情報を表示")

    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="JSON形式で出力")
    output_group.add_argument(
        "--list-needs-processing",
        action="store_true",
        help="処理が必要なraw (出力欠損・改訂後未再処理) のパスを1行1件で出力",
    )

    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help=(
            "未完了 (出力欠損・処理できないraw) または改訂後未再処理のrawがあれば終了コード1で終了"
            " (rawに対応しない processed は対象外、報告のみ)"
        ),
    )

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

    if args.list_needs_processing:
        # stdout is consumed as a file list (e.g. by process-data --files), so print paths only.
        for raw_file in needs_processing_raw_files(status["coverage"]):
            print(str(Path(args.data_dir) / "raw" / raw_file))
    elif args.json:
        # JSON形式で出力
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        # 人間可読形式で出力
        print_status(status, args.verbose)

    # Sources reprocessing cannot fix (unsupported name, nested path, unprocessable content) still
    # fail the gate even though --list-needs-processing omits them.
    # Orphaned processed files stay report-only: issue #725 decided so, and the fetch job never renames or deletes raw.
    coverage = status["coverage"]
    if args.fail_on_incomplete and (coverage["incomplete_source_count"] > 0 or coverage["stale_source_count"] > 0):
        return 1
    return 0


def needs_processing_raw_files(coverage: dict[str, Any]) -> list[str]:
    """Return raw files that re-running the processor would fix (missing outputs or stale outputs).

    Sources with a ``reason`` (unsupported name, noncanonical path, unprocessable content) are
    excluded because reprocessing cannot fix them.
    """
    raw_files = {source["raw_file"] for source in coverage["incomplete_sources"] if source.get("reason") is None}
    raw_files.update(source["raw_file"] for source in coverage["stale_sources"])
    return sorted(raw_files)


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

    lines = raw_path.read_text(encoding="shift_jis", errors="replace").splitlines()
    suffixes: list[str | None]
    if output_kind == "single":
        suffixes = [None] if find_data_start_line(lines, NOTIFIABLE_DATA_START_MARKERS) is not None else []
    else:
        gender_sections = detect_gender_sections(lines)
        if not gender_sections:
            suffixes = [None] if find_data_start_line(lines, SENTINEL_DATA_START_MARKERS) is not None else []
        else:
            section_rows = [
                (GENDER_SUFFIX_BY_LABEL[section["gender"]], extract_gender_section_data(lines, section))
                for section in gender_sections
            ]
            if output_kind == "medical_district_sections":
                # Mirror DataProcessor: a district file without male/female sections is rejected as a whole,
                # and its 男女合計 section is emitted only when it has rows beyond the header.
                has_male_or_female = any(suffix != "total" for suffix, _ in section_rows)
                section_rows = [
                    (suffix, rows)
                    for suffix, rows in section_rows
                    if has_male_or_female and (suffix != "total" or section_has_data_rows(rows))
                ]
            suffixes = list(dict.fromkeys(suffix for suffix, rows in section_rows if rows))

    year = match.group("year")
    period = match.group("period")
    outputs = []
    for suffix in suffixes:
        suffix_part = f"_{suffix}" if suffix is not None else ""
        outputs.append(f"normalized_{data_type}{suffix_part}_{year}_{period}.csv")
    return sorted(outputs)


def _raise_scan_error(error: OSError) -> None:
    raise error


def find_csv_files(dir_path: Path) -> list[Path]:
    """Find CSV files without suppressing directory traversal errors."""
    if not dir_path.exists():
        return []

    return sorted(
        Path(root) / filename
        for root, _directories, filenames in os.walk(dir_path, onerror=_raise_scan_error)
        for filename in filenames
        if filename.endswith(".csv")
    )


def _calculate_file_hash(file_path: Path) -> str:
    """Return the sha256 hex digest of a file, matching the processor's ``source_hash``."""
    sha256 = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()


def _recorded_source_hash(metadata_file: Path) -> object:
    """Return ``_process.source_hash`` from processed metadata, or None when it cannot be read."""
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    process = metadata.get("_process") if isinstance(metadata, dict) else None
    return process.get("source_hash") if isinstance(process, dict) else None


def find_stale_outputs(raw_file: Path, expected_outputs: list[str], metadata_dir: Path) -> list[str]:
    """Return outputs whose metadata does not record the current raw file hash.

    Missing or unreadable metadata counts as stale: the output cannot be proven to reflect
    the current raw content (e.g. the raw file was revised after processing).
    """
    raw_hash = _calculate_file_hash(raw_file)
    return sorted(
        output
        for output in expected_outputs
        if _recorded_source_hash(metadata_dir / f"{Path(output).stem}.json") != raw_hash
    )


def check_processing_coverage(raw_dir: Path, processed_dir: Path) -> dict[str, Any]:
    """Calculate source-based processing coverage and identify mismatched artifacts.

    A source counts as processed only when every expected output exists and its metadata
    records the current raw file hash; otherwise it is reported as incomplete (missing outputs)
    or stale (outputs predate a raw revision).
    """
    raw_files = find_csv_files(raw_dir)
    processed_files = find_csv_files(processed_dir)
    processed_paths = {path.relative_to(processed_dir).as_posix() for path in processed_files}
    metadata_dir = processed_dir / ".metadata"

    expected_paths: set[str] = set()
    incomplete_sources: list[dict[str, Any]] = []
    stale_sources: list[dict[str, Any]] = []
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
                {"raw_file": raw_path, "missing_outputs": [], "reason": "unprocessable_raw_content"}
            )
            continue

        expected_paths.update(expected_outputs)
        missing_outputs = sorted(set(expected_outputs) - processed_paths)
        if missing_outputs:
            incomplete_sources.append({"raw_file": raw_path, "missing_outputs": missing_outputs})
            continue

        stale_outputs = find_stale_outputs(raw_file, expected_outputs, metadata_dir)
        if stale_outputs:
            stale_sources.append({"raw_file": raw_path, "stale_outputs": stale_outputs})
        else:
            processed_source_count += 1

    raw_source_count = len(raw_files)
    processed_rate = (processed_source_count / raw_source_count * 100) if raw_source_count else 0.0
    orphaned_processed_files = sorted(processed_paths - expected_paths)

    return {
        "processed_rate": processed_rate,
        "raw_source_count": raw_source_count,
        "processed_source_count": processed_source_count,
        "incomplete_source_count": len(incomplete_sources),
        "incomplete_sources": incomplete_sources,
        "stale_source_count": len(stale_sources),
        "stale_sources": stale_sources,
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
    csv_files = find_csv_files(dir_path)
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
    print(f"改訂後未再処理raw: {status['coverage']['stale_source_count']}件")
    print(f"rawに対応しない処理済みファイル: {status['coverage']['orphaned_processed_count']}件")

    if verbose and status["coverage"]["incomplete_sources"]:
        print("  未完了raw一覧:")
        for source in status["coverage"]["incomplete_sources"]:
            if source.get("reason") == "unsupported_raw_filename":
                print(f"    - {source['raw_file']} (未対応のファイル名)")
            elif source.get("reason") == "noncanonical_raw_path":
                print(f"    - {source['raw_file']} (raw直下ではないファイル)")
            elif source.get("reason") == "unprocessable_raw_content":
                print(f"    - {source['raw_file']} (処理可能なデータ構造なし)")
            else:
                missing = ", ".join(source["missing_outputs"])
                print(f"    - {source['raw_file']} (欠損: {missing})")

    if verbose and status["coverage"]["stale_sources"]:
        print("  改訂後未再処理raw一覧:")
        for source in status["coverage"]["stale_sources"]:
            stale = ", ".join(source["stale_outputs"])
            print(f"    - {source['raw_file']} (再処理が必要: {stale})")

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
    stale_sources = status["coverage"]["stale_sources"]

    if status["raw"]["file_count"] == 0:
        print("⚠️  data/raw/にデータがありません")
        print("   → データ取得スクリプトを実行してください")

    else:
        if processable_incomplete_sources:
            if status["coverage"]["processed_source_count"] == 0:
                print("⚠️  データ処理が必要です")
            else:
                print("⚠️  一部のファイルが処理されていません")

        if stale_sources:
            print("⚠️  改訂後に再処理されていないrawがあります")

        if processable_incomplete_sources or stale_sources:
            print(
                "   → uv run check-data-status --list-needs-processing の一覧を process-data --files に渡してください"
            )

        elif not unprocessable_sources:
            print("✅ すべての処理が完了しています")

        if unprocessable_sources:
            print(f"⚠️  処理できないrawファイルが{len(unprocessable_sources)}件あります")
            if any(source.get("reason") == "unprocessable_raw_content" for source in unprocessable_sources):
                print("   → rawの内容を修正してください")
            if any(source.get("reason") != "unprocessable_raw_content" for source in unprocessable_sources):
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
    sys.exit(main())
