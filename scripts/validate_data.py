#!/usr/bin/env python3
"""
取得したデータの妥当性を検証するスクリプト

セキュリティとデータ品質の観点から、取得したCSVファイルを検証し、
悪意のあるデータや破損データがmainブランチに入ることを防ぐ。

エンコーディング:
- raw データ: Shift_JIS (デフォルト、Tokyo IDSCからの取得データ)
- processed データ: UTF-8 (--encoding utf-8 を指定)
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

# storage_manager から検証設定と統一メッセージ定数をインポート
from src.managers.storage_manager import CSV_FORMAT_INCONSISTENT_COLUMN_COUNT_MSG
from src.managers.storage_manager import VALIDATION_MAX_COLUMN_COUNT as MAX_COLUMN_COUNT
from src.managers.storage_manager import VALIDATION_MAX_FILE_SIZE_MB as MAX_FILE_SIZE_MB
from src.managers.storage_manager import VALIDATION_MAX_LINE_COUNT as MAX_LINE_COUNT
from src.managers.storage_manager import VALIDATION_MIN_COLUMN_COUNT as MIN_COLUMN_COUNT
from src.managers.storage_manager import VALIDATION_MIN_FILE_SIZE as MIN_FILE_SIZE_BYTES
from src.managers.storage_manager import VALIDATION_MIN_LINE_COUNT as MIN_LINE_COUNT


def setup_logging(log_level: str = "INFO"):
    """ロギングのセットアップ"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


