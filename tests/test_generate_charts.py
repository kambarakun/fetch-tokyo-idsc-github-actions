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

import hashlib
import itertools
import re
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pytest

# テスト対象モジュールのインポート
from scripts import generate_charts
from scripts.generate_charts import (
    _EXTRA_MARKERS,
    _PRIMARY_MARKERS,
    FONT_SHA256,
    FONT_URL,
    DiseaseStyle,
    _format_period_label,
    _setup_x_axis_ticks,
    build_consistent_style_map,
    build_ranking_eligibility,
    calculate_deviation_rate,
    calculate_seasonal_baseline,
    filter_rates_for_ranking,
    format_legend_label,
    get_all_weeks_data,
    parse_notifiable_weekly,
    parse_period_from_filename,
    parse_sentinel_gender_counts,
    parse_sentinel_weekly_gender,
    select_top_absolute_diseases,
    select_top_deviation_diseases,
    setup_japanese_font,
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
        """ベースラインが0で実測値が正の場合 - 新規発生として固定値100%"""
        data = {"インフルエンザ": {202501: 10.0}}
        baseline = {"インフルエンザ": {202501: 0.0}}

        rates = calculate_deviation_rate(data, baseline)

        # 「新規発生」として固定値100%を設定
        # これにより、トップN選択で除外されず、グラフの連続性も保たれる
        # CDCでは計算スキップだが、可視化目的では適度な警告レベルとして表示
        assert "インフルエンザ" in rates
        assert 202501 in rates["インフルエンザ"]
        assert rates["インフルエンザ"][202501] == 100.0

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


class TestSelectTopDeviationDiseases:
    """select_top_deviation_diseases()関数のテスト

    乖離率グラフ用の TopN 選定ロジック:
    - 期間内のいずれかで baseline を超えた疾患があれば、最大正乖離の降順で TopN
    - 全期間 baseline 以下なら、絶対値最大乖離の降順で TopN (空グラフ回避フォールバック)
    """

    def test_positive_deviation_uses_window_max(self):
        """期間内のどこかで正乖離があれば、最新が負でも選定される"""
        # 疾患Aは最新負だが期間内で +1900% のピーク, Bは最新正だが小さい
        deviation_rates = {
            "疾患A": {202508: 1900.0, 202604: -100.0},
            "疾患B": {202508: 5.0, 202604: 10.0},
        }

        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=5)

        assert fallback is False
        # Aの代表値は期間内最大の1900
        assert top[0][0] == "疾患A"
        assert top[0][1] == 1900.0
        assert top[1][0] == "疾患B"
        assert top[1][1] == 10.0

    def test_top_n_respected(self):
        """top_n を超える結果は返さない"""
        deviation_rates = {f"疾患{i}": {202501: float(i)} for i in range(1, 11)}

        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=3)

        assert fallback is False
        assert len(top) == 3
        # 上位3つは 10, 9, 8
        assert [d for d, _ in top] == ["疾患10", "疾患9", "疾患8"]

    def test_fallback_when_all_below_baseline(self):
        """期間中ずっと baseline 以下のとき、絶対値TopNにフォールバック

        ソート順は絶対値降順だが、戻り値は符号を保つ。
        """
        deviation_rates = {
            "疾患A": {202601: -100.0, 202602: -50.0},
            "疾患B": {202601: -10.0, 202602: -20.0},
            "疾患C": {202601: 0.0},
        }

        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=5)

        assert fallback is True
        # 絶対値降順: |A|=100 > |B|=20 > |C|=0
        assert [d for d, _ in top] == ["疾患A", "疾患B", "疾患C"]
        # 値は符号付き
        assert top[0][1] == -100.0
        assert top[1][1] == -20.0
        assert top[2][1] == 0.0

    def test_zero_only_data_returns_fallback(self):
        """全疾患の値が 0 のみのとき、フォールバック経由で 0 値が返る"""
        deviation_rates = {"疾患A": {202501: 0.0}}

        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=5)

        assert fallback is True
        assert top == [("疾患A", 0.0)]

    def test_disease_with_no_values_excluded(self):
        """値が一つもない疾患は選定対象外"""
        deviation_rates = {
            "疾患A": {},
            "疾患B": {202501: 50.0},
        }

        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=5)

        assert fallback is False
        assert [d for d, _ in top] == ["疾患B"]

    def test_none_values_skipped(self):
        """None 値は除外し、有効値のみで判定する"""
        deviation_rates = {
            "疾患A": {202501: None, 202502: 25.0},
            "疾患B": {202501: None, 202502: None},  # 有効値なし → 除外
        }

        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=5)

        assert fallback is False
        assert [d for d, _ in top] == ["疾患A"]

    def test_empty_input(self):
        """空入力は空リスト + fallback=True を返す (空フィルタの後は常にフォールバック判定)"""
        top, fallback = select_top_deviation_diseases({}, top_n=5)

        assert top == []
        assert fallback is True

    def test_fallback_does_not_activate_when_positive_deviations_exist(self):
        """正乖離が一つでも存在すればフォールバックは発動しない (正負混在ケース)

        期間内: [+30, -50] → 絶対値最大は -50 だが、+30 が存在するため
        通常パス (primary_scores) が発動し、fallback_used=False が返る。
        通常パスのスコアは max(positive_values) = 30.0。
        """
        deviation_rates = {
            "疾患A": {202501: 30.0, 202502: -50.0},
        }
        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=5)
        assert fallback is False
        assert top[0][1] == 30.0

    def test_fallback_with_mixed_negative_magnitudes(self):
        """フォールバックで複数の負乖離疾患を絶対値順に並べ、符号付きで返す"""
        deviation_rates = {
            "疾患A": {202501: -5.0, 202502: -3.0},
            "疾患B": {202501: -50.0},
        }
        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=5)
        assert fallback is True
        assert top[0] == ("疾患B", -50.0)
        assert top[1] == ("疾患A", -5.0)

    def test_top_n_zero_returns_empty(self):
        """top_n=0 は空リストを返す (正乖離あり経路)"""
        deviation_rates = {"疾患A": {202501: 30.0}}
        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=0)
        assert top == []
        assert fallback is False

    def test_top_n_zero_returns_empty_in_fallback_path(self):
        """top_n=0 はフォールバック経路でも空リストを返す"""
        deviation_rates = {"疾患A": {202501: -50.0}}
        top, fallback = select_top_deviation_diseases(deviation_rates, top_n=0)
        assert top == []
        assert fallback is True

    def test_top_n_negative_raises_value_error(self):
        """top_n が負数の場合は ValueError を送出 (負スライスでの意図しない挙動を防止)"""
        deviation_rates = {"疾患A": {202501: 30.0}}
        with pytest.raises(ValueError, match="top_n must be non-negative"):
            select_top_deviation_diseases(deviation_rates, top_n=-1)


