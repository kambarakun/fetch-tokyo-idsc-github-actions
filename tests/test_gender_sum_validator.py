"""Tests for gender_sum_validator module."""

from pathlib import Path

import pytest

from src.validators.gender_sum_validator import GenderSumValidator


class TestGenderSumValidator:
    """Tests for GenderSumValidator class."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        """Setup test environment."""
        self.raw_dir = tmp_path / "raw"
        self.raw_dir.mkdir()
        self.validator = GenderSumValidator(self.raw_dir)

    def test_validate_returns_none_for_non_gender_split_data(self) -> None:
        """性別分割されていないデータ型にはNoneを返す."""
        result = self.validator.validate("sentinel_weekly_gender_2024_01.csv", "sentinel_weekly_gender")
        assert result is None

    def test_validate_returns_none_if_source_not_exists(self) -> None:
        """元ファイルが存在しない場合はNoneを返す."""
        result = self.validator.validate("nonexistent.csv", "sentinel_weekly_age")
        assert result is None

    def test_validate_medical_district(self, tmp_path: Path) -> None:
        """医療圏データの検証."""
        # テスト用のShift_JIS CSVファイルを作成
        # CSVリーダーが正しく解釈できる形式にする
        csv_content = """"性別","男性"
"","","インフルエンザ"
"区中央部","10","5"

"性別","女性"
"","","インフルエンザ"
"区中央部","5","3"

"性別","男女合計"
"","","インフルエンザ"
"区中央部","15","8"
"""
        source_file = self.raw_dir / "sentinel_weekly_medical_district_2024_01.csv"
        source_file.write_bytes(csv_content.encode("shift_jis"))

        result = self.validator.validate(
            "sentinel_weekly_medical_district_2024_01.csv",
            "sentinel_weekly_medical_district",
        )

        assert result is not None
        assert result["check_type"] == "gender_sum_consistency"
        assert result["validation_status"] == "completed"
        assert result["details"]["source_file"] == "sentinel_weekly_medical_district_2024_01.csv"

    def test_validate_detects_mismatch(self, tmp_path: Path) -> None:
        """male + female != total の不整合を検出."""
        # 不整合があるデータ (total = male, femaleが反映されていない)
        csv_content = """性別,男性
,"",インフルエンザ
島しょ,3,10

性別,女性
,"",インフルエンザ
島しょ,3,5

性別,男女合計
,"",インフルエンザ
島しょ,3,15
"""
        source_file = self.raw_dir / "sentinel_weekly_medical_district_2024_06.csv"
        source_file.write_bytes(csv_content.encode("shift_jis"))

        result = self.validator.validate(
            "sentinel_weekly_medical_district_2024_06.csv",
            "sentinel_weekly_medical_district",
        )

        assert result is not None
        assert result["validation_status"] == "completed"
        assert result["details"]["affected_count"] == 1
        assert result["details"]["truncated"] is False
        assert len(result["details"]["affected_locations"]) == 1

        error = result["details"]["affected_locations"][0]
        assert error["location"] == "島しょ"
        assert error["column"] == "インフルエンザ"
        assert error["male"] == 3
        assert error["female"] == 3
        assert error["total"] == 3
        assert error["expected"] == 6

    def test_validate_health_center(self, tmp_path: Path) -> None:
        """保健所データの検証."""
        csv_content = """性別,男性
,"",インフルエンザ
千代田,5,10

性別,女性
,"",インフルエンザ
千代田,3,8

性別,男女合計
,"",インフルエンザ
千代田,8,18
"""
        source_file = self.raw_dir / "sentinel_weekly_health_center_2024_01.csv"
        source_file.write_bytes(csv_content.encode("shift_jis"))

        result = self.validator.validate(
            "sentinel_weekly_health_center_2024_01.csv",
            "sentinel_weekly_health_center",
        )

        assert result is not None
        assert result["check_type"] == "gender_sum_consistency"
        assert result["validation_status"] == "completed"
        assert result["details"]["affected_count"] == 0

    def test_validate_age(self, tmp_path: Path) -> None:
        """年齢層データの検証."""
        csv_content = """性別,男性
,"",インフルエンザ
0～4歳,2,5

性別,女性
,"",インフルエンザ
0～4歳,3,4