class DataValidator:
    """データ検証クラス"""

    def __init__(self, strict_mode: bool = False, encoding: str = "shift_jis"):
        """
        Args:
            strict_mode: 厳格モード(警告もエラーとして扱う)
            encoding: ファイルエンコーディング (デフォルト: shift_jis)
        """
        self.strict_mode = strict_mode
        self.encoding = encoding
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
        details: dict[str, Any] = {}

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
            # warningsはvalidに関係なく常に収集
            result["warnings"].extend(size_result.get("warnings", []))
            if size_result.get("details"):
                details.update(size_result["details"])

            # エンコーディングチェック
            encoding_result = self._check_encoding(file_path)
            result["checks"]["encoding"] = encoding_result
            if not encoding_result["valid"]:
                result["errors"].extend(encoding_result.get("errors", []))
            if encoding_result.get("details"):
                details.update(encoding_result["details"])

            # CSVフォーマットチェック
            if file_path.suffix.lower() == ".csv":
                csv_result = self._check_csv_format(file_path)
                result["checks"]["csv_format"] = csv_result
                if not csv_result["valid"]:
                    result["errors"].extend(csv_result.get("errors", []))
                # warningsはvalidに関係なく常に収集
                result["warnings"].extend(csv_result.get("warnings", []))
                if csv_result.get("details"):
                    details.update(csv_result["details"])

            # パストラバーサルチェック
            path_result = self._check_path_safety(file_path)
            result["checks"]["path_safety"] = path_result
            if not path_result["valid"]:
                result["errors"].extend(path_result.get("errors", []))
            if path_result.get("details"):
                details.update(path_result["details"])

            # 詳細情報がある場合のみdetailsフィールドを追加
            if details:
                result["details"] = details

            # 結果の集計
            if result["errors"]:
                result["valid"] = False
                self.has_errors = True
            # warningsは常に記録し、strictモード時はinvalidにする
            if result["warnings"]:
                self.has_warnings = True
                if self.strict_mode:
                    result["valid"] = False

        except Exception as e:
            self.logger.exception(f"Unexpected error validating {file_path}")
            result["errors"].append(f"Validation failed: {e!s}")
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
            result["errors"].append(f"Failed to check file size: {e!s}")
            result["valid"] = False

        return result

    def _check_encoding(self, file_path: Path) -> dict[str, Any]:
        """エンコーディングをチェック"""
        result = {"valid": True, "errors": []}

        try:
            # 指定されたエンコーディングで読み込みを試みる
            with file_path.open("r", encoding=self.encoding) as f:
                # 最初の数行を読んで確認
                for i, _line in enumerate(f):
                    if i >= 10:  # 最初の10行のみチェック
                        break
                result["encoding"] = self.encoding

        except UnicodeDecodeError as e:
            result["errors"].append(f"Encoding error (expected {self.encoding}): {e!s}")
            result["valid"] = False
        except Exception as e:
            result["errors"].append(f"Failed to check encoding: {e!s}")
            result["valid"] = False

        return result

    def _check_csv_format(self, file_path: Path) -> dict[str, Any]:
        """CSVフォーマットをチェック"""
        result = {"valid": True, "errors": [], "warnings": [], "details": {}}

        try:
            with file_path.open("r", encoding=self.encoding) as f:
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

                    # 行数チェック(早期終了)
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
                    # 警告メッセージは統一形式 (v1.3.0: storage_managerと共通化)
                    result["warnings"].append(CSV_FORMAT_INCONSISTENT_COLUMN_COUNT_MSG)
                    # 詳細情報はdetailsフィールドに保存 (ソート済みリスト形式)
                    result["details"]["column_counts"] = sorted(column_counts)

        except csv.Error as e:
            result["errors"].append(f"CSV format error: {e!s}")
            result["valid"] = False
        except Exception as e:
            result["errors"].append(f"Failed to check CSV format: {e!s}")
            result["valid"] = False

        return result

    def _check_path_safety(self, file_path: Path) -> dict[str, Any]:
        """パスの安全性をチェック(パストラバーサル攻撃対策)"""
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
            result["errors"].append(f"Failed to check path safety: {e!s}")
            result["valid"] = False

        return result

    def validate_directory(self, directory: Path, pattern: str = "*.csv") -> list[dict[str, Any]]:
        """ディレクトリ内のファイルを検証

        Args:
            directory: 検証するディレクトリ
            pattern: ファイルパターン(glob形式)

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

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Markdownの特殊文字をエスケープする

        テーブルセル内で問題になる文字をエスケープします。
        """
        if not isinstance(text, str):
            text = str(text)
        # パイプはテーブルの区切りになるのでエスケープ
        # バックティックはコードブロックになるのでエスケープ
        return text.replace("|", "\\|").replace("`", "\\`")

    def generate_markdown_report(self) -> str:
        """検証レポートをMarkdown形式で生成"""
        report = self.generate_report()
        summary = report["summary"]
        lines: list[str] = []

        # ヘッダー
        lines.append("# データ検証レポート")
        lines.append("")
        lines.append(f"**実行日時**: {report['timestamp']}")
        lines.append("")

        # サマリー
        lines.append("## サマリー")
        lines.append("")

        # ステータスアイコン
        if summary["has_errors"]:
            status_icon = "❌"
            status_text = "エラーあり"
        elif summary["has_warnings"]:
            status_icon = "⚠️"
            status_text = "警告あり"
        else:
            status_icon = "✅"
            status_text = "正常"

        lines.append("| 項目 | 値 |")
        lines.append("|------|-----|")
        lines.append(f"| ステータス | {status_icon} {status_text} |")
        lines.append(f"| 総ファイル数 | {summary['total_files']} |")
        lines.append(f"| 有効 | {summary['valid_files']} |")
        lines.append(f"| 無効 | {summary['invalid_files']} |")
        lines.append(f"| 成功率 | {summary['success_rate']:.1f}% |")
        lines.append("")

        # エラー/警告の詳細
        errors_exist = any(r["errors"] for r in report["results"])
        warnings_exist = any(r["warnings"] for r in report["results"])

        if errors_exist:
            lines.append("## ❌ エラー")
            lines.append("")
            for result in report["results"]:
                if result["errors"]:
                    escaped_file = self._escape_markdown(result["file"])
                    lines.append(f"### `{escaped_file}`")
                    lines.append("")
                    for error in result["errors"]:
                        escaped_error = self._escape_markdown(error)
                        lines.append(f"- {escaped_error}")
                    lines.append("")

        if warnings_exist:
            lines.append("## ⚠️ 警告")
            lines.append("")
            for result in report["results"]:
                if result["warnings"]:
                    escaped_file = self._escape_markdown(result["file"])
                    lines.append(f"### `{escaped_file}`")
                    lines.append("")
                    for warning in result["warnings"]:
                        escaped_warning = self._escape_markdown(warning)
                        lines.append(f"- {escaped_warning}")
                    lines.append("")

        # 検証済みファイル一覧 (エラー/警告がない場合のみ詳細表示)
        if not errors_exist and not warnings_exist and report["results"]:
            lines.append("## 検証済みファイル")
            lines.append("")
            lines.append("| ファイル | サイズ | 行数 | ステータス |")
            lines.append("|----------|--------|------|------------|")
            for result in report["results"]:
                file_name = self._escape_markdown(Path(result["file"]).name)
                size = result.get("checks", {}).get("file_size", {}).get("size_mb", "N/A")
                if isinstance(size, int | float):
                    size = f"{size:.2f} MB"
                line_count = result.get("checks", {}).get("csv_format", {}).get("line_count", "N/A")
                status = "✅" if result["valid"] else "❌"
                lines.append(f"| {file_name} | {size} | {line_count} | {status} |")
            lines.append("")

        return "\n".join(lines)


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
        help="検証するファイルパターン(glob形式)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="厳格モード(警告もエラーとして扱う)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="検証結果をファイルに出力",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="json",
        choices=["json", "markdown"],
        help="出力形式 (json または markdown)",
    )
    parser.add_argument(
        "--encoding",
        type=str,
        default="shift_jis",
        help="ファイルエンコーディング (デフォルト: shift_jis, processed データには utf-8 を指定)",
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
    validator = DataValidator(strict_mode=args.strict, encoding=args.encoding)

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
            if args.format == "markdown":
                f.write(validator.generate_markdown_report())
            else:
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
