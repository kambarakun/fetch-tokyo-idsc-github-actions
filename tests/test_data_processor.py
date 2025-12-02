"""データ処理のユニットテスト"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.processors.data_processor import DataProcessor


class TestDataProcessor(unittest.TestCase):
    """DataProcessorのテスト"""

    def setUp(self):
        # 一時ディレクトリを作成
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir)

        # data/構造を作成
        self.data_dir = self.base_path / "data"
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.processor = DataProcessor(self.data_dir)

    def tearDown(self):
        # 一時ディレクトリを削除
        if self.base_path.exists():
            shutil.rmtree(self.base_path)

    def test_process_notifiable_file(self):
        """全数報告データ処理のテスト"""
        # Shift_JISテストファイルを作成
        test_file = self.raw_dir / "notifiable_weekly_2025_01.csv"
        test_content = """集計期間開始週,"2025年1週"
集計期間終了週,"2025年1週"
疾病名,報告数
インフルエンザ,100
結核,5
"""
        test_file.write_text(test_content, encoding="shift_jis")

        # 処理実行
        result = self.processor.process_file(test_file)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.output_files)
        self.assertEqual(len(result.output_files), 1)

        # 出力ファイルの確認
        output_file = self.data_dir / "processed" / "normalized_notifiable_weekly_2025_01.csv"
        self.assertTrue(output_file.exists())

        # 内容確認（メタデータ行が除外されているか）
        output_content = output_file.read_text(encoding="utf-8")
        self.assertIn("疾病名", output_content)
        self.assertIn("インフルエンザ", output_content)
        self.assertNotIn("集計期間", output_content)

    def test_process_sentinel_with_gender_sections(self):
        """性別セクション付き定点監視データ処理のテスト"""
        # Shift_JISテストファイルを作成
        test_file = self.raw_dir / "sentinel_weekly_age_2025_01.csv"
        test_content = """定点報告疾患,週報告分
性別,"男性"
年齢区分,インフルエンザ,RSウイルス
0歳,10,5
1-4歳,20,8
性別,"女性"
年齢区分,インフルエンザ,RSウイルス
0歳,12,6
1-4歳,18,7
性別,"男女合計"
年齢区分,インフルエンザ,RSウイルス
0歳,22,11
1-4歳,38,15
"""
        test_file.write_text(test_content, encoding="shift_jis")

        # 処理実行
        result = self.processor.process_file(test_file)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.output_files)
        self.assertEqual(len(result.output_files), 3)  # male, female, total

        # 各性別ファイルの確認
        male_file = self.data_dir / "processed" / "normalized_sentinel_weekly_age_male_2025_01.csv"
        female_file = self.data_dir / "processed" / "normalized_sentinel_weekly_age_female_2025_01.csv"
        total_file = self.data_dir / "processed" / "normalized_sentinel_weekly_age_total_2025_01.csv"

        self.assertTrue(male_file.exists())
        self.assertTrue(female_file.exists())
        self.assertTrue(total_file.exists())

        # 内容確認
        male_content = male_file.read_text(encoding="utf-8")
        self.assertIn("年齢区分", male_content)
        self.assertIn("インフルエンザ", male_content)

    def test_process_sentinel_simple(self):
        """性別セクションなし定点監視データ処理のテスト"""
        # Shift_JISテストファイルを作成（性別が列形式）
        test_file = self.raw_dir / "sentinel_weekly_gender_2025_01.csv"
        test_content = """定点報告疾患,週報告分
年齢区分,男性,女性,合計
0歳,10,12,22
1-4歳,20,18,38
"""
        test_file.write_text(test_content, encoding="shift_jis")

        # 処理実行
        result = self.processor.process_file(test_file)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.output_files)
        self.assertEqual(len(result.output_files), 1)

        # 出力ファイルの確認
        output_file = self.data_dir / "processed" / "normalized_sentinel_weekly_gender_2025_01.csv"
        self.assertTrue(output_file.exists())

    def test_process_all(self):
        """全ファイル処理のテスト"""
        # 複数のテストファイルを作成
        files = [
            ("notifiable_weekly_2025_01.csv", "疾病名,報告数\nインフルエンザ,100"),
            ("notifiable_weekly_2025_02.csv", "疾病名,報告数\n結核,5"),
        ]

        for filename, content in files:
            test_file = self.raw_dir / filename
            test_file.write_text(content, encoding="shift_jis")

        # 一括処理
        result = self.processor.process_all()

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["succeeded"], 2)
        self.assertEqual(result["failed"], 0)

        # 出力ファイルの確認
        processed_files = list((self.data_dir / "processed").glob("*.csv"))
        self.assertEqual(len(processed_files), 2)

    def test_extract_metadata_from_filename(self):
        """ファイル名からのメタデータ抽出テスト"""
        # 全数報告
        metadata = self.processor._extract_metadata_from_filename("notifiable_weekly_2025_01.csv")
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["category"], "notifiable")
        self.assertEqual(metadata["frequency"], "weekly")
        self.assertEqual(metadata["year"], "2025")
        self.assertEqual(metadata["period"], "01")

        # 定点監視
        metadata = self.processor._extract_metadata_from_filename("sentinel_weekly_gender_2025_01.csv")
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["category"], "sentinel")
        self.assertEqual(metadata["frequency"], "weekly")
        self.assertEqual(metadata["aggregation"], "gender")
        self.assertEqual(metadata["year"], "2025")
        self.assertEqual(metadata["period"], "01")

    def test_detect_gender_sections(self):
        """性別セクション検出のテスト"""
        lines = [
            "定点報告疾患,週報告分",
            '性別,"男性"',
            "データ1",
            "データ2",
            '性別,"女性"',
            "データ3",
            '性別,"男女合計"',
            "データ4",
        ]

        sections = self.processor._detect_gender_sections(lines)

        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0]["gender"], "男性")
        self.assertEqual(sections[1]["gender"], "女性")
        self.assertEqual(sections[2]["gender"], "男女合計")

    def test_get_gender_suffix(self):
        """性別サフィックス変換のテスト"""
        self.assertEqual(self.processor._get_gender_suffix("男性"), "male")
        self.assertEqual(self.processor._get_gender_suffix("女性"), "female")
        self.assertEqual(self.processor._get_gender_suffix("男女合計"), "total")
        self.assertEqual(self.processor._get_gender_suffix("不明"), "unknown")

    def test_processing_log(self):
        """処理ログのテスト"""
        # テストファイルを作成して処理
        test_file = self.raw_dir / "notifiable_weekly_2025_01.csv"
        test_content = "疾病名,報告数\nインフルエンザ,100"
        test_file.write_text(test_content, encoding="shift_jis")

        result = self.processor.process_file(test_file)
        self.assertTrue(result.success)

        # ログファイルの確認
        log_file = self.data_dir / "processed" / ".metadata" / "processing_log.json"
        self.assertTrue(log_file.exists())

        # ログ内容の確認
        import json

        with log_file.open("r", encoding="utf-8") as f:
            logs = json.load(f)

        self.assertIn("processing", logs)
        self.assertEqual(len(logs["processing"]), 1)
        self.assertEqual(logs["processing"][0]["success"], True)


if __name__ == "__main__":
    unittest.main()
