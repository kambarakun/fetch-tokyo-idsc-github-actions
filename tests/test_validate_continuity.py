"""
データ連続性検証のテスト
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# パスの設定
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.validate_continuity import ContinuityReport, ContinuityValidator


class TestContinuityValidator(unittest.TestCase):
    """連続性検証のテスト"""

    def setUp(self):
        """テスト準備"""
        # 一時ディレクトリを作成
        self.temp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.temp_dir)
        self.validator = ContinuityValidator(self.data_dir)

    def tearDown(self):
        """テスト後処理"""
        # 一時ディレクトリをクリーンアップ
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_files(self, file_patterns):
        """テスト用ファイルを作成

        Args:
            file_patterns: ファイル名のリスト
        """
        for pattern in file_patterns:
            file_path = self.data_dir / pattern
            file_path.write_text("test data")

    def test_validate_data_type_no_missing(self):
        """欠損なしのデータタイプ検証"""
        # 2025年の第1週から第10週までのファイルを作成
        files = [f"sentinel_weekly_gender_2025_{week:02d}.csv" for week in range(1, 11)]
        self._create_test_files(files)

        # 検証実行（第1週から第10週を対象）
        with patch("scripts.validate_continuity.datetime") as mock_datetime:
            # 2025年第10週を現在時刻として設定
            mock_datetime.now.return_value.year = 2025
            mock_datetime.now.return_value.isocalendar.return_value = (2025, 10, 1)

            report = self.validator.validate_data_type("sentinel_weekly_gender", 2025, 2025)

        # 検証
        self.assertTrue(report.is_valid)
        self.assertEqual(len(report.missing_periods), 0)
        self.assertEqual(report.expected_count, 10)
        self.assertEqual(report.actual_count, 10)

    def test_validate_data_type_with_missing(self):
        """欠損ありのデータタイプ検証"""
        # 第1, 2, 4, 5週のファイルを作成（第3週が欠損）
        files = [
            "sentinel_weekly_gender_2025_01.csv",
            "sentinel_weekly_gender_2025_02.csv",
            "sentinel_weekly_gender_2025_04.csv",
            "sentinel_weekly_gender_2025_05.csv",
        ]
        self._create_test_files(files)

        # 検証実行
        with patch("scripts.validate_continuity.datetime") as mock_datetime:
            mock_datetime.now.return_value.year = 2025
            mock_datetime.now.return_value.isocalendar.return_value = (2025, 5, 1)

            report = self.validator.validate_data_type("sentinel_weekly_gender", 2025, 2025)

        # 検証
        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.missing_periods), 1)
        self.assertEqual(report.missing_periods[0]["year"], 2025)
        self.assertEqual(report.missing_periods[0]["period"], 3)

    def test_validate_monthly_data(self):
        """月次データの検証"""
        # 1月、2月、4月のファイルを作成（3月が欠損）
        files = [
            "sentinel_monthly_age_2025_01.csv",
            "sentinel_monthly_age_2025_02.csv",
            "sentinel_monthly_age_2025_04.csv",
        ]
        self._create_test_files(files)

        # 検証実行
        with patch("scripts.validate_continuity.datetime") as mock_datetime:
            mock_datetime.now.return_value.year = 2025
            mock_datetime.now.return_value.month = 4

            report = self.validator.validate_data_type("sentinel_monthly_age", 2025, 2025)

        # 検証
        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.missing_periods), 1)
        self.assertEqual(report.missing_periods[0]["year"], 2025)
        self.assertEqual(report.missing_periods[0]["period"], 3)
        self.assertEqual(report.missing_periods[0]["type"], "monthly")

    def test_validate_week_53(self):
        """53週がある年の検証"""
        # 2020年は53週まである
        # 第50～53週のファイルを作成
        files = [f"sentinel_weekly_gender_2020_{week:02d}.csv" for week in range(50, 54)]
        self._create_test_files(files)

        # 検証実行（第50週から第53週のみを対象）
        with patch("scripts.validate_continuity.datetime") as mock_datetime:
            mock_datetime.now.return_value.year = 2021
            mock_datetime.now.return_value.isocalendar.return_value = (2021, 1, 1)

            # _get_weeks_in_yearが53を返すようにモック
            with patch.object(self.validator, "_get_weeks_in_year", return_value=53):
                # _generate_expected_periodsを呼び出して期待値を確認
                expected = self.validator._generate_expected_periods(
                    "sentinel_weekly_gender", 2020, 2020, is_monthly=False
                )

                # 53週が含まれることを確認
                self.assertIn((2020, 53), expected)

    def test_validate_all(self):
        """全データタイプの検証"""
        # いくつかのデータタイプのファイルを作成
        files = [
            "sentinel_weekly_gender_2025_01.csv",
            "sentinel_weekly_age_2025_01.csv",
            "notifiable_weekly_2025_01.csv",
            "sentinel_monthly_age_2025_01.csv",
        ]
        self._create_test_files(files)

        # 検証実行
        with patch("scripts.validate_continuity.datetime") as mock_datetime:
            mock_datetime.now.return_value.year = 2025
            mock_datetime.now.return_value.month = 1
            mock_datetime.now.return_value.isocalendar.return_value = (2025, 1, 1)

            reports = self.validator.validate_all(2025, 2025)

        # 検証
        self.assertIn("sentinel_weekly_gender", reports)
        self.assertIn("sentinel_monthly_age", reports)
        self.assertIn("notifiable_weekly", reports)

    def test_generate_json_report(self):
        """JSONレポート生成のテスト"""
        # レポートを作成
        report = ContinuityReport(
            data_type="test_data",
            start_year=2025,
            end_year=2025,
            expected_count=10,
            actual_count=8,
            missing_periods=[
                {"year": 2025, "period": 3, "type": "weekly", "filename": "test_2025_03.csv"},
                {"year": 2025, "period": 5, "type": "weekly", "filename": "test_2025_05.csv"},
            ],
            is_valid=False,
        )

        reports = {"test_data": report}

        # JSONレポート生成
        json_output = self.validator.generate_report(reports, "json")
        data = json.loads(json_output)

        # 検証
        self.assertIn("test_data", data)
        self.assertEqual(data["test_data"]["missing_count"], 2)
        self.assertFalse(data["test_data"]["is_valid"])

    def test_generate_text_report(self):
        """テキストレポート生成のテスト"""
        report = ContinuityReport(
            data_type="test_data",
            start_year=2025,
            end_year=2025,
            expected_count=10,
            actual_count=10,
            missing_periods=[],
            is_valid=True,
        )

        reports = {"test_data": report}

        # テキストレポート生成
        text_output = self.validator.generate_report(reports, "text")

        # 検証
        self.assertIn("データ連続性検証レポート", text_output)
        self.assertIn("✅ 正常", text_output)
        self.assertIn("test_data", text_output)

    def test_generate_markdown_report(self):
        """Markdownレポート生成のテスト"""
        report1 = ContinuityReport(
            data_type="weekly_data",
            start_year=2025,
            end_year=2025,
            expected_count=10,
            actual_count=9,
            missing_periods=[{"year": 2025, "period": 5, "type": "weekly", "filename": "weekly_2025_05.csv"}],
            is_valid=False,
        )

        report2 = ContinuityReport(
            data_type="monthly_data",
            start_year=2025,
            end_year=2025,
            expected_count=12,
            actual_count=12,
            missing_periods=[],
            is_valid=True,
        )

        reports = {"weekly_data": report1, "monthly_data": report2}

        # Markdownレポート生成
        md_output = self.validator.generate_report(reports, "markdown")

        # 検証
        self.assertIn("# データ連続性検証レポート", md_output)
        self.assertIn("| weekly_data", md_output)
        self.assertIn("| monthly_data", md_output)
        self.assertIn("✅", md_output)  # 正常データ
        self.assertIn("❌", md_output)  # 欠損データ

    def test_empty_directory(self):
        """空のディレクトリでの検証"""
        report = self.validator.validate_data_type("sentinel_weekly_gender", 2025, 2025)

        # 検証
        self.assertFalse(report.is_valid)
        self.assertEqual(report.actual_count, 0)
        self.assertIn("データファイルが見つかりません", report.error_messages)

    def test_invalid_filename_format(self):
        """不正なファイル名形式の処理"""
        # 不正な形式のファイルを作成
        files = [
            "invalid_format.csv",
            "sentinel_weekly_gender.csv",  # 年と期間がない
            "sentinel_weekly_gender_invalid_01.csv",  # 年が数値でない
        ]
        self._create_test_files(files)

        # 正しい形式のファイルも1つ作成
        self._create_test_files(["sentinel_weekly_gender_2025_01.csv"])

        # 検証実行
        with patch("scripts.validate_continuity.datetime") as mock_datetime:
            mock_datetime.now.return_value.year = 2025
            mock_datetime.now.return_value.isocalendar.return_value = (2025, 1, 1)

            report = self.validator.validate_data_type("sentinel_weekly_gender", 2025, 2025)

        # 検証（正しい形式の1ファイルのみが認識される）
        self.assertEqual(report.actual_count, 1)


if __name__ == "__main__":
    unittest.main()