class TestSelectTopAbsoluteDiseases:
    """select_top_absolute_diseases()関数のテスト"""

    def test_picks_top_by_latest_period_value(self):
        """最新期間の値が大きい順にトップNを返す"""
        data = {
            "疾患A": {202501: 10, 202502: 20},
            "疾患B": {202501: 5, 202502: 50},
            "疾患C": {202501: 100, 202502: 3},
        }
        top = select_top_absolute_diseases(data, top_n=2)
        assert top[0][0] == "疾患B"
        assert top[1][0] == "疾患A"

    def test_missing_latest_period_value_treated_as_zero(self):
        """最新期間にデータがない疾患は0扱いで末尾に並ぶ"""
        data = {
            "疾患A": {202501: 10, 202502: 20},
            "疾患B": {202501: 100},
        }
        top = select_top_absolute_diseases(data, top_n=2)
        assert top[0][0] == "疾患A"
        assert top[1] == ("疾患B", 0)

    def test_empty_data_returns_empty_list(self):
        """データが空の場合は空リストを返す"""
        assert select_top_absolute_diseases({}, top_n=5) == []

    def test_all_diseases_empty_periods_returns_empty(self):
        """全疾患の期間データが空なら空リストを返す"""
        assert select_top_absolute_diseases({"疾患A": {}, "疾患B": {}}, top_n=5) == []

    def test_top_n_zero_returns_empty(self):
        """top_n=0 は空リストを返す (0件取得という有効な指定)"""
        data = {"疾患A": {202502: 10}, "疾患B": {202502: 5}}
        assert select_top_absolute_diseases(data, top_n=0) == []

    def test_top_n_negative_raises_value_error(self):
        """top_n が負数の場合は ValueError を送出する (Python負スライスの意図しない挙動を防止)"""
        data = {"疾患A": {202502: 10}, "疾患B": {202502: 5}}
        with pytest.raises(ValueError, match="top_n must be non-negative"):
            select_top_absolute_diseases(data, top_n=-1)


