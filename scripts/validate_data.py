#!/usr/bin/env python3
"""
取得したデータの妥当性を検証するスクリプト

セキュリティとデータ品質の観点から、取得したCSVファイルを検証し、
悪意のあるデータや破損データがmainブランチに入ることを防ぐ。
"""
# mypy: ignore-errors

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# プロジェクトのルートディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

# 検証設定
MAX_FILE_SIZE_MB = 50  # 最大ファイルサイズ（MB）
MIN_FILE_SIZE_BYTES = 100  # 最小ファイルサイズ（バイト）
MAX_LINE_COUNT = 1000000  # 最大行数
MIN_LINE_COUNT = 1  # 最小行数
EXPECTED_ENCODING = "shift_jis"  # 期待されるエンコーディング
MAX_COLUMN_COUNT = 100  # 最大カラム数
MIN_COLUMN_COUNT = 2  # 最小カラム数


def setup_logging(log_level: str = "INFO"):
    """ロギングのセットアップ"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


class DataValidator:
    """データ検証クラス"""

    def __init__(self, strict_mode: bool = False):
        """
        Args:
            strict_mode: 厳格モード（警告もエラーとして扱う）
        """
        self.strict_mode = strict_mode
        self.logger = logging.getLogger(__name__)
        self.validation_results: list[dict[str, Any]] = []
        self.has_errors = False
        self.has_warnings = False

    def validate_file(self, file_path: Path) -> dict[str, Any]:
        """ファイルを検証する

        Args:
            file_path: 検証するファイルのパス

        Returns:
            検証結果の辞書
        """
        result: dict[str, Any] = {
            "file": str(file_path),
            "timestamp": datetime.now().isoformat(),
            "checks": {},
            "errors": [],
            "warnings": [],
            "valid": True,
        }

        try:
            # ファイル存在チェック
            if not file_path.exists():
                result["errors"].append(f"File not found: {file_path}")
                result["valid"] = False
                return result

            # ファイルサイズチェック
            size_result = self._check_file_size(file_path)
            result["checks"]["file_size"] = size_result
            if not size_result["valid"]:
                result["errors"].extend(size_result.get("errors", []))
                result["warnings"].extend(size_result.get("warnings", []))

            # エンコーディングチェック
            encoding_result = self._check_encoding(file_path)
            result["checks"]["encoding"] = encoding_result
            if not encoding_result["valid"]:
                result["errors"].extend(encoding_result.get("errors", []))

            # CSVフォーマットチェック
            if file_path.suffix.lower() == ".csv":
                csv_result = self._check_csv_format(file_path)
                result["checks"]["csv_format"] = csv_result
                if not csv_result["valid"]:
                    result["errors"].extend(csv_result.get("errors", []))
                    result["warnings"].extend(csv_result.get("warnings", []))

            # パストラバーサルチェック
            path_result = self._check_path_safety(file_path)
            result["checks"]["path_safety"] = path_result
            if not path_result["valid"]:
                result["errors"].extend(path_result.get("errors", []))

            # 結果の集計
            if result["errors"]:
                result["valid"] = False
                self.has_errors = True
            elif result["warnings"] and self.strict_mode:
                result["valid"] = False
                self.has_warnings = True

        except Exception as e:
            self.logger.exception(f"Unexpected error validating {file_path}")
            result["errors"].append(f"Validation failed: {str(e)}")
            result["valid"] = False
            self.has_errors = True

        return result

    def _check_file_size(self, file_path: Path) -> dict[str, Any]:
        """ファイルサイズをチェック"""
        result = {"valid": True, "errors": [], "warnings": []}

        try:
            size_bytes = file_path.stat().st_size
            size_mb = size_bytes / (1024 * 1024)

            result["size_bytes"] = size_bytes
            result["size_mb"] = round(size_mb, 2)

            if size_bytes < MIN_FILE_SIZE_BYTES:
                result["errors"].append(f"File too small: {size_bytes} bytes (minimum: {MIN_FILE_SIZE_BYTES})")
                result["valid"] = False
            elif size_mb > MAX_FILE_SIZE_MB:
                result["errors"].append(f"File too large: {size_mb:.2f} MB (maximum: {MAX_FILE_SIZE_MB} MB)")
                result["valid"] = False
            elif size_mb > MAX_FILE_SIZE_MB * 0.8:
                result["warnings"].append(f"File size warning: {size_mb:.2f} MB (80% of maximum)")

        except Exception as e:
            result["errors"].append(f"Failed to check file size: {str(e)}")
            result["valid"] = False

        return result

    def _check_encoding(self, file_path: Path) -> dict[str, Any]:
        """エンコーディングをチェック"""
        result = {"valid": True, "errors": []}

        try:
            # Shift_JISで読み込みを試みる
            with file_path.open("r", encoding=EXPECTED_ENCODING) as f:
                # 最初の数行を読んで確認
                for i, _line in enumerate(f):
                    if i >= 10:  # 最初の10行のみチェック
                        break
                result["encoding"] = EXPECTED_ENCODING

        except UnicodeDecodeError as e:
            result["errors"].append(f"Encoding error (expected {EXPECTED_ENCODING}): {str(e)}")
            result["valid"] = False
        except Exception as e:
            result["errors"].append(f"Failed to check encoding: {str(e)}")
            result["valid"] = False

        return result

    def _check_csv_format(self, file_path: Path) -> dict[str, Any]:
        """CSVフォーマットをチェック"""
        result = {"valid": True, "errors": [], "warnings": []}

        try:
            with file_path.open("r", encoding=EXPECTED_ENCODING) as f:
                # CSVリーダーで読み込み
                reader = csv.reader(f)

                line_count = 0
                column_counts = set()
                max_columns = 0

                for row in reader:
                    line_count += 1
                    column_count = len(row)
                    column_counts.add(column_count)
                    max_columns = max(max_columns, column_count)

                    # 行数チェック（早期終了）
                    if line_count > MAX_LINE_COUNT:
                        result["errors"].append(f"Too many lines: >{MAX_LINE_COUNT}")
                        result["valid"] = False
                        break

                result["line_count"] = line_count
                result["column_variations"] = len(column_counts)
                result["max_columns"] = max_columns

                # 検証
                if line_count < MIN_LINE_COUNT:
                    result["errors"].append(f"Too few lines: {line_count} (minimum: {MIN_LINE_COUNT})")
                    result["valid"] = False

                if max_columns > MAX_COLUMN_COUNT:
                    result["errors"].append(f"Too many columns: {max_columns} (maximum: {MAX_COLUMN_COUNT})")
                    result["valid"] = False
                elif max_columns < MIN_COLUMN_COUNT:
                    result["errors"].append(f"Too few columns: {max_columns} (minimum: {MIN_COLUMN_COUNT})")
                    result["valid"] = False

                # カラム数の一貫性チェック
                if len(column_counts) > 1:
                    result["warnings"].append(f"Inconsistent column count: {column_counts}")

        except csv.Error as e:
            result["errors"].append(f"CSV format error: {str(e)}")
            result["valid"] = False
        except Exception as e:
            result["errors"].append(f"Failed to check CSV format: {str(e)}")
            result["valid"] = False

        return result

    def _check_path_safety(self, file_path: Path) -> dict[str, Any]:
        """パスの安全性をチェック（パストラバーサル攻撃対策）"""
        result = {"valid": True, "errors": []}

        try:
            # 絶対パスを解決
            resolved_path = file_path.resolve()
            base_path = Path.cwd() / "data"

            # base_path内にあることを確認
            if not str(resolved_path).startswith(str(base_path)):
                result["errors"].append(f"Path traversal detected: {resolved_path} not in {base_path}")
                result["valid"] = False

            # 危険な文字のチェック
            dangerous_patterns = ["../", "..\\", "~", "|", "&", ";", "$", "`"]
            path_str = str(file_path)
            for pattern in dangerous_patterns:
                if pattern in path_str:
                    result["errors"].append(f"Dangerous pattern in path: {pattern}")
                    result["valid"] = False

        except Exception as e:
            result["errors"].append(f"Failed to check path safety: {str(e)}")
            result["valid"] = False

        return result

    def validate_directory(self, directory: Path, pattern: str = "*.csv") -> list[dict[str, Any]]:
        """ディレクトリ内のファイルを検証

        Args:
            directory: 検証するディレクトリ
            pattern: ファイルパターン（glob形式）

        Returns:
            各ファイルの検証結果のリスト
        """
        results: list[dict[str, Any]] = []

        if not directory.exists():
            self.logger.error(f"Directory not found: {directory}")
            return results

        files = list(directory.glob(pattern))
        self.logger.info(f"Found {len(files)} files to validate in {directory}")

        for file_path in files:
            self.logger.info(f"Validating: {file_path}")
            result = self.validate_file(file_path)
            results.append(result)
            self.validation_results.append(result)

            # 結果のログ出力
            if result["valid"]:
                self.logger.info(f"✓ Valid: {file_path}")
            else:
                self.logger.error(f"✗ Invalid: {file_path}")
                for error in result["errors"]:
                    self.logger.error(f"  - {error}")
                for warning in result["warnings"]:
                    self.logger.warning(f"  - {warning}")

        return results

    def generate_report(self) -> dict[str, Any]:
        """検証レポートを生成"""
        total_files = len(self.validation_results)
        valid_files = sum(1 for r in self.validation_results if r["valid"])
        invalid_files = total_files - valid_files

        return {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_files": total_files,
                "valid_files": valid_files,
                "invalid_files": invalid_files,
                "has_errors": self.has_errors,
                "has_warnings": self.has_warnings,
                "success_rate": (valid_files / total_files * 100) if total_files > 0 else 0,
            },
            "results": self.validation_results,
        }


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(description="東京都感染症データの妥当性検証")
    parser.add_argument(
        "path",
        type=str,
        nargs="?",
        default="data/raw",
        help="検証するファイルまたはディレクトリのパス",
    )
    parser.add_argument(
        "--pattern",
        type=str,
        default="*.csv",
        help="検証するファイルパターン（glob形式）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="厳格モード（警告もエラーとして扱う）",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="検証結果をJSONファイルに出力",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル",
    )

    args = parser.parse_args()

    # ロギング設定
    logger = setup_logging(args.log_level)

    # バリデーター作成
    validator = DataValidator(strict_mode=args.strict)

    # パスの処理
    path = Path(args.path)

    if path.is_file():
        # 単一ファイルの検証
        validator.validate_file(path)
    else:
        # ディレクトリの検証
        validator.validate_directory(path, args.pattern)

    # レポート生成
    report = validator.generate_report()

    # 結果の出力
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"Report saved to: {output_path}")

    # サマリー表示
    summary = report["summary"]
    print("\n" + "=" * 60)
    print("検証結果サマリー:")
    print(f"  総ファイル数: {summary['total_files']}")
    print(f"  有効: {summary['valid_files']}")
    print(f"  無効: {summary['invalid_files']}")
    print(f"  成功率: {summary['success_rate']:.1f}%")
    print("=" * 60)

    # 終了コード
    if validator.has_errors or (validator.has_warnings and args.strict):
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
