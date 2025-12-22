"""
tests/test_generate_charts.py - グラフ生成機能のテスト

主要関数のテストケース:
- parse_period_from_filename: ファイル名パース
- _format_period_label: 期間ラベル生成
- calculate_seasonal_baseline: 季節性ベースライン計算
- calculate_deviation_rate: 乖離率計算
"""

from pathlib import Path

# テスト対象モジュールのインポート
from scripts.generate_charts import (
    _format_period_label,
    calculate_deviation_rate,
    calculate_seasonal_baseline,
    parse_period_from_filename,
)


class TestParsePeriodFromFilename:
    """parse_period_from_filename()関数のテスト"""

    def test_sentinel_weekly_gender_file(self):
        """定点週次・性別ファイルのパース"""
        file_path = Path("sentinel_weekly_gender_2025_50.csv")
        result = parse_period_from_filename(file_path)

        assert result is not None
        year, period, period_key = result
        assert year == 2025
        assert period == 50
        assert period_key == 202550

    def test_notifiable_weekly_file(self):
        """全数週次ファイルのパース"""
        file_path = Path("notifiable_weekly_2025_01.csv")
        result = parse_period_from_filename(file_path)

        assert result is not None
        year, period, period_key = result
        assert year == 2025
        assert period == 1
        assert period_key == 202501

    def test_sentinel_monthly_age_file(self):
        """定点月次・年齢群ファイルのパース"""
        file_path = Path("sentinel_monthly_age_2024_12.csv")
        result = parse_period_from_filename(file_path)

        assert result is not None
        year, period, period_key = result
        assert year == 2024
        assert period == 12
        assert period_key == 202412

    def test_invalid_filename(self):
        """不正なファイル名"""
        file_path = Path("invalid.csv")
        result = parse_period_from_filename(file_path)

        assert result is None

    def test_missing_period(self):
        """期間が欠けているファイル名"""
        file_path = Path("sentinel_weekly.csv")
        result = parse_period_from_filename(file_path)

        assert result is None


class TestFormatPeriodLabel:
    """_format_period_label()関数のテスト"""

    def test_weekly_label(self):
        """週次期間ラベル生成"""
        label = _format_period_label(202501, 202552, "week")
        assert label == "2025年第1週 - 2025年第52週"

    def test_monthly_label(self):
        """月次期間ラベル生成"""
        label = _format_period_label(202401, 202412, "month")
        assert label == "2024年1月 - 2024年12月"

    def test_cross_year_weekly(self):
        """年跨ぎ週次ラベル"""
        label = _format_period_label(202450, 202505, "week")
        assert label == "2024年第50週 - 2025年第5週"

    def test_cross_year_monthly(self):
        """年跨ぎ月次ラベル"""
        label = _format_period_label(202410, 202503, "month")
        assert label == "2024年10月 - 2025年3月"


class TestCalculateSeasonalBaseline:
    """calculate_seasonal_baseline()関数のテスト"""

    def test_baseline_with_sufficient_data(self):
        """十分なデータがある場合のベースライン計算"""
        # 5年分のデータを作成
        all_data = {
            "インフルエンザ": {
                202001: 10.0,
                202101: 12.0,
                202201: 15.0,
                202301: 11.0,
                202401: 13.0,
                202501: 20.0,  # 最新データ
            }
        }
        recent_periods = [202501]

        baselines = calculate_seasonal_baseline(all_data, recent_periods, years=5)

        # 過去5年平均 = (10+12+15+11+13)/5 = 12.2
        assert "インフルエンザ" in baselines
        assert 202501 in baselines["インフルエンザ"]
        assert abs(baselines["インフルエンザ"][202501] - 12.2) < 0.01

    def test_baseline_with_insufficient_data(self):
        """データ不足の場合 (3年未満)"""
        all_data = {
            "インフルエンザ": {
                202401: 13.0,
                202501: 20.0,  # 過去2年分のみ
            }
        }
        recent_periods = [202501]

        baselines = calculate_seasonal_baseline(all_data, recent_periods, years=5)

        # データ不足の場合はキーが設定されない
        assert "インフルエンザ" in baselines
        assert 202501 not in baselines["インフルエンザ"]

    def test_baseline_with_exactly_3_years(self):
        """ちょうど3年分のデータ (最小必要データ数)"""
        all_data = {
            "インフルエンザ": {
                202201: 15.0,
                202301: 11.0,
                202401: 13.0,
                202501: 20.0,
            }
        }
        recent_periods = [202501]

        baselines = calculate_seasonal_baseline(all_data, recent_periods, years=5)

        # 3年分あればベースライン計算される: (15+11+13)/3 = 13.0
        assert "インフルエンザ" in baselines
        assert 202501 in baselines["インフルエンザ"]
        assert abs(baselines["インフルエンザ"][202501] - 13.0) < 0.01


class TestCalculateDeviationRate:
    """calculate_deviation_rate()関数のテスト"""

    def test_positive_deviation(self):
        """正の乖離 (流行)"""
        data = {"インフルエンザ": {202501: 20.0}}
        baseline = {"インフルエンザ": {202501: 10.0}}

        rates = calculate_deviation_rate(data, baseline)

        # (20-10)/10 * 100 = 100%
        assert "インフルエンザ" in rates
        assert 202501 in rates["インフルエンザ"]
        assert abs(rates["インフルエンザ"][202501] - 100.0) < 0.01

    def test_negative_deviation(self):
        """負の乖離 (減少)"""
        data = {"インフルエンザ": {202501: 5.0}}
        baseline = {"インフルエンザ": {202501: 10.0}}

        rates = calculate_deviation_rate(data, baseline)

        # (5-10)/10 * 100 = -50%
        assert "インフルエンザ" in rates
        assert 202501 in rates["インフルエンザ"]
        assert abs(rates["インフルエンザ"][202501] - (-50.0)) < 0.01

    def test_zero_baseline(self):
        """ベースラインが0の場合 (ゼロ除算回避)"""
        data = {"インフルエンザ": {202501: 10.0}}
        baseline = {"インフルエンザ": {202501: 0.0}}

        rates = calculate_deviation_rate(data, baseline)

        # ベースラインが0の場合はキーが設定されない
        assert "インフルエンザ" in rates
        assert 202501 not in rates["インフルエンザ"]

    def test_missing_baseline(self):
        """ベースラインが存在しない場合 (データ不足)"""
        data = {"インフルエンザ": {202501: 10.0}}
        baseline = {"インフルエンザ": {}}  # 202501のベースラインなし

        rates = calculate_deviation_rate(data, baseline)

        # ベースラインが存在しない場合は乖離率を計算しない
        assert "インフルエンザ" in rates
        assert 202501 not in rates["インフルエンザ"]

    def test_disease_not_in_baseline(self):
        """疾患がベースラインに存在しない場合"""
        data = {"新型疾患": {202501: 10.0}}
        baseline = {}  # 新型疾患のベースラインなし

        rates = calculate_deviation_rate(data, baseline)

        # 疾患がベースラインに存在しない場合はスキップ
        assert "新型疾患" not in rates