class TestBuildConsistentStyleMap:
    """build_consistent_style_map()関数のテスト"""

    def test_shared_disease_gets_same_style_across_charts(self):
        """共有疾患は extra 側の順序・内容に依存せず primary 由来の同一スタイルを維持する

        DiseaseStyle (NamedTuple) の等価性を直接比較することで
        「推移と乖離率で同一疾患が同じスタイル」の契約を実証する。
        """
        # primary のみ
        sm_primary_only = build_consistent_style_map(["A", "B", "C"], [])
        # primary + extra (extra に共有疾患を含む)
        sm_with_extra = build_consistent_style_map(["A", "B", "C"], ["B", "C", "D"])
        # primary + extra (extra の順序を入れ替え)
        sm_extra_reordered = build_consistent_style_map(["A", "B", "C"], ["C", "B", "D"])

        # 共有疾患 "B"/"C" は extra 側の有無/順序に関係なく primary 由来の同一スタイル
        for key in ("A", "B", "C"):
            assert sm_with_extra[key] == sm_primary_only[key]
            assert sm_extra_reordered[key] == sm_primary_only[key]
            # color と marker の両属性も明示的に一致を確認
            assert sm_with_extra[key].color == sm_primary_only[key].color
            assert sm_with_extra[key].marker == sm_primary_only[key].marker

        # extra-only の "D" は primary 経由ではないので primary_only には存在しない
        assert "D" not in sm_primary_only
        assert "D" in sm_with_extra
        # 全エントリが DiseaseStyle 型
        for style in sm_with_extra.values():
            assert isinstance(style, DiseaseStyle)

    def test_primary_diseases_use_primary_markers(self):
        """推移チャートに登場する疾患には _PRIMARY_MARKERS が順番に割り当てられる"""
        style_map = build_consistent_style_map(["A", "B", "C"], [])
        assert style_map["A"].marker == _PRIMARY_MARKERS[0]
        assert style_map["B"].marker == _PRIMARY_MARKERS[1]
        assert style_map["C"].marker == _PRIMARY_MARKERS[2]

    def test_extra_only_diseases_use_extra_markers(self):
        """乖離率のみで登場する疾患には _EXTRA_MARKERS が順番に割り当てられる"""
        style_map = build_consistent_style_map(["A"], ["A", "B", "C"])
        # A は推移由来 (primary marker)
        assert style_map["A"].marker == _PRIMARY_MARKERS[0]
        # B, C は乖離率専用 (extra marker)
        assert style_map["B"].marker == _EXTRA_MARKERS[0]
        assert style_map["C"].marker == _EXTRA_MARKERS[1]

    def test_primary_and_extra_markers_dont_overlap(self):
        """プライマリとエクストラのマーカーセットに重複がない (識別容易性)"""
        assert set(_PRIMARY_MARKERS).isdisjoint(set(_EXTRA_MARKERS))

    def test_extra_only_disease_gets_distinct_color(self):
        """乖離率のみで登場する疾患は別パレットから色を割り当てる"""
        style_map = build_consistent_style_map(["A"], ["A", "B"])
        # extra palette (Set2) は primary (colorblind) と異なる系統
        assert style_map["A"].color != style_map["B"].color

    def test_primary_palette_order_preserved(self):
        """推移の表示順がそのまま色順に反映される"""
        sm1 = build_consistent_style_map(["A", "B"], [])
        sm2 = build_consistent_style_map(["A", "B", "C"], [])
        # 順序は維持される (Aが0番目、Bが1番目)
        keys1 = list(sm1.keys())
        keys2 = list(sm2.keys())
        assert keys1 == ["A", "B"]
        assert keys2 == ["A", "B", "C"]

    def test_only_deviation_diseases_uses_extra_palette(self):
        """推移が空で乖離率のみの場合、全て extra palette から割り当て"""
        style_map = build_consistent_style_map([], ["A", "B"])
        assert "A" in style_map
        assert "B" in style_map
        # 全て extra marker
        assert style_map["A"].marker == _EXTRA_MARKERS[0]
        assert style_map["B"].marker == _EXTRA_MARKERS[1]

    def test_both_empty_returns_empty_map(self):
        """両方空なら空マップを返す"""
        assert build_consistent_style_map([], []) == {}

    def test_extra_only_diseases_preserve_input_order(self):
        """乖離率のみで登場する疾患の入力順序がそのまま色順に反映される"""
        style_map = build_consistent_style_map(["X"], ["X", "B", "A"])
        # X は推移由来、B と A は乖離率専用 (入力順を維持)
        extras = [k for k in style_map if k != "X"]
        assert extras == ["B", "A"]

    def test_absolute_diseases_exceeding_primary_markers_raises(self):
        """推移疾患数が _PRIMARY_MARKERS の容量を超えるとマーカー一意性が
        破綻するため ValueError を送出 (冗長エンコーディング契約の維持)"""
        too_many = [f"D{i}" for i in range(len(_PRIMARY_MARKERS) + 1)]
        with pytest.raises(ValueError, match="primary_markers capacity"):
            build_consistent_style_map(too_many, [])

    def test_extra_only_diseases_exceeding_extra_markers_raises(self):
        """乖離率専用疾患数が _EXTRA_MARKERS の容量を超えると ValueError を送出"""
        too_many_extras = [f"E{i}" for i in range(len(_EXTRA_MARKERS) + 1)]
        with pytest.raises(ValueError, match="extra_markers capacity"):
            build_consistent_style_map([], too_many_extras)

    def test_shared_diseases_dont_count_against_extra_capacity(self):
        """推移と重複する疾患は extra 側にカウントされず、容量超過にならない"""
        # primary=5件 (_PRIMARY_MARKERS と同サイズ), deviation=5件 (全て primary と重複) + 1件のextra
        primary = list(_PRIMARY_MARKERS)  # 形状文字をそのまま疾患名として5件
        deviation = [*primary, "ExtraOnly"]
        # ExtraOnly は extra に1件のみ → 容量内で成功
        style_map = build_consistent_style_map(primary, deviation)
        assert "ExtraOnly" in style_map
        assert style_map["ExtraOnly"].marker == _EXTRA_MARKERS[0]


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