性別,男女合計
,"",インフルエンザ
0～4歳,5,9
"""
        source_file = self.raw_dir / "sentinel_weekly_age_2024_01.csv"
        source_file.write_bytes(csv_content.encode("shift_jis"))

        result = self.validator.validate(
            "sentinel_weekly_age_2024_01.csv",
            "sentinel_weekly_age",
        )

        assert result is not None
        assert result["check_type"] == "gender_sum_consistency"
        assert result["validation_status"] == "completed"
        assert result["details"]["affected_count"] == 0

    def test_validate_truncates_errors_at_10(self, tmp_path: Path) -> None:
        """エラーが10件を超える場合、truncatedフラグが立つ."""
        # 15箇所の不整合を含むデータを作成
        csv_lines = [
            '性別,男性\n,"",疾病A,疾病B,疾病C,疾病D,疾病E,疾病F,疾病G,疾病H,疾病I,疾病J,疾病K,疾病L,疾病M,疾病N,疾病O\n'
        ]
        csv_lines.append("地域1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域2,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域3,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域4,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域5,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域6,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域7,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域8,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域9,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域10,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域11,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域12,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append(
            "\n性別,女性\n,"
            ",疾病A,疾病B,疾病C,疾病D,疾病E,疾病F,疾病G,疾病H,疾病I,疾病J,疾病K,疾病L,疾病M,疾病N,疾病O\n"
        )
        csv_lines.append("地域1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域2,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域3,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域4,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域5,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域6,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域7,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域8,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域9,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域10,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域11,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        csv_lines.append("地域12,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")
        # totalは全て0 (不整合)
        csv_lines.append(
            "\n性別,男女合計\n,"
            ",疾病A,疾病B,疾病C,疾病D,疾病E,疾病F,疾病G,疾病H,疾病I,疾病J,疾病K,疾病L,疾病M,疾病N,疾病O\n"
        )
        csv_lines.append("地域1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域4,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域5,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域6,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域7,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域8,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域10,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域11,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")
        csv_lines.append("地域12,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n")

        csv_content = "".join(csv_lines)
        source_file = self.raw_dir / "sentinel_weekly_medical_district_2024_15.csv"
        source_file.write_bytes(csv_content.encode("shift_jis"))

        result = self.validator.validate(
            "sentinel_weekly_medical_district_2024_15.csv",
            "sentinel_weekly_medical_district",
        )

        assert result is not None
        assert result["details"]["affected_count"] == 12  # 12地域
        assert result["details"]["truncated"] is True
        assert len(result["details"]["affected_locations"]) == 10  # 最大10件

    def test_validate_skips_empty_columns(self, tmp_path: Path) -> None:
        """空文字列の列 (定点数など) はスキップする."""
        csv_content = """性別,男性
,"",インフルエンザ,定点数
区中央部,10,
区西部,5,

性別,女性
,"",インフルエンザ,定点数
区中央部,5,
区西部,3,

性別,男女合計
,"",インフルエンザ,定点数
区中央部,15,10
区西部,8,5
"""
        source_file = self.raw_dir / "sentinel_weekly_medical_district_2024_02.csv"
        source_file.write_bytes(csv_content.encode("shift_jis"))

        result = self.validator.validate(
            "sentinel_weekly_medical_district_2024_02.csv",
            "sentinel_weekly_medical_district",
        )

        assert result is not None
        # 定点数列は空なのでスキップされ、エラーなし
        assert result["details"]["affected_count"] == 0

    def test_build_result_no_errors(self) -> None:
        """エラーなしの場合の結果構築."""
        result = self.validator._build_result("test.csv", 10, 0, [], "record(s)")

        assert result["check_type"] == "gender_sum_consistency"
        assert result["validation_status"] == "completed"
        assert "No mismatch observed" in result["message"]
        assert result["details"]["affected_count"] == 0
        assert result["details"]["truncated"] is False
        assert result["details"]["affected_locations"] == []

    def test_build_result_with_errors(self) -> None:
        """エラーありの場合の結果構築."""
        errors = [
            {"location": "地域1", "column": "疾病A", "row_index": 5, "male": 1, "female": 1, "total": 0, "expected": 2},
            {"location": "地域2", "column": "疾病B", "row_index": 6, "male": 2, "female": 2, "total": 0, "expected": 4},
        ]
        result = self.validator._build_result("test.csv", 10, 2, errors, "record(s)")

        assert result["check_type"] == "gender_sum_consistency"
        assert result["validation_status"] == "completed"
        assert "Observed mismatch" in result["message"]
        assert result["details"]["affected_count"] == 2
        assert result["details"]["truncated"] is False
        assert len(result["details"]["affected_locations"]) == 2

    def test_no_validation_result(self) -> None:
        """検証できない場合の結果."""
        result = self.validator._no_validation_result("test.csv")

        assert result["check_type"] == "gender_sum_consistency"
        assert result["validation_status"] == "skipped"
        assert "skipped" in result["message"].lower()
        assert result["details"]["affected_count"] == 0
        assert result["details"]["truncated"] is False
        assert result["details"]["affected_locations"] == []
