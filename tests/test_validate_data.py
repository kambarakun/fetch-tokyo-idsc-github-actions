"""
validate_data.py のテスト
"""

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


class TestDataValidatorFormatOption(unittest.TestCase):
    """--formatオプションのテスト"""

    def test_format_option_json(self):
        """JSON形式出力のテスト"""
        import json
        import os

        # プロジェクトルートを取得
        project_root = Path(__file__).parent.parent
        temp_dir = tempfile.mkdtemp()
        output_file = Path(temp_dir) / "report.json"

        # data/rawディレクトリを使用 (パス安全性チェックを通すため)
        data_dir = project_root / "data" / "raw"

        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "scripts/validate_data.py",
                str(data_dir),
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
            cwd=project_root,
        )

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertTrue(output_file.exists())

        # JSONとして読み込めることを確認
        with output_file.open() as f:
            data = json.load(f)
        self.assertIn("summary", data)

    def test_format_option_markdown(self):
        """Markdown形式出力のテスト"""
        project_root = Path(__file__).parent.parent
        temp_dir = tempfile.mkdtemp()
        output_file = Path(temp_dir) / "report.md"

        # data/rawディレクトリを使用
        data_dir = project_root / "data" / "raw"

        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "scripts/validate_data.py",
                str(data_dir),
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
            cwd=project_root,
        )

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertTrue(output_file.exists())

        # Markdownの内容を確認
        content = output_file.read_text()
        self.assertIn("# データ検証レポート", content)
        self.assertIn("## サマリー", content)


if __name__ == "__main__":
    unittest.main()