SENTINEL_HEADER = "疾病名,男性,女性,男女合計,定点数"
NOTIFIABLE_HEADER = "疾病名,報告数"


def _write_shift_jis(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="shift_jis")


class TestParseSentinelGenderCounts:
    """parse_sentinel_gender_counts() と get_all_weeks_data(parser=...) のテスト"""

    def test_counts_are_not_divided_by_sentinels(self, tmp_path):
        # Arrange: 実データと同じ形状のヘッダーと行
        csv_path = tmp_path / "sentinel_weekly_gender_2025_01.csv"
        _write_shift_jis(csv_path, [SENTINEL_HEADER, "インフルエンザ,2000,1991,3991,419"])

        # Act / Assert
        assert parse_sentinel_weekly_gender(csv_path) == {"インフルエンザ": 3991 / 419}
        assert parse_sentinel_gender_counts(csv_path) == {"インフルエンザ": 3991.0}

    def test_get_all_weeks_data_uses_given_parser(self, tmp_path):
        _write_shift_jis(
            tmp_path / "sentinel_weekly_gender_2025_01.csv", [SENTINEL_HEADER, "インフルエンザ,2000,1991,3991,419"]
        )
        _write_shift_jis(tmp_path / "sentinel_weekly_gender_2025_02.csv", [SENTINEL_HEADER, "インフルエンザ,1,1,2,419"])

        assert get_all_weeks_data(tmp_path, parser=parse_sentinel_gender_counts) == {
            "インフルエンザ": {202501: 3991.0, 202502: 2.0}
        }
        assert get_all_weeks_data(tmp_path)["インフルエンザ"][202501] == 3991 / 419

    def test_skips_masked_and_non_numeric_values_like_existing_parser(self, tmp_path):
        csv_path = tmp_path / "sentinel_weekly_gender_2025_01.csv"
        _write_shift_jis(csv_path, [SENTINEL_HEADER, "A,1,1,*,10", "B,1,1,abc,10", "C,1,1,0,10", "D,1"])

        assert parse_sentinel_gender_counts(csv_path) == {"C": 0.0}

    def test_missing_header_returns_empty(self, tmp_path):
        csv_path = tmp_path / "sentinel_weekly_gender_2025_01.csv"
        _write_shift_jis(csv_path, ["疾病名,男性,女性,合計"])

        assert parse_sentinel_gender_counts(csv_path) == {}


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def isolated_font_env(tmp_path, monkeypatch):
    """HOME を tmp に差し替え、待機を無くし、フォント登録をテスト内に閉じ込める"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(generate_charts.time, "sleep", lambda _seconds: None)
    # addfont は fontManager.ttflist に追記するので、テスト後に元のリストへ戻す
    monkeypatch.setattr(fm.fontManager, "ttflist", list(fm.fontManager.ttflist))
    return tmp_path / ".local" / "share" / "fonts" / "NotoSansCJKjp-Regular.otf"


class TestFont:
    """setup_japanese_font() の取得・照合・登録のテスト (実フォントはダウンロードしない)"""

    def test_font_url_and_hash_are_pinned(self):
        assert re.search(r"/noto-cjk/[0-9a-f]{40}/", FONT_URL)
        assert "/main/" not in FONT_URL
        assert FONT_URL == (
            "https://raw.githubusercontent.com/notofonts/noto-cjk/"
            "165c01b46ea533872e002e0785ff17e44f6d97d8/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf"
        )
        assert FONT_SHA256 == "68a3fc98800b2a27b371f2fb79991daf3633bd89309d4ffaa6946fd587f375b5"

    def test_download_with_wrong_hash_is_rejected(self, isolated_font_env, monkeypatch, capsys):
        calls = []

        def fake_get(url, timeout):
            calls.append(url)
            return _FakeResponse(b"tampered font" * 100)

        monkeypatch.setattr(generate_charts.requests, "get", fake_get)

        assert setup_japanese_font() is None
        assert not isolated_font_env.exists()
        assert calls == [FONT_URL] * 3
        assert "[font] sha256 verified:" not in capsys.readouterr().out

    def test_existing_font_with_wrong_hash_is_redownloaded(self, isolated_font_env, monkeypatch, capsys):
        # Arrange: 本物のフォント (DejaVuSans) をダウンロード結果にし、期待 sha256 をそれに合わせる
        font_bytes = (Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf").read_bytes()
        monkeypatch.setattr(generate_charts, "FONT_SHA256", hashlib.sha256(font_bytes).hexdigest())
        monkeypatch.setattr(generate_charts.requests, "get", lambda url, timeout: _FakeResponse(font_bytes))
        isolated_font_env.parent.mkdir(parents=True)
        isolated_font_env.write_bytes(b"stale or truncated font")

        # Act
        font_prop = setup_japanese_font()

        # Assert
        assert font_prop is not None
        assert isolated_font_env.read_bytes() == font_bytes
        assert capsys.readouterr().out.count(f"[font] sha256 verified: {isolated_font_env}") == 1

    def test_existing_font_with_matching_hash_is_used_without_download(self, isolated_font_env, monkeypatch, capsys):
        font_bytes = (Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf").read_bytes()
        monkeypatch.setattr(generate_charts, "FONT_SHA256", hashlib.sha256(font_bytes).hexdigest())

        def fail_get(url, timeout):
            raise AssertionError("verified font must not be downloaded again")

        monkeypatch.setattr(generate_charts.requests, "get", fail_get)
        isolated_font_env.parent.mkdir(parents=True)
        isolated_font_env.write_bytes(font_bytes)

        assert setup_japanese_font() is not None
        assert "[font] sha256 verified:" in capsys.readouterr().out

    def test_corrupt_font_returns_none(self, isolated_font_env, monkeypatch):
        # 照合は通るが addfont が RuntimeError (FT_Open_Face 失敗) を投げるバイト列
        corrupt = b"not a font" * 100
        monkeypatch.setattr(generate_charts, "FONT_SHA256", hashlib.sha256(corrupt).hexdigest())
        monkeypatch.setattr(generate_charts.requests, "get", lambda url, timeout: _FakeResponse(corrupt))

        assert setup_japanese_font() is None


def _png_files(root: Path) -> list[Path]:
    return sorted((root / "docs" / "images").glob("*.png"))


class TestMainFailClosed:
    """main() が PNG を書く前に失敗を検出して非 0 を返すことのテスト"""

    def test_main_returns_nonzero_without_font_and_writes_no_png(self, tmp_path, monkeypatch, capsys):
        # Arrange: 入力はあるのでフォントだけが失敗要因になる
        monkeypatch.chdir(tmp_path)
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)
        _write_shift_jis(raw_dir / "notifiable_weekly_2025_01.csv", [NOTIFIABLE_HEADER, "結核,40"])
        monkeypatch.setattr(generate_charts, "get_japanese_font", lambda: None)

        # Act
        exit_code = generate_charts.main()

        # Assert
        assert exit_code != 0
        assert _png_files(tmp_path) == []
        assert "::error title=Japanese font unavailable::" in capsys.readouterr().out

    def test_main_returns_nonzero_when_no_input_files(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data" / "raw").mkdir(parents=True)
        monkeypatch.setattr(generate_charts, "get_japanese_font", fm.FontProperties)

        assert generate_charts.main() == 2
        assert _png_files(tmp_path) == []
        assert "::error title=Chart input error::no chart input files under data/raw" in capsys.readouterr().out


class TestMainEndToEnd:
    """合成データで main() を最後まで通し、乖離率の選定が件数下限と最新期間フィルタに従うことを確認する"""

    @staticmethod
    def _write_dataset(raw_dir: Path) -> None:
        # 定点数 1000: 定点当たりでは 5 未満になる値でも、実数で判定していれば順位付けに入る
        sentinels = 1000
        for year in (2024, 2025):
            for week in range(1, 53):
                latest = year == 2025 and week == 52
                rows = [
                    f"大流行病,0,0,{3500 if latest else 1000},{sentinels}",
                    f"少数病,0,0,{4 if (year, week) == (2025, 30) else 1},{sentinels}",
                    f"ゼロ基準病,0,0,{3 if (year, week) == (2025, 40) else 0},{sentinels}",
                ]
                if year == 2024 or week < 45:
                    rows.append(f"消失病,0,0,{100 if (year, week) == (2025, 10) else 10},{sentinels}")
                _write_shift_jis(raw_dir / f"sentinel_weekly_gender_{year}_{week:02d}.csv", [SENTINEL_HEADER, *rows])

                rows = [
                    f"流行病N,{50 if latest else 10}",
                    f"単発病N,{3 if (year, week) == (2025, 20) else 0}",
                ]
                if year == 2024 or week < 40:
                    rows.append(f"消失病N,{60 if (year, week) == (2025, 5) else 5}")
                _write_shift_jis(raw_dir / f"notifiable_weekly_{year}_{week:02d}.csv", [NOTIFIABLE_HEADER, *rows])

            for month in range(1, 13):
                rows = [f"月流行病,0,0,{400 if (year, month) == (2025, 12) else 100},{sentinels}"]
                if year == 2024 or month < 5:
                    rows.append(f"月消失病,0,0,{100 if (year, month) == (2025, 3) else 10},{sentinels}")
                _write_shift_jis(raw_dir / f"sentinel_monthly_gender_{year}_{month:02d}.csv", [SENTINEL_HEADER, *rows])

    @pytest.mark.filterwarnings("ignore:Glyph")
    def test_main_writes_six_charts_and_ranks_only_eligible_current_diseases(self, tmp_path, monkeypatch, capsys):
        # Arrange
        monkeypatch.chdir(tmp_path)
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)
        self._write_dataset(raw_dir)
        monkeypatch.setattr(generate_charts, "get_japanese_font", fm.FontProperties)

        # Act
        exit_code = generate_charts.main()

        # Assert
        assert exit_code == 0
        assert [p.name for p in _png_files(tmp_path)] == [
            "notifiable_weekly_absolute.png",
            "notifiable_weekly_deviation.png",
            "sentinel_monthly_absolute.png",
            "sentinel_monthly_deviation.png",
            "sentinel_weekly_absolute.png",
            "sentinel_weekly_deviation.png",
        ]
        chart_lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[chart] ")]
        assert len(chart_lines) == 6
        assert "[chart] sentinel_weekly_deviation: 大流行病" in chart_lines
        assert "[chart] notifiable_weekly_deviation: 流行病N" in chart_lines
        assert "[chart] sentinel_monthly_deviation: 月流行病" in chart_lines


class TestWeekTicks:
    """_setup_x_axis_ticks() の週次目盛りのテスト"""

    @staticmethod
    def _ticks(periods: list[int]) -> tuple[list[float], list[str]]:
        fig, ax = plt.subplots()
        try:
            ax.plot(range(len(periods)), [0] * len(periods))
            _setup_x_axis_ticks(ax, periods, "week", None)
            return list(ax.get_xticks()), [label.get_text() for label in ax.get_xticklabels()]
        finally:
            plt.close(fig)

    def test_year_boundary_ticks_include_year_and_do_not_collide(self):
        periods = list(range(202538, 202553)) + list(range(202601, 202638))

        positions, labels = self._ticks(periods)

        assert labels == ["2025/40", "45", "50", "2026/1", "5", "10", "15", "20", "25", "30", "35"]
        assert "52" not in labels
        assert "1" not in labels
        assert min(b - a for a, b in itertools.pairwise(positions)) >= 2

    def test_week53_window_ticks_do_not_collide(self):
        periods = [*range(202603, 202654), 202701]

        positions, labels = self._ticks(periods)

        assert labels[0] == "2026/5"
        assert labels[-1] == "2027/1"
        assert "53" not in labels
        assert min(b - a for a, b in itertools.pairwise(positions)) >= 2

    def test_year_start_before_first_candidate_carries_the_year(self):
        periods = [202552, 202601, 202602, 202603, 202604, 202605]

        _, labels = self._ticks(periods)

        assert labels == ["2026/1", "5"]


class TestDeviationRanking:
    """件数下限 (build_ranking_eligibility) と最新期間フィルタ (filter_rates_for_ranking) のテスト"""

    @staticmethod
    def _rank(counts, count_baseline, periods, data=None, rates=None):
        data = counts if data is None else data
        rates = calculate_deviation_rate(data, count_baseline) if rates is None else rates
        eligible = build_ranking_eligibility(counts, count_baseline, periods)
        return select_top_deviation_diseases(filter_rates_for_ranking(data, rates, eligible), top_n=5)

    def test_small_count_spikes_are_not_ranked(self):
        p = 202637
        counts = {"新規": {p: 1.0}, "数例": {p: 1.0}, "大流行": {p: 3515.0}}
        count_baseline = {"新規": {p: 0.0}, "数例": {p: 0.2}, "大流行": {p: 963.8}}

        top, fallback_used = self._rank(counts, count_baseline, [p])

        assert [d for d, _ in top] == ["大流行"]
        assert fallback_used is False

    def test_floor_boundaries(self):
        p = 202637
        counts = {"実測4": {p: 4.0}, "実測5": {p: 5.0}, "基準0.9": {p: 10.0}, "基準1.0": {p: 10.0}}
        count_baseline = {"実測4": {p: 1.0}, "実測5": {p: 1.0}, "基準0.9": {p: 0.9}, "基準1.0": {p: 1.0}}

        eligible = build_ranking_eligibility(counts, count_baseline, [p])

        assert eligible == {"実測5": {p}, "基準1.0": {p}}

    def test_zero_baseline_is_not_ranked(self):
        # 麻しん 202616 型: 46 例 vs 基準 0 は描画上 100% だが順位付けには使わない
        counts = {"麻しん": {202616: 46.0}}
        count_baseline = {"麻しん": {202616: 0.0}}

        assert calculate_deviation_rate(counts, count_baseline) == {"麻しん": {202616: 100.0}}
        assert self._rank(counts, count_baseline, [202616]) == ([], True)

    def test_disease_missing_latest_period_is_not_ranked(self):
        p1, p2 = 202603, 202604
        counts = {"報告中": {p1: 10.0, p2: 12.0}, "報告停止": {p1: 100.0}}
        count_baseline = {"報告中": {p1: 5.0, p2: 5.0}, "報告停止": {p1: 2.0}}

        top, _ = self._rank(counts, count_baseline, [p1, p2])

        assert [d for d, _ in top] == ["報告中"]

    def test_past_peak_with_negative_latest_is_still_ranked(self):
        p1, p2 = 202603, 202604
        counts = {"過去ピーク": {p1: 40.0, p2: 5.0}}
        count_baseline = {"過去ピーク": {p1: 10.0, p2: 10.0}}

        top, fallback_used = self._rank(counts, count_baseline, [p1, p2])

        assert top == [("過去ピーク", 300.0)]
        assert fallback_used is False

    def test_fallback_uses_only_eligible_periods(self):
        p1, p2 = 202603, 202604
        counts = {"A": {p1: 4.0, p2: 9.0}, "B": {p1: 5.0, p2: 8.0}}
        count_baseline = {"A": {p1: 40.0, p2: 10.0}, "B": {p1: 10.0, p2: 10.0}}

        top, fallback_used = self._rank(counts, count_baseline, [p1, p2])

        # A の -90% (実測 4 例) は不適格なので、A の代表値は適格な期間の -10% になる
        assert fallback_used is True
        assert top == [("B", -50.0), ("A", -10.0)]

    def test_rates_are_ranked_on_chart_values_while_floors_use_counts(self):
        # 定点当たりの値 (チャートの値) が小さくても、実数が下限を満たせば順位付けに入る
        p = 202637
        counts = {"定点病": {p: 3500.0}}
        count_baseline = {"定点病": {p: 1000.0}}
        per_sentinel = {"定点病": {p: 3.5}}
        rates = calculate_deviation_rate(per_sentinel, {"定点病": {p: 1.0}})

        top, _ = self._rank(counts, count_baseline, [p], data=per_sentinel, rates=rates)

        assert top == [("定点病", 250.0)]

    def test_none_eligibility_applies_only_latest_period_filter(self):
        data = {"報告中": {202603: 1.0, 202604: 1.0}, "報告停止": {202603: 1.0}}
        rates = {"報告中": {202603: 900.0, 202604: 100.0}, "報告停止": {202603: 999.0}}

        assert filter_rates_for_ranking(data, rates, None) == {"報告中": {202603: 900.0, 202604: 100.0}}
        assert filter_rates_for_ranking({}, rates, None) == {}


class TestLegendLabel:
    """format_legend_label() のテスト"""

    def test_latest_label(self):
        assert format_legend_label("結核", "44.0", 202637, 202637, "week") == "結核 (最新: 44.0)"

    def test_last_period_label_when_latest_missing(self):
        assert format_legend_label("疾患X", "-100%", 202604, 202608, "month") == "疾患X (最終 2026年4月: -100%)"
        assert format_legend_label("疾患X", "+10%", 202636, 202637, "week") == "疾患X (最終 2026年第36週: +10%)"

    def test_provisional_label_only_when_flagged(self):
        assert (
            format_legend_label("結核", "44.0", 202637, 202637, "week", provisional=True) == "結核 (最新・速報: 44.0)"
        )
        assert format_legend_label("結核", "40.0", 202636, 202637, "week", provisional=True) == (
            "結核 (最終 2026年第36週: 40.0)"
        )
