"""
validate_data.py のテスト
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.validate_data import DataValidator


class TestDataValidatorMarkdownReport(unittest.TestCase):
    """Markdown形式レポート生成のテスト"""

    def setUp(self):
        """テスト準備"""
        self.temp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.temp_dir) / "data"
        self.data_dir.mkdir(parents=True)

    def tearDown(self):
        """テスト後処理: 一時ディレクトリをクリーンアップ"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_generate_markdown_report_empty(self):
        """ファイルがない場合のMarkdownレポート生成"""
        validator = DataValidator()
        report = validator.generate_markdown_report()

        self.assertIn("# データ検証レポート", report)
        self.assertIn("## サマリー", report)
        self.assertIn("| 総ファイル数 | 0 |", report)

    def test_generate_markdown_report_with_valid_file(self):
        """有効なファイルのMarkdownレポート生成"""
        # テスト用CSVファイルを作成 (Shift_JIS)
        test_file = self.data_dir / "test.csv"
        content = "col1,col2,col3\nval1,val2,val3\n"
        test_file.write_bytes(content.encode("shift_jis"))

        validator = DataValidator()
        # validate_directoryを使うことでvalidation_resultsに追加される
        validator.validate_directory(self.data_dir, "*.csv")
        report = validator.generate_markdown_report()

        self.assertIn("# データ検証レポート", report)
        self.assertIn("| 総ファイル数 | 1 |", report)

    def test_generate_markdown_report_with_errors(self):
        """エラーがある場合のMarkdownレポート生成"""
        validator = DataValidator()
        # 手動で結果を追加してテスト
        validator.validation_results.append({
            "file": "/test/file.csv",
            "valid": False,
            "errors": ["File not found"],
            "warnings": [],
        })
        validator.has_errors = True
        report = validator.generate_markdown_report()

        self.assertIn("## サマリー", report)
        self.assertIn("| 無効 | 1 |", report)
        self.assertIn("エラーあり", report)
        self.assertIn("## ❌ エラー", report)

    def test_generate_markdown_report_with_warnings(self):
        """警告がある場合のMarkdownレポート生成"""
        validator = DataValidator()
        validator.validation_results.append({
            "file": "/test/file.csv",
            "valid": True,
            "errors": [],
            "warnings": ["Inconsistent column count"],
        })
        validator.has_warnings = True
        report = validator.generate_markdown_report()

        self.assertIn("警告あり", report)
        self.assertIn("## ⚠️ 警告", report)
        self.assertIn("Inconsistent column count", report)


class TestMarkdownEscaping(unittest.TestCase):
    """Markdown特殊文字のエスケープテスト"""

    def test_escape_markdown_pipe(self):
        """パイプ文字のエスケープ"""
        result = DataValidator._escape_markdown("test|value")
        self.assertEqual(result, "test\\|value")

    def test_escape_markdown_backtick(self):
        """バックティックのエスケープ"""
        result = DataValidator._escape_markdown("test`code`value")
        self.assertEqual(result, "test\\`code\\`value")

    def test_escape_markdown_combined(self):
        """複合的な特殊文字のエスケープ"""
        result = DataValidator._escape_markdown("file|name`test`.csv")
        self.assertEqual(result, "file\\|name\\`test\\`.csv")

    def test_escape_markdown_non_string(self):
        """非文字列入力の処理"""
        result = DataValidator._escape_markdown(123)
        self.assertEqual(result, "123")

    def test_markdown_report_with_special_chars_in_error(self):
        """エラーメッセージに特殊文字が含まれる場合"""
        validator = DataValidator()
        validator.validation_results.append({
            "file": "/test/file|special.csv",
            "valid": False,
            "errors": ["Error with | pipe and ` backtick"],
            "warnings": [],
        })
        validator.has_errors = True
        report = validator.generate_markdown_report()

        # パイプとバックティックがエスケープされていることを確認
        self.assertIn("\\|", report)
        self.assertIn("\\`", report)


class TestDataValidatorFormatOption(unittest.TestCase):
    """--formatオプションのテスト"""

    def setUp(self):
        """テスト準備"""
        self.project_root = Path(__file__).parent.parent
        self.temp_dir = tempfile.mkdtemp()
        self.data_dir = self.project_root / "data" / "raw"

    def tearDown(self):
        """テスト後処理: 一時ディレクトリをクリーンアップ"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_format_option_json(self):
        """JSON形式出力のテスト"""
        output_file = Path(self.temp_dir) / "report.json"

        result = subprocess.run(
            [
                sys.executable,
                "scripts/validate_data.py",
                str(self.data_dir),
                "--pattern",
                "sentinel_weekly_gender_2025_48.csv",
                "--format",
                "json",
                "--output",
                str(output_file),
                "--log-level",
                "ERROR",
            ],
            capture_output=True,
            text=True,
            cwd=self.project_root,
            check=False,
        )

        self.assertEqual(
            result.returncode, 0,
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        self.assertTrue(output_file.exists())

        # JSONとして読み込めることを確認
        with output_file.open() as f:
            data = json.load(f)
        self.assertIn("summary", data)

    def test_format_option_markdown(self):
        """Markdown形式出力のテスト"""
        output_file = Path(self.temp_dir) / "report.md"

        result = subprocess.run(
            [
                sys.executable,
                "scripts/validate_data.py",
                str(self.data_dir),
                "--pattern",
                "sentinel_weekly_gender_2025_48.csv",
                "--format",
                "markdown",
                "--output",
                str(output_file),
                "--log-level",
                "ERROR",
            ],
            capture_output=True,
            text=True,
            cwd=self.project_root,
            check=False,
        )

        self.assertEqual(
            result.returncode, 0,
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        self.assertTrue(output_file.exists())

        # Markdownの内容を確認
        content = output_file.read_text()
        self.assertIn("# データ検証レポート", content)
        self.assertIn("## サマリー", content)


if __name__ == "__main__":
    unittest.main()
