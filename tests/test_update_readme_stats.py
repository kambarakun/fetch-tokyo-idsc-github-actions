"""
tests/test_update_readme_stats.py - README統計情報更新機能のテスト

主要関数のテストケース:
- _has_53_weeks: ISO 8601週番号の53週判定
- get_metadata_stats: メタデータ統計情報取得
- format_data_type_table: データ種別表形式整形
- _detect_missing_periods: 欠損期間検出
- update_readme: README更新
"""

import json
import os
from pathlib import Path

from scripts.update_readme_stats import (
    _detect_missing_periods,
    _has_53_weeks,
    format_data_type_table,
    get_metadata_stats,
    update_readme,
)


class TestHas53Weeks:
    """_has_53_weeks()関数のテスト"""

    def test_2020_has_53_weeks(self):
        """2020年は53週を持つ (木曜日で始まる閏年)"""
        assert _has_53_weeks(2020) is True

    def test_2015_has_53_weeks(self):
        """2015年は53週を持つ (木曜日で始まる年)"""
        assert _has_53_weeks(2015) is True

    def test_2021_has_52_weeks(self):
        """2021年は52週のみ"""
        assert _has_53_weeks(2021) is False

    def test_2023_has_52_weeks(self):
        """2023年は52週のみ"""
        assert _has_53_weeks(2023) is False


class TestDetectMissingPeriods:
    """_detect_missing_periods()関数のテスト"""

    def test_no_missing_weekly_data(self):
        """週次データに欠損がない場合"""
        # 2025年第1週から第3週まで連続
        periods = [(2025, 1), (2025, 2), (2025, 3)]
        result = _detect_missing_periods("sentinel_weekly_gender", periods)
        assert result == "なし"

    def test_missing_weekly_data(self):
        """週次データに欠損がある場合"""
        # 2025年第1週と第3週 (第2週が欠損)
        periods = [(2025, 1), (2025, 3)]
        result = _detect_missing_periods("sentinel_weekly_gender", periods)
        assert "2025年第2週" in result

    def test_many_missing_periods(self):
        """多数の欠損がある場合 (件数のみ表示)"""
        # 2025年第1週と第20週 (第2週から第19週まで18週分が欠損)
        periods = [(2025, 1), (2025, 20)]
        result = _detect_missing_periods("sentinel_weekly_gender", periods)
        # 欠損が5件より多い場合は件数のみ表示される
        assert result == "18件"

    def test_no_missing_monthly_data(self):
        """月次データに欠損がない場合"""
        # 2025年1月から3月まで連続
        periods = [(2025, 1), (2025, 2), (2025, 3)]
        result = _detect_missing_periods("sentinel_monthly_age", periods)
        assert result == "なし"

    def test_empty_periods(self):
        """期間データが空の場合"""
        periods = []
        result = _detect_missing_periods("sentinel_weekly_gender", periods)
        assert result == "N/A"


class TestGetMetadataStats:
    """get_metadata_stats()関数のテスト"""

    def test_no_metadata_directory(self, tmp_path):
        """メタデータディレクトリが存在しない場合"""
        # 一時ディレクトリを作業ディレクトリとして使用
        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            result = get_metadata_stats()
            assert result["total_files"] == 0
            assert result["date_range"] == "データなし"
        finally:
            os.chdir(original_cwd)

    def test_valid_metadata_files(self, tmp_path):
        """正常なメタデータファイルを読み込む"""
        # メタデータディレクトリを作成
        metadata_dir = tmp_path / "data" / "raw" / ".metadata"
        metadata_dir.mkdir(parents=True)

        # テストデータを作成
        metadata1 = {
            "filename": "sentinel_weekly_gender_2025_01.csv",
            "data_type": "sentinel_weekly_gender",
            "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
            "created": "2025-01-01T00:00:00Z",
            "modified": "2025-01-01T00:00:00Z",
        }

        metadata2 = {
            "filename": "sentinel_weekly_gender_2025_02.csv",
            "data_type": "sentinel_weekly_gender",
            "temporal": {"year": 2025, "period": 2, "period_type": "weekly"},
            "created": "2025-01-08T00:00:00Z",
            "modified": "2025-01-08T00:00:00Z",
        }

        # JSONファイルとして保存
        (metadata_dir / "sentinel_weekly_gender_2025_01.json").write_text(json.dumps(metadata1), encoding="utf-8")
        (metadata_dir / "sentinel_weekly_gender_2025_02.json").write_text(json.dumps(metadata2), encoding="utf-8")

        # テスト実行
        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            result = get_metadata_stats()
            assert result["total_files"] == 2
            assert "sentinel_weekly_gender" in result["data_types"]
            assert result["data_types"]["sentinel_weekly_gender"] == 2
            assert 2025 in result["years"]
        finally:
            os.chdir(original_cwd)

    def test_invalid_metadata_file(self, tmp_path, capsys):
        """不正なメタデータファイルはスキップされる"""
        metadata_dir = tmp_path / "data" / "raw" / ".metadata"
        metadata_dir.mkdir(parents=True)

        # 不正なJSONファイル
        (metadata_dir / "invalid.json").write_text("{ invalid json", encoding="utf-8")

        # 正常なファイルも1つ追加
        valid_metadata = {
            "filename": "sentinel_weekly_gender_2025_01.csv",
            "data_type": "sentinel_weekly_gender",
            "temporal": {"year": 2025, "period": 1, "period_type": "weekly"},
            "created": "2025-01-01T00:00:00Z",
            "modified": "2025-01-01T00:00:00Z",
        }
        (metadata_dir / "valid.json").write_text(json.dumps(valid_metadata), encoding="utf-8")

        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            result = get_metadata_stats()
            # 正常なファイルのみカウントされる
            assert result["total_files"] == 1
            # 警告メッセージが出力される
            captured = capsys.readouterr()
            assert "警告" in captured.out or "⚠️" in captured.out
        finally:
            os.chdir(original_cwd)


