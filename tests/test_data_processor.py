"""データ処理のユニットテスト"""

import csv
import json
import shutil
import stat
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

        # 内容確認(メタデータ行が除外されているか)
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
        # Shift_JISテストファイルを作成(性別が列形式)
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

    def test_process_medical_district_skips_total(self):
        """medical_districtのtotalセクションがスキップされることを確認"""
        # Shift_JISテストファイルを作成
        test_file = self.raw_dir / "sentinel_weekly_medical_district_2025_01.csv"
        test_content = """定点報告疾患,週報告分
性別,"男性"
医療圏,インフルエンザ,RSウイルス
区中央部,10,5
性別,"女性"
医療圏,インフルエンザ,RSウイルス
区中央部,12,6
性別,"男女合計"
医療圏,インフルエンザ,RSウイルス
区中央部,22,11
"""
        test_file.write_text(test_content, encoding="shift_jis")

        # 処理実行
        result = self.processor.process_file(test_file)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.output_files)
        # medical_districtはmale, femaleのみ(totalはスキップ)
        self.assertEqual(len(result.output_files), 2)

        # male/femaleファイルは存在するがtotalは存在しない
        male_file = self.data_dir / "processed" / "normalized_sentinel_weekly_medical_district_male_2025_01.csv"
        female_file = self.data_dir / "processed" / "normalized_sentinel_weekly_medical_district_female_2025_01.csv"
        total_file = self.data_dir / "processed" / "normalized_sentinel_weekly_medical_district_total_2025_01.csv"

        self.assertTrue(male_file.exists())
        self.assertTrue(female_file.exists())
        self.assertFalse(total_file.exists())  # totalはスキップされる

    def test_medical_district_total_only_error(self):
        """medical_districtでtotalセクションのみ存在する異常データの検出"""
        # Arrange: totalセクションのみのmedical_districtデータ(異常)
        test_file = self.raw_dir / "sentinel_weekly_medical_district_2025_02.csv"
        test_content = """定点報告疾患,週報告分
性別,"男女合計"
医療圏,インフルエンザ,RSウイルス
区中央部,22,11
"""
        test_file.write_text(test_content, encoding="shift_jis")

        # Act: 処理実行(エラーログをキャプチャ)
        with self.assertLogs("src.processors.data_processor", level="ERROR") as log_context:
            result = self.processor.process_file(test_file)

        # Assert: 処理失敗と明示的なエラーメッセージ
        self.assertFalse(result.success)
        self.assertIn("必須の性別セクション(男性/女性)が存在しません", result.error)

        # エラーログが出力されていることを確認
        error_logs = [record.getMessage() for record in log_context.records if record.levelno >= 40]  # ERROR以上
        self.assertTrue(
            any("男性/女性セクションが存在しません" in log for log in error_logs),
            f"エラーログが見つかりません。実際のログ: {error_logs}",
        )

    def test_missing_total_section(self):
        """totalセクションが欠落している場合の動作を確認"""
        # maleとfemaleはあるが、totalセクションがないケース
        test_file = self.raw_dir / "sentinel_weekly_age_2025_02.csv"
        test_content = """定点報告疾患,週報告分
性別,"男性"
年齢区分,インフルエンザ,RSウイルス
0歳,10,5
性別,"女性"
年齢区分,インフルエンザ,RSウイルス
0歳,12,6
"""
        test_file.write_text(test_content, encoding="shift_jis")

        # 処理実行
        result = self.processor.process_file(test_file)

        self.assertTrue(result.success)
        # totalセクションがないので、male, femaleの2ファイルのみ生成される
        self.assertEqual(len(result.output_files), 2)

        # male/femaleファイルは存在するがtotalは存在しない
        male_file = self.data_dir / "processed" / "normalized_sentinel_weekly_age_male_2025_02.csv"
        female_file = self.data_dir / "processed" / "normalized_sentinel_weekly_age_female_2025_02.csv"
        total_file = self.data_dir / "processed" / "normalized_sentinel_weekly_age_total_2025_02.csv"

        self.assertTrue(male_file.exists())
        self.assertTrue(female_file.exists())
        self.assertFalse(total_file.exists())  # totalセクションがないため生成されない

    def test_empty_total_section_triggers_warning(self):
        """totalセクションが空(ヘッダーのみ)の場合に警告ログが出ることを確認"""
        # totalセクションはあるが、データ行がない(ヘッダーのみ)ケース
        # この場合、totalファイルは生成されるが空(ヘッダーのみ)となり、警告ログが出力される
        test_file = self.raw_dir / "sentinel_weekly_age_2025_03.csv"
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
"""
        test_file.write_text(test_content, encoding="shift_jis")

        # ログ出力をキャプチャするため、logging を使用

        # 処理実行
        with self.assertLogs("src.processors.data_processor", level="WARNING") as log_context:
            result = self.processor.process_file(test_file)

        self.assertTrue(result.success)
        # male, female, total の3ファイルが生成される
        self.assertEqual(len(result.output_files), 3)

        # totalファイルは存在するがヘッダーのみで空
        total_file = self.data_dir / "processed" / "normalized_sentinel_weekly_age_total_2025_03.csv"
        self.assertTrue(total_file.exists())

        # 警告ログが出力されていることを確認
        warning_logs = [record.message for record in log_context.records if record.levelname == "WARNING"]
        # 「totalセクションが空です」という警告が含まれることを確認
        self.assertTrue(
            any("totalセクションが空です" in log for log in warning_logs),
            f"警告ログが見つかりません。実際のログ: {warning_logs}",
        )

        # ファイル内容を確認(ヘッダーのみ)
        total_content = total_file.read_text(encoding="utf-8")
        lines = [line for line in total_content.strip().split("\n") if line]
        self.assertEqual(len(lines), 1)  # ヘッダー行のみ
        self.assertIn("年齢区分", lines[0])

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

    def test_processing_metadata(self):
        """処理メタデータのテスト (v1.1形式)"""
        # テストファイルを作成して処理
        test_file = self.raw_dir / "notifiable_weekly_2025_01.csv"
        test_content = "疾病名,報告数\nインフルエンザ,100"
        test_file.write_text(test_content, encoding="shift_jis")

        result = self.processor.process_file(test_file)
        self.assertTrue(result.success)

        # 出力ファイルの確認
        output_file = self.data_dir / "processed" / "normalized_notifiable_weekly_2025_01.csv"
        self.assertTrue(output_file.exists())

        # 個別メタデータファイルの確認
        metadata_file = self.data_dir / "processed" / ".metadata" / "normalized_notifiable_weekly_2025_01.json"
        self.assertTrue(metadata_file.exists())

        # メタデータ内容の確認
        with metadata_file.open("r", encoding="utf-8") as f:
            meta = json.load(f)

        # v1.1形式の検証
        self.assertEqual(meta["metadata_version"], "1.1.0")
        self.assertEqual(meta["profile"], "tokyo-idsc-processed")
        self.assertEqual(meta["filename"], "normalized_notifiable_weekly_2025_01.csv")
        self.assertEqual(meta["encoding"], "utf-8")
        self.assertIn("hash", meta)
        self.assertEqual(meta["hash"]["algorithm"], "sha256")
        self.assertIn("_process", meta)
        self.assertEqual(meta["_process"]["source_name"], "notifiable_weekly_2025_01")
        self.assertIn("source_hash", meta["_process"])
        self.assertIn("processing_time_seconds", meta["_process"])

    def test_parse_int_helper(self):
        """_parse_int ヘルパーメソッドのテスト"""
        # 通常の整数
        self.assertEqual(self.processor._parse_int("123"), 123)
        self.assertEqual(self.processor._parse_int("0"), 0)

        # 空文字列
        self.assertEqual(self.processor._parse_int(""), 0)
        self.assertEqual(self.processor._parse_int("   "), 0)

        # 負の数
        self.assertEqual(self.processor._parse_int("-5"), -5)

    def test_parse_int_with_asterisk_raises_value_error(self):
        """'*'などの非数値でValueErrorが発生することを確認

        東京都データでは'*'は「非該当」を意味する既知の仕様。
        _parse_intはValueErrorを投げ、呼び出し側で検証をスキップする。
        """
        # '*'(非該当)でValueErrorが発生
        with self.assertRaises(ValueError):
            self.processor._parse_int("*")

        # 警告ログも出力されることを確認
        with (
            self.assertLogs("src.processors.data_processor", level="WARNING") as log_context,
            self.assertRaises(ValueError),
        ):
            self.processor._parse_int("*")

        self.assertTrue(
            any("数値変換失敗" in log.message for log in log_context.records),
            f"警告ログが見つかりません: {[log.message for log in log_context.records]}",
        )

        # 他の非数値パターンもValueErrorになることを確認
        with self.assertRaises(ValueError):
            self.processor._parse_int("N/A")

        with self.assertRaises(ValueError):
            self.processor._parse_int("abc")

    def test_is_empty_data_file(self):
        """_is_empty_data_file のテスト"""
        # ヘッダーのみのファイル
        test_file = self.data_dir / "processed" / "empty_test.csv"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("年齢区分,インフルエンザ\n", encoding="utf-8")

        self.assertTrue(self.processor._is_empty_data_file(test_file))

        # データ行があるファイル
        test_file2 = self.data_dir / "processed" / "non_empty_test.csv"
        test_file2.write_text("年齢区分,インフルエンザ\n0歳,10\n", encoding="utf-8")

        self.assertFalse(self.processor._is_empty_data_file(test_file2))

    def test_verify_total_with_mismatch(self):
        """total 検証で不一致がある場合のテスト"""
        # テストファイルを作成
        male_file = self.data_dir / "processed" / "verify_male.csv"
        female_file = self.data_dir / "processed" / "verify_female.csv"
        total_file = self.data_dir / "processed" / "verify_total.csv"

        male_file.parent.mkdir(parents=True, exist_ok=True)

        # male=10, female=5 だが total=20(不一致)
        male_file.write_text("年齢区分,インフルエンザ\n0歳,10\n", encoding="utf-8")
        female_file.write_text("年齢区分,インフルエンザ\n0歳,5\n", encoding="utf-8")
        total_file.write_text("年齢区分,インフルエンザ\n0歳,20\n", encoding="utf-8")

        # 警告ログが出る(不一致検出)
        self.processor._verify_total_calculation(male_file, female_file, total_file)

        # エラーにはならず、警告のみ

    def test_verify_total_skips_ari_column(self):
        """ARIカラム(急性呼吸器感染症)の検証がスキップされることを確認

        東京都データでは、ARIは年齢グループ化されており、
        一部の年齢帯で'*'(非該当)が入る既知の仕様。
        このカラムは検証対象外としてスキップする。
        """
        male_file = self.data_dir / "processed" / "verify_ari_male.csv"
        female_file = self.data_dir / "processed" / "verify_ari_female.csv"
        total_file = self.data_dir / "processed" / "verify_ari_total.csv"

        male_file.parent.mkdir(parents=True, exist_ok=True)

        # ARIカラムに'*'を含むデータを作成
        # インフルエンザは正しいデータ(male + female = total)
        # ARIは'*'が含まれるため検証スキップされる
        male_file.write_text(
            "年齢区分,急性呼吸器感染症,インフルエンザ\n0歳,*,10\n1-4歳,100,20\n",
            encoding="utf-8",
        )
        female_file.write_text(
            "年齢区分,急性呼吸器感染症,インフルエンザ\n0歳,*,5\n1-4歳,90,15\n",
            encoding="utf-8",
        )
        total_file.write_text(
            "年齢区分,急性呼吸器感染症,インフルエンザ\n0歳,*,15\n1-4歳,190,35\n",
            encoding="utf-8",
        )

        # ARIカラムはスキップされるため、ValueErrorは発生せず正常終了
        # (インフルエンザカラムのみ検証される)
        with self.assertLogs("src.processors.data_processor", level="INFO") as log_context:
            self.processor._verify_total_calculation(male_file, female_file, total_file)

        # 検証OKのログが出力されることを確認
        info_logs = [log.message for log in log_context.records if log.levelname == "INFO"]
        self.assertTrue(
            any("total検証OK" in log for log in info_logs),
            f"検証OKログが見つかりません: {info_logs}",
        )

    def test_verify_total_skips_ari_column_with_inconsistent_data(self):
        """ARIカラムの不整合データが無視されることを確認

        ARIカラムは検証スキップされるため、
        male + female != total でもエラーにならない。
        """
        male_file = self.data_dir / "processed" / "verify_ari_skip_male.csv"
        female_file = self.data_dir / "processed" / "verify_ari_skip_female.csv"
        total_file = self.data_dir / "processed" / "verify_ari_skip_total.csv"

        male_file.parent.mkdir(parents=True, exist_ok=True)

        # ARIカラムは意図的に不整合(100 + 90 != 999)だが、スキップされるので問題なし
        # インフルエンザは正しいデータ
        male_file.write_text(
            "年齢区分,急性呼吸器感染症,インフルエンザ\n0歳,100,10\n",
            encoding="utf-8",
        )
        female_file.write_text(
            "年齢区分,急性呼吸器感染症,インフルエンザ\n0歳,90,5\n",
            encoding="utf-8",
        )
        total_file.write_text(
            "年齢区分,急性呼吸器感染症,インフルエンザ\n0歳,999,15\n",  # ARIは不整合
            encoding="utf-8",
        )

        # ARIカラムはスキップされるため、不整合があっても検証OKとなる
        with self.assertLogs("src.processors.data_processor", level="INFO") as log_context:
            self.processor._verify_total_calculation(male_file, female_file, total_file)

        info_logs = [log.message for log in log_context.records if log.levelname == "INFO"]
        self.assertTrue(
            any("total検証OK" in log for log in info_logs),
            f"検証OKログが見つかりません(ARIカラムはスキップされるべき): {info_logs}",
        )

    def test_process_file_with_encoding_error(self):
        """エンコーディングエラー時の処理テスト"""
        # UTF-8で書かれたファイル(Shift_JISとして読むとエラー)
        test_file = self.raw_dir / "broken_encoding.csv"
        test_file.write_text("これはUTF-8です\n特殊文字: 🎉", encoding="utf-8")

        # エラーハンドリングで errors="replace" が使われるため、処理は継続される
        result = self.processor.process_file(test_file)

        # ファイル名が不正なのでメタデータ抽出に失敗するが、エラーではなく失敗として記録される
        self.assertFalse(result.success)

    def test_extract_metadata_with_multi_word_aggregation(self):
        """複数単語の集計軸のメタデータ抽出テスト"""
        # health_center
        metadata = self.processor._extract_metadata_from_filename("sentinel_weekly_health_center_2025_01.csv")
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["aggregation"], "health_center")

        # medical_district
        metadata = self.processor._extract_metadata_from_filename("sentinel_weekly_medical_district_2025_01.csv")
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["aggregation"], "medical_district")

    def test_extract_section_data_with_no_header(self):
        """ヘッダー行が見つからない場合のセクションデータ抽出テスト"""
        lines = [
            '性別,"男性"',
            "データ1",
            "データ2",
        ]
        section = {"gender": "男性", "start_line": 0}

        # ヘッダー行(疾病キーワード2個以上)が見つからない場合、空リストが返る
        data = self.processor._extract_section_data(lines, section)

        self.assertEqual(data, [])

    def test_cross_dataset_consistency_check_success(self):
        """クロスデータセット整合性チェック (正常系) のテスト"""
        # 処理済みディレクトリに2つの集計軸のファイルを作成
        processed_dir = self.processor.processed_dir

        # 同じデータ (合計が一致) を持つファイルを作成
        test_data = [
            ["合計", "100", "50", "30", "20"],
            ["区分A", "50", "25", "15", "10"],
            ["区分B", "50", "25", "15", "10"],
        ]

        # age と health_center のみ(medical_districtは除外)
        for aggregation in ["age", "health_center"]:
            file_path = processed_dir / f"normalized_sentinel_weekly_{aggregation}_total_2025_01.csv"
            with file_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(test_data)

        # 整合性チェックを実行 (正常系: エラーなし)
        # 例外が発生しないことを確認
        try:
            self.processor._verify_cross_dataset_consistency()
        except Exception as e:
            self.fail(f"整合性チェックで予期しない例外が発生しました: {e}")

    def test_cross_dataset_consistency_check_with_mismatch(self):
        """クロスデータセット整合性チェック (不一致あり) のテスト"""
        processed_dir = self.processor.processed_dir

        # 異なるデータを持つファイルを作成
        test_data_age = [
            ["合計", "100", "50", "30", "20"],
            ["区分A", "50", "25", "15", "10"],
        ]

        test_data_hc = [
            ["合計", "99", "49", "30", "20"],  # age と不一致
            ["区分A", "49", "24", "15", "10"],  # age と不一致
        ]

        # ageファイル
        file_path = processed_dir / "normalized_sentinel_weekly_age_total_2025_01.csv"
        with file_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(test_data_age)

        # health_centerファイル(age と不一致)
        file_path = processed_dir / "normalized_sentinel_weekly_health_center_total_2025_01.csv"
        with file_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(test_data_hc)

        # 整合性チェックを実行 (警告ログが出るが例外は発生しない)
        # 警告のみで処理継続することを確認
        try:
            self.processor._verify_cross_dataset_consistency()
        except Exception as e:
            self.fail(f"警告のみ期待だが予期しない例外が発生しました: {e}")

    def test_collect_periods_for_verification(self):
        """整合性チェック対象の期間収集のテスト"""
        processed_dir = self.processor.processed_dir

        # 2つの集計軸のファイルを作成(age, health_center)
        for aggregation in ["age", "health_center"]:
            file_path = processed_dir / f"normalized_sentinel_weekly_{aggregation}_total_2025_01.csv"
            file_path.write_text("合計,100\n", encoding="utf-8")

        # 1つしかない場合 (揃っていない)
        file_path = processed_dir / "normalized_sentinel_monthly_age_total_2025_12.csv"
        file_path.write_text("合計,50\n", encoding="utf-8")

        # 期間を収集
        periods = self.processor._collect_periods_for_verification()

        # 2つ揃っている期間のみ返されることを確認
        self.assertIn("weekly_2025_01", periods)
        self.assertEqual(len(periods["weekly_2025_01"]), 2)
        self.assertNotIn("monthly_2025_12", periods)

    def test_extract_total_row(self):
        """合計行抽出のテスト"""
        processed_dir = self.processor.processed_dir

        # テストファイルを作成
        test_file = processed_dir / "test_total.csv"
        test_data = [
            ["区分", "値1", "値2"],
            ["区分A", "10", "20"],
            ["合計", "100", "200"],
        ]

        with test_file.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(test_data)

        # 合計行を抽出
        total_row = self.processor._extract_total_row(test_file)

        self.assertIsNotNone(total_row)
        self.assertEqual(total_row[0], "合計")
        self.assertEqual(total_row[1], "100")
        self.assertEqual(total_row[2], "200")

    def test_extract_total_row_not_found(self):
        """合計行が見つからない場合のテスト"""
        processed_dir = self.processor.processed_dir

        # 合計行がないテストファイルを作成
        test_file = processed_dir / "test_no_total.csv"
        test_data = [["区分", "値1", "値2"], ["区分A", "10", "20"]]

        with test_file.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(test_data)

        # 合計行を抽出 (見つからない)
        total_row = self.processor._extract_total_row(test_file)

        self.assertIsNone(total_row)

    def test_log_processing_with_missing_output_file(self):
        """出力ファイルが存在しない場合のメタデータ処理テスト"""
        # Arrange: テストファイルを作成
        source = self.raw_dir / "test_source.csv"
        source.write_text("test,data\n1,2\n", encoding="shift_jis")

        # 存在しない出力ファイルを指定
        non_existent_output = self.processor.processed_dir / "non_existent.csv"

        metadata = {
            "category": "sentinel",
            "aggregation": "gender",
            "frequency": "weekly",
            "year": 2025,
            "period": 1,
        }

        # Act & Assert: 警告ログが出力され、例外が発生しないことを確認
        with self.assertLogs("src.processors.data_processor", level="WARNING") as log_context:
            # メタデータ処理は例外を発生させずに完了すべき
            self.processor._log_processing(
                source=source,
                outputs=[non_existent_output],
                metadata=metadata,
                processing_time=1.0,
                gender_info=None,
            )

        # 警告ログが出力されたことを確認
        self.assertTrue(
            any("出力ファイルが存在しません" in message for message in log_context.output),
            "Missing file warning should be logged",
        )

    def test_log_processing_with_metadata_write_error(self):
        """メタデータ書き込み失敗時の処理テスト"""
        # Arrange: ソースファイルと出力ファイルを作成
        source = self.raw_dir / "test_source.csv"
        source.write_text("test,data\n1,2\n", encoding="shift_jis")

        output = self.processor.processed_dir / "test_output.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("result,data\n3,4\n", encoding="utf-8")

        metadata = {
            "category": "sentinel",
            "aggregation": "gender",
            "frequency": "weekly",
            "year": 2025,
            "period": 1,
        }

        # メタデータディレクトリを読み取り専用にして書き込みエラーを発生させる
        metadata_dir = self.processor.processed_dir / ".metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)

        # ディレクトリを読み取り専用に設定
        original_mode = metadata_dir.stat().st_mode
        try:
            metadata_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

            # Act & Assert: 警告ログが出力され、例外が発生しないことを確認
            with self.assertLogs("src.processors.data_processor", level="WARNING") as log_context:
                # メタデータ書き込み失敗でも処理は完了すべき
                self.processor._log_processing(
                    source=source,
                    outputs=[output],
                    metadata=metadata,
                    processing_time=1.0,
                    gender_info=None,
                )

            # 警告ログが出力されたことを確認
            self.assertTrue(
                any("メタデータ保存失敗" in message for message in log_context.output),
                "Metadata write error should be logged as warning",
            )
        finally:
            # パーミッションを元に戻す
            metadata_dir.chmod(original_mode)

    def test_log_processing_creates_metadata_directory(self):
        """メタデータディレクトリが自動作成されることを確認"""
        # Arrange: ソースファイルと出力ファイルを作成
        source = self.raw_dir / "test_source.csv"
        source.write_text("test,data\n1,2\n", encoding="shift_jis")

        output = self.processor.processed_dir / "test_output.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("result,data\n3,4\n", encoding="utf-8")

        metadata = {
            "category": "sentinel",
            "aggregation": "gender",
            "frequency": "weekly",
            "year": 2025,
            "period": 1,
        }

        # メタデータディレクトリが存在しないことを確認
        metadata_dir = self.processor.processed_dir / ".metadata"
        if metadata_dir.exists():
            shutil.rmtree(metadata_dir)

        self.assertFalse(metadata_dir.exists())

        # Act: メタデータ処理を実行
        self.processor._log_processing(
            source=source,
            outputs=[output],
            metadata=metadata,
            processing_time=1.0,
            gender_info=None,
        )

        # Assert: メタデータディレクトリが作成されたことを確認
        self.assertTrue(metadata_dir.exists())
        self.assertTrue(metadata_dir.is_dir())

        # メタデータファイルが作成されたことを確認
        metadata_file = metadata_dir / f"{output.stem}.json"
        self.assertTrue(metadata_file.exists())


if __name__ == "__main__":
    unittest.main()
