"""
tests/test_generate_charts.py - グラフ生成機能のテスト

主要関数のテストケース:
- parse_period_from_filename: ファイル名パース
- _format_period_label: 期間ラベル生成
- calculate_seasonal_baseline: 季節性ベースライン計算
- calculate_deviation_rate: 乖離率計算
- parse_sentinel_weekly_gender: 定点週次・性別CSVパース
- parse_notifiable_weekly: 全数週次CSVパース
"""

import tempfile
from pathlib import Path

# テスト対象モジュールのインポート
from scripts.generate_charts import (
    _format_period_label,
    calculate_deviation_rate,
    calculate_seasonal_baseline,
    parse_notifiable_weekly,
    parse_period_from_filename,
    parse_sentinel_weekly_gender,
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
        """データ不足の場合 (2年分) - グラフ連続性のため1年分でも計算"""
        all_data = {
            "インフルエンザ": {
                202401: 13.0,
                202501: 20.0,  # 過去2年分のみ
            }
        }
        recent_periods = [202501]

        baselines = calculate_seasonal_baseline(all_data, recent_periods, years=5)

        # 時系列グラフの連続性のため、1年分でもベースライン計算される
        # 統計的信頼性は低いが、グラフの欠損を防ぐ
        assert "インフルエンザ" in baselines
        assert 202501 in baselines["インフルエンザ"]
        # 2年分の平均: (13+20) / 2 = 16.5 (注: recent_periods以外のデータは除外)
        # 実際は202401の13.0のみが過去データ (202501は現在年のため除外)
        assert abs(baselines["インフルエンザ"][202501] - 13.0) < 0.01

    def test_baseline_with_exactly_3_years(self):
        """3年分のデータ (CDCベストプラクティス)"""
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

    def test_zero_baseline_with_positive_value(self):
        """ベースラインが0で実測値が正の場合 (ゼロ除算回避)"""
        data = {"インフルエンザ": {202501: 10.0}}
        baseline = {"インフルエンザ": {202501: 0.0}}

        rates = calculate_deviation_rate(data, baseline)

        # ベースラインが0で実測値が正の場合はキーが設定されない
        # (数学的に定義不可能 - 0で割れない)
        assert "インフルエンザ" in rates
        assert 202501 not in rates["インフルエンザ"]

    def test_zero_baseline_with_zero_value(self):
        """ベースラインと実測値の両方が0の場合 - グラフ連続性のため0%を設定"""
        data = {"インフルエンザ": {202501: 0.0}}
        baseline = {"インフルエンザ": {202501: 0.0}}

        rates = calculate_deviation_rate(data, baseline)

        # 時系列グラフの連続性のため、0%を明示的に設定
        assert "インフルエンザ" in rates
        assert 202501 in rates["インフルエンザ"]
        assert rates["インフルエンザ"][202501] == 0.0

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


class TestParseSentinelWeeklyGender:
    """parse_sentinel_weekly_gender()関数のテスト"""

    def test_parse_valid_csv_with_data(self):
        """正常なCSVデータのパース"""
        # Shift_JISで書かれたCSVファイルを一時ファイルとして作成
        # 実際のフォーマット: 疾病名、男性、女性、男女合計、定点数の列が必要
        csv_content = """集計対象期間,2025年第1週,,,
性別,男性,,,
疾病名,男性,女性,男女合計,定点数
インフルエンザ,250,300,550,10
COVID-19,100,120,220,10
RSウイルス,50,60,110,10"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="shift_jis") as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            # パース実行
            result = parse_sentinel_weekly_gender(temp_path)

            # 疾患データが正しく抽出されているか確認
            assert "インフルエンザ" in result
            assert "COVID-19" in result
            assert "RSウイルス" in result

            # 値が正しいか確認 (合計/定点数)
            assert result["インフルエンザ"] == 55.0  # 550/10
            assert result["COVID-19"] == 22.0  # 220/10
            assert result["RSウイルス"] == 11.0  # 110/10
        finally:
            temp_path.unlink()

    def test_parse_csv_with_no_data(self):
        """データ行がないCSVのパース"""
        csv_content = """集計対象期間,2025年第1週,,,
性別,男性,,,
疾病名,男性,女性,男女合計"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="shift_jis") as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            result = parse_sentinel_weekly_gender(temp_path)
            # データがない場合は空の辞書が返される
            assert result == {}
        finally:
            temp_path.unlink()

    def test_parse_csv_with_invalid_values(self):
        """不正な数値データの扱い"""
        csv_content = """集計対象期間,2025年第1週,,,
性別,男性,,,
疾病名,男性,女性,男女合計,定点数
インフルエンザ,abc,def,550,10
COVID-19,100,120,xyz,10"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="shift_jis") as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            result = parse_sentinel_weekly_gender(temp_path)
            # 有効な値のみが抽出される
            assert isinstance(result, dict)
            assert "インフルエンザ" in result  # 550は有効
            assert result["インフルエンザ"] == 55.0  # 550/10
            # COVID-19はxyzなのでスキップされる
            assert "COVID-19" not in result
        finally:
            temp_path.unlink()

    def test_parse_csv_with_zero_values(self):
        """0件のデータを含むCSVのパース - 時系列グラフの連続性のため0も含める"""
        csv_content = """集計対象期間,2025年第1週,,,
性別,男性,,,
疾病名,男性,女性,男女合計,定点数
インフルエンザ,0,0,0,10
COVID-19,50,60,110,10
RSウイルス,0,0,0,5"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="shift_jis") as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            result = parse_sentinel_weekly_gender(temp_path)

            # 時系列グラフの連続性のため、0の値も含まれる
            assert "インフルエンザ" in result
            assert result["インフルエンザ"] == 0.0  # 0/10
            # 正の値も正しく記録される
            assert "COVID-19" in result
            assert result["COVID-19"] == 11.0  # 110/10
            # 別の0の値も含まれる
            assert "RSウイルス" in result
            assert result["RSウイルス"] == 0.0  # 0/5
        finally:
            temp_path.unlink()


class TestParseNotifiableWeekly:
    """parse_notifiable_weekly()関数のテスト"""

    def test_parse_valid_csv_with_data(self):
        """正常なCSVデータのパース"""
        csv_content = """集計対象期間,2025年第1週,,,
,,,,
,東京都
疾病名,報告数
腸管出血性大腸菌感染症,5
デング熱,2
梅毒,15"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="shift_jis") as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            result = parse_notifiable_weekly(temp_path)

            # 疾患データが正しく抽出されているか確認
            assert "腸管出血性大腸菌感染症" in result
            assert "デング熱" in result
            assert "梅毒" in result

            # 値が正しいか確認
            assert result["腸管出血性大腸菌感染症"] == 5.0
            assert result["デング熱"] == 2.0
            assert result["梅毒"] == 15.0
        finally:
            temp_path.unlink()

    def test_parse_csv_with_no_data(self):
        """データ行がないCSVのパース"""
        csv_content = """集計対象期間,2025年第1週,,,
,,,,
,東京都
疾病名,報告数"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="shift_jis") as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            result = parse_notifiable_weekly(temp_path)
            # データがない場合は空の辞書が返される
            assert result == {}
        finally:
            temp_path.unlink()

    def test_parse_csv_with_zero_values(self):
        """0件のデータを含むCSVのパース - 時系列グラフの連続性のため0も含める"""
        csv_content = """集計対象期間,2025年第1週,,,
,,,,
,東京都
疾病名,報告数
腸管出血性大腸菌感染症,0
デング熱,5"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="shift_jis") as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            result = parse_notifiable_weekly(temp_path)

            # 時系列グラフの連続性のため、0の値も含まれる
            assert "腸管出血性大腸菌感染症" in result
            assert result["腸管出血性大腸菌感染症"] == 0.0
            # 正の値も正しく記録される
            assert "デング熱" in result
            assert result["デング熱"] == 5.0
        finally:
            temp_path.unlink()