class TestFormatDataTypeTable:
    """format_data_type_table()関数のテスト"""

    def test_format_with_data(self):
        """データ種別の表形式整形"""
        data_types = {"sentinel_weekly_gender": 10, "notifiable_weekly": 5}
        data_type_periods = {
            "sentinel_weekly_gender": [(2025, 1), (2025, 10)],
            "notifiable_weekly": [(2025, 1), (2025, 5)],
        }

        result = format_data_type_table(data_types, data_type_periods)

        # Markdown表のヘッダーが含まれる
        assert "データ種別" in result
        assert "件数" in result
        assert "データ期間" in result
        assert "欠損" in result

        # データ種別の日本語名が含まれる
        assert "定点週次・性別" in result
        assert "全数週次" in result

        # 件数が含まれる
        assert "10件" in result
        assert "5件" in result

    def test_format_empty_data(self):
        """空のデータ種別の整形"""
        data_types = {}
        data_type_periods = {}

        result = format_data_type_table(data_types, data_type_periods)

        # ヘッダーのみ含まれる
        assert "データ種別" in result
        assert "---" in result


class TestUpdateReadme:
    """update_readme()関数のテスト"""

    def test_update_readme_with_marker(self, tmp_path):
        """既存のマーカーがある場合の更新"""
        readme_path = tmp_path / "README.md"
        original_content = """# Project

<!-- start data-statistics -->
Old stats here
<!-- end data-statistics -->

More content
"""
        readme_path.write_text(original_content, encoding="utf-8")

        stats = {
            "total_files": 100,
            "week_range": "2025年第1週 - 2025年第10週",
            "month_range": "2025年1月 - 2025年3月",
            "latest_fetch": "2025-01-01 00:00 JST",
            "latest_update": "2025-01-01 00:00 JST",
            "data_types": {},
            "data_type_periods": {},
            "latest_week": "2025年第10週",
            "latest_month": "2025年3月",
            "week_count": 10,
            "month_count": 3,
            "anomalies": {"errors": {}, "warnings": {}, "quality_issues": {}},
        }

        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            result = update_readme(stats)
            assert result is True

            # 更新されたREADMEを確認
            updated_content = readme_path.read_text(encoding="utf-8")
            assert "<!-- start data-statistics -->" in updated_content
            assert "<!-- end data-statistics -->" in updated_content
            assert "100件" in updated_content
            assert "Old stats here" not in updated_content
        finally:
            os.chdir(original_cwd)

    def test_update_readme_no_changes(self, tmp_path):
        """変更がない場合"""
        readme_path = tmp_path / "README.md"

        # 統計情報を含むREADMEを作成
        stats = {
            "total_files": 100,
            "week_range": "2025年第1週 - 2025年第10週",
            "month_range": "2025年1月 - 2025年3月",
            "latest_fetch": "2025-01-01 00:00 JST",
            "latest_update": "2025-01-01 00:00 JST",
            "data_types": {},
            "data_type_periods": {},
            "latest_week": "2025年第10週",
            "latest_month": "2025年3月",
            "week_count": 10,
            "month_count": 3,
            "anomalies": {"errors": {}, "warnings": {}, "quality_issues": {}},
        }

        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)

            # 初回更新
            original_content = """# Project

<!-- start data-statistics -->
Old stats
<!-- end data-statistics -->
"""
            readme_path.write_text(original_content, encoding="utf-8")
            update_readme(stats)

            # 同じ統計情報で再度更新
            # 同じ内容で再度更新
            result = update_readme(stats)

            # 変更なしの場合はFalseが返る
            assert result is False
        finally:
            os.chdir(original_cwd)
