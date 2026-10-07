#!/usr/bin/env python3
"""
感染症データの可視化グラフ生成スクリプト (CDCベストプラクティス準拠)

以下の6種類のグラフを生成:
1. 週次定点・絶対数トップ5
2. 週次定点・季節性乖離率トップ5
3. 週次全数・絶対数トップ5
4. 週次全数・季節性乖離率トップ5
5. 月次定点・絶対数トップ5
6. 月次定点・季節性乖離率トップ5

季節性ベースライン: 同週/同月の過去5年平均を使用 (CDC推奨)
乖離率: (実測値 - ベースライン) / ベースライン x 100
"""

import contextlib
import csv
import hashlib
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import requests
import seaborn as sns


class DiseaseStyle(NamedTuple):
    """1疾患分の描画スタイル (色 + マーカー形状)

    色覚多様性 (Color Vision Deficiency) への配慮のため、色だけでなく
    マーカー形状も疾患ごとに一意に割り当てる。同じ疾患が複数チャートに
    登場する場合は同じスタイルを再利用することで視覚的な追跡を容易にする。
    """

    color: tuple[float, float, float]
    marker: str


# 推移チャート用マーカー (top_n=5 を想定)
# 形状カテゴリを最大限分散させて知覚的識別性を確保 (CatPAW論文の知見):
# 円・正方形・菱形・三角・星 はそれぞれ独立した形状カテゴリ
_PRIMARY_MARKERS: tuple[str, ...] = ("o", "s", "D", "^", "*")

# 乖離率チャート専用 (推移にいない疾患) 用マーカー
# プライマリと形状カテゴリが被らないよう選定:
# 塗りプラス・塗りX・下向き三角・六角・右向き三角
# (細い `+` は密データで線状に見えるため SAS 推奨に従い使用しない)
_EXTRA_MARKERS: tuple[str, ...] = ("P", "X", "v", "h", ">")

# 日本語フォントの取得元 (可変ブランチではなく固定コミット) と期待する sha256
FONT_URL = "https://raw.githubusercontent.com/notofonts/noto-cjk/165c01b46ea533872e002e0785ff17e44f6d97d8/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf"
FONT_SHA256 = "68a3fc98800b2a27b371f2fb79991daf3633bd89309d4ffaa6946fd587f375b5"

# 乖離率ランキングに使う期間の件数下限 (定点当たりではなく実数。定点は男女合計、全数は報告数)
# 数例のスパイクや baseline=0 の固定 100% が順位を占有しないようにする
DEVIATION_MIN_OBSERVED_CASES = 5
DEVIATION_MIN_BASELINE_CASES = 1.0

# マーカーサイズ (推移・乖離率の両チャート共通)
# 形状による識別性を確保するため十分な大きさを設定 (色覚多様性配慮)
_MARKER_SIZE: int = 7


def _copy_font_properties(base_fp, size: float):
    """FontPropertiesをサイズ指定でコピー

    @lru_cacheの副作用を回避するため、常に新しいインスタンスを返す

    Args:
        base_fp: 元のFontPropertiesオブジェクト (None可)
        size: フォントサイズ (ポイント)

    Returns:
        サイズ設定済みのFontPropertiesコピー、またはNone
    """
    if not base_fp:
        return None
    fp = base_fp.copy()
    fp.set_size(size)
    return fp


def _add_annotation(
    ax, values: list, value_text: str, line_color: str | tuple, japanese_font, check_non_zero: bool = True
):
    """グラフにアノテーションを追加 (最新の非None値に対して)

    Args:
        ax: matplotlibのAxesオブジェクト
        values: データ値のリスト (Noneを含む可能性あり)
        value_text: 表示するテキスト (例: "123", "+45%")
        line_color: アノテーションの色 (文字列またはRGBタプル)
        japanese_font: 日本語フォント (FontPropertiesオブジェクト、またはNone)
        check_non_zero: Trueの場合は最新値が0でないときのみ追加
    """
    # 最新値を取得 (Noneでない最後の値)
    latest_value = next((v for v in reversed(values) if v is not None), None)

    # 最新値がNoneの場合、またはcheck_non_zeroがTrueで値が0の場合はスキップ
    if latest_value is None:
        return
    if check_non_zero and latest_value == 0:
        return

    # 最新値の位置を見つける (逆順で最初の非None値)
    for i in range(len(values) - 1, -1, -1):
        if values[i] is not None:
            # アノテーションを追加
            if japanese_font:
                ax.annotate(
                    value_text,
                    xy=(i, latest_value),
                    xytext=(5, 0),
                    textcoords="offset points",
                    color=line_color,
                    fontweight="bold",
                    fontproperties=_copy_font_properties(japanese_font, 9),
                )
            else:
                ax.annotate(
                    value_text,
                    xy=(i, latest_value),
                    xytext=(5, 0),
                    textcoords="offset points",
                    fontsize=9,
                    color=line_color,
                    fontweight="bold",
                )
            break


def setup_japanese_font():
    """日本語フォント (Noto Sans CJK JP) を固定コミットから取得・sha256 照合して登録する

    Returns:
        登録した FontProperties。取得・照合・登録のいずれかに失敗した場合は None
        (呼び出し側は PNG を書かずに非 0 で終了する。豆腐グラフを公開しないため)
    """
    # HOME 差し替えがテストで効くよう、モジュール定数にせず毎回 Path.home() から求める
    font_dir = Path.home() / ".local" / "share" / "fonts"
    font_path = font_dir / "NotoSansCJKjp-Regular.otf"

    # 既存ファイルも無条件には信用しない (不完全な取得や別版のフォントを使い続けないため)
    try:
        font_dir.mkdir(parents=True, exist_ok=True)
        if font_path.exists() and hashlib.sha256(font_path.read_bytes()).hexdigest() != FONT_SHA256:
            print(f"[WARNING] 既存フォントの sha256 が一致しないため削除して再取得します: {font_path}")
            font_path.unlink()
    except OSError as e:
        # 例外を伝播させると main() が ::error 注記を出せないため None で返す
        print(f"[ERROR] フォントキャッシュを確認できません: {e}")
        return None

    if not font_path.exists():
        print("📥 日本語フォント (Noto Sans CJK JP) をダウンロード中...")
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                print(f"[INFO] ダウンロード試行 {attempt}/{max_retries}...")
                response = requests.get(FONT_URL, timeout=30)
                response.raise_for_status()

                downloaded_data = response.content
                sha256_hash = hashlib.sha256(downloaded_data).hexdigest()
                print(f"[INFO] ダウンロードサイズ: {len(downloaded_data) / 1024 / 1024:.2f} MB")
                if sha256_hash != FONT_SHA256:
                    raise ValueError(f"sha256 が一致しません (expected {FONT_SHA256}, got {sha256_hash})")

                # 照合に成功したバイト列だけを保存する
                font_path.write_bytes(downloaded_data)
                print(f"[SUCCESS] フォントをダウンロード: {font_path}")
                break

            except requests.exceptions.RequestException as e:
                print(f"[WARNING] フォントのダウンロードに失敗 (試行 {attempt}/{max_retries}): {e}")
            except (ValueError, OSError) as e:
                print(f"[WARNING] フォントの検証/保存に失敗 (試行 {attempt}/{max_retries}): {e}")
                # 書きかけのファイルを残さない (削除にも失敗した場合は次回の sha256 照合で検出される)
                with contextlib.suppress(OSError):
                    font_path.unlink(missing_ok=True)

            if attempt == max_retries:
                print("[ERROR] 最大リトライ回数に達しました。日本語フォントを取得できません。")
                return None
            time.sleep(2)

    print(f"[font] sha256 verified: {font_path}")

    # フォントを登録してFontPropertiesオブジェクトを返す
    try:
        # フォントを明示的に追加 (matplotlib 3.10.8以降はキャッシュ自動更新)
        fm.fontManager.addfont(str(font_path))
        font_prop = fm.FontProperties(fname=str(font_path))
    except (OSError, ValueError, RuntimeError) as e:
        # 壊れたフォントでは FT_Open_Face 失敗が RuntimeError として送出される
        print(f"[ERROR] フォントの登録に失敗: {e}")
        return None

    plt.rcParams["axes.unicode_minus"] = False
    print(f"✅ 日本語フォントを設定: {font_prop.get_name()}")
    return font_prop


@lru_cache(maxsize=1)
def get_japanese_font():
    """日本語フォントを遅延初期化して取得 (@lru_cacheでキャッシュ)

    @lru_cacheを使用することで、グローバル変数を使わずにキャッシュを実現。
    テストやコンカレント実行時の問題を回避する。
    """
    font_prop = setup_japanese_font()

    # Seabornスタイル設定もここで実行
    sns.set_style("whitegrid")
    sns.set_palette("husl")

    return font_prop


def read_csv_shift_jis(file_path: Path) -> list[list[str]]:
    """Shift_JISエンコードのCSVファイルを読み込む

    デコード不可能な文字は'�'(U+FFFD)に置換してデータ品質問題を可視化する。
    """
    with file_path.open(encoding="shift_jis", errors="replace") as f:
        reader = csv.reader(f)
        return list(reader)


def parse_period_from_filename(file_path: Path) -> tuple[int, int, int] | None:
    """ファイル名から年と期間を抽出する

    Args:
        file_path: データファイルのパス

    Returns:
        (year, period, period_key) のタプル、またはNone
        - year: 年 (例: 2025)
        - period: 週または月 (例: 50 for 第50週, 12 for 12月)
        - period_key: 年*100 + 期間 (例: 202550 for 2025年第50週)

    Examples:
        sentinel_weekly_gender_2025_50.csv -> (2025, 50, 202550)
        notifiable_weekly_2025_01.csv -> (2025, 1, 202501)
        sentinel_monthly_age_2024_12.csv -> (2024, 12, 202412)
    """
    parts = file_path.stem.split("_")
    # ファイル名形式: {type}_{period_type}_{subtype}_{YYYY}_{PP}.csv
    # 最後の2つがYYYYとPP
    if len(parts) < 2:
        return None

    try:
        # 末尾から2番目と1番目を年と期間として抽出
        year = int(parts[-2])
        period = int(parts[-1])
        period_key = year * 100 + period
        return (year, period, period_key)
    except (ValueError, IndexError):
        return None


def _find_sentinel_gender_header(rows: list[list[str]]) -> tuple[int, int] | None:
    """定点・性別データのヘッダー行と男女合計列を探す

    Returns:
        (ヘッダー行のインデックス, 男女合計列のインデックス)、見つからなければ None
    """
    # ヘッダー行を探す(疾病名男性女性男女合計を含む行)
    for i, row in enumerate(rows):
        if len(row) >= 4 and "疾病名" in str(row[0]):
            # 男女合計列のインデックスを探す
            for j, cell in enumerate(row):
                if "男女合計" in str(cell):
                    return i, j
            return None
    return None


def parse_sentinel_gender_counts(csv_path: Path) -> dict[str, float]:
    """定点・性別データから疾患別の実患者数 (男女合計列、定点数で割らない) を抽出

    乖離率ランキングの件数下限の判定に使う。読み方 (ヘッダー探索、`*`/`-`/空・
    非数値のスキップ) は parse_sentinel_weekly_gender と同じ。

    Returns:
        疾患名 -> 患者数 (実数) のdict
    """
    rows = read_csv_shift_jis(csv_path)
    header = _find_sentinel_gender_header(rows)
    if header is None:
        return {}
    header_row, total_col_idx = header

    disease_data = {}
    for row in rows[header_row + 1 :]:
        if len(row) <= total_col_idx:
            continue
        disease_name = str(row[0]).strip()
        value_str = str(row[total_col_idx]).strip()
        if disease_name and value_str and value_str not in ["*", "-", ""]:
            try:
                disease_data[disease_name] = float(value_str)
            except ValueError:
                continue
    return disease_data


def parse_sentinel_weekly_gender(csv_path: Path) -> dict[str, float]:
    """定点週次・性別データから疾患別患者数を抽出

    Returns:
        疾患名 -> 定点あたり患者数 (男女合計列) のdict
    """
    rows = read_csv_shift_jis(csv_path)

    header = _find_sentinel_gender_header(rows)
    if header is None:
        return {}
    header_row, total_col_idx = header

    # データ行を読み込み(ヘッダーの次の行から)
    disease_data = {}
    for i in range(header_row + 1, len(rows)):
        row = rows[i]
        if len(row) <= total_col_idx:
            continue

        disease_name = str(row[0]).strip()
        value_str = str(row[total_col_idx]).strip()

        # 疾患名と患者数が有効な場合のみ追加
        # 注: 0のデータも含める (時系列グラフの連続性を保つため)
        # 0を除外すると、折れ線グラフに欠損が生じ、視覚的な連続性が損なわれる
        if disease_name and value_str and value_str not in ["*", "-", ""]:
            try:
                # 定点あたり患者数を計算(合計患者数 / 定点数)
                total_count = float(value_str)
                # 定点数は5列目(インデックス4)
                if len(row) > 4:
                    sentinel_count_str = str(row[4]).strip()
                    sentinel_count = float(sentinel_count_str) if sentinel_count_str else 1
                    patients_per_sentinel = total_count / sentinel_count
                    disease_data[disease_name] = patients_per_sentinel
            except (ValueError, ZeroDivisionError):
                continue

    return disease_data


def parse_notifiable_weekly(csv_path: Path) -> dict[str, float]:
    """全数報告週次データから疾患別報告数を抽出

    Returns:
        疾患名 -> 報告数のdict
    """
    rows = read_csv_shift_jis(csv_path)

    # ヘッダー行を探す(疾病名報告数を含む行)
    header_row = None

    for i, row in enumerate(rows):
        if len(row) >= 2 and "疾病名" in str(row[0]) and "報告数" in str(row[1]):
            header_row = i
            break

    if header_row is None:
        return {}

    # データ行を読み込み(ヘッダーの次の行から)
    disease_data = {}
    for i in range(header_row + 1, len(rows)):
        row = rows[i]
        if len(row) < 2:
            continue

        disease_name = str(row[0]).strip()
        value_str = str(row[1]).strip()

        # 疾患名と報告数が有効な場合のみ追加
        # 注: 0のデータも含める (時系列グラフの連続性を保つため)
        # 0を除外すると、折れ線グラフに欠損が生じ、視覚的な連続性が損なわれる
        if disease_name and value_str and value_str not in ["*", "-", ""]:
            try:
                count = float(value_str)
                disease_data[disease_name] = count
            except ValueError:
                continue

    return disease_data


def parse_sentinel_monthly_gender(csv_path: Path) -> dict[str, float]:
    """定点月次・性別データから疾患別患者数を抽出

    Returns:
        疾患名 -> 定点あたり患者数 (男女合計列) のdict
    """
    # 月次データは週次と同じフォーマットなので、同じパーサーを使用
    return parse_sentinel_weekly_gender(csv_path)


def get_recent_weeks_data(data_dir: Path, num_weeks: int = 12) -> dict[str, dict[int, float]]:
    """直近N週のデータを取得 (定点・性別)

    Returns:
        疾患名 -> {週番号: 患者数} のdict
    """
    # 最新の週次データファイルを取得
    weekly_files = sorted(data_dir.glob("sentinel_weekly_gender_*.csv"), reverse=True)

    if not weekly_files:
        return {}

    # 直近N週分を処理
    all_data: dict[str, dict[int, float]] = defaultdict(dict)

    for file_path in weekly_files[:num_weeks]:
        # ファイル名から年週を抽出
        period_info = parse_period_from_filename(file_path)
        if period_info is None:
            continue

        _, _, period_key = period_info

        disease_data = parse_sentinel_weekly_gender(file_path)
        for disease, value in disease_data.items():
            all_data[disease][period_key] = value

    return dict(all_data)


def get_notifiable_weeks_data(data_dir: Path, num_weeks: int = 12) -> dict[str, dict[int, float]]:
    """直近N週のデータを取得 (全数報告)

    Returns:
        疾患名 -> {週番号: 報告数} のdict
    """
    weekly_files = sorted(data_dir.glob("notifiable_weekly_*.csv"), reverse=True)

    if not weekly_files:
        return {}

    all_data: dict[str, dict[int, float]] = defaultdict(dict)

    for file_path in weekly_files[:num_weeks]:
        period_info = parse_period_from_filename(file_path)
        if period_info is None:
            continue

        _, _, period_key = period_info

        disease_data = parse_notifiable_weekly(file_path)
        for disease, value in disease_data.items():
            all_data[disease][period_key] = value

    return dict(all_data)


def get_recent_months_data(data_dir: Path, num_months: int = 12) -> dict[str, dict[int, float]]:
    """直近N月のデータを取得 (定点・性別)

    Returns:
        疾患名 -> {月番号: 患者数} のdict
    """
    monthly_files = sorted(data_dir.glob("sentinel_monthly_gender_*.csv"), reverse=True)

    if not monthly_files:
        return {}

    all_data: dict[str, dict[int, float]] = defaultdict(dict)

    for file_path in monthly_files[:num_months]:
        period_info = parse_period_from_filename(file_path)
        if period_info is None:
            continue

        _, _, period_key = period_info

        disease_data = parse_sentinel_monthly_gender(file_path)
        for disease, value in disease_data.items():
            all_data[disease][period_key] = value

    return dict(all_data)


def get_all_weeks_data(
    data_dir: Path, parser: Callable[[Path], dict[str, float]] = parse_sentinel_weekly_gender
) -> dict[str, dict[int, float]]:
    """全週次データを取得 (季節性ベースライン計算用)

    Args:
        data_dir: データディレクトリ
        parser: 1ファイル分を読むパーサー (既定は定点あたり患者数。実数は parse_sentinel_gender_counts)

    Returns:
        疾患名 -> {週番号: 患者数} のdict
    """
    weekly_files = sorted(data_dir.glob("sentinel_weekly_gender_*.csv"))
    all_data: dict[str, dict[int, float]] = defaultdict(dict)

    for file_path in weekly_files:
        period_info = parse_period_from_filename(file_path)
        if period_info is None:
            continue

        _, _, period_key = period_info

        disease_data = parser(file_path)
        for disease, value in disease_data.items():
            all_data[disease][period_key] = value

    return dict(all_data)


def get_all_notifiable_weeks_data(data_dir: Path) -> dict[str, dict[int, float]]:
    """全全数報告週次データを取得 (季節性ベースライン計算用)

    Returns:
        疾患名 -> {週番号: 報告数} のdict
    """
    weekly_files = sorted(data_dir.glob("notifiable_weekly_*.csv"))
    all_data: dict[str, dict[int, float]] = defaultdict(dict)

    for file_path in weekly_files:
        period_info = parse_period_from_filename(file_path)
        if period_info is None:
            continue

        _, _, period_key = period_info

        disease_data = parse_notifiable_weekly(file_path)
        for disease, value in disease_data.items():
            all_data[disease][period_key] = value

    return dict(all_data)


def get_all_months_data(
    data_dir: Path, parser: Callable[[Path], dict[str, float]] = parse_sentinel_monthly_gender
) -> dict[str, dict[int, float]]:
    """全月次データを取得 (季節性ベースライン計算用)

    Args:
        data_dir: データディレクトリ
        parser: 1ファイル分を読むパーサー (既定は定点あたり患者数。実数は parse_sentinel_gender_counts)

    Returns:
        疾患名 -> {月番号: 患者数} のdict
    """
    monthly_files = sorted(data_dir.glob("sentinel_monthly_gender_*.csv"))
    all_data: dict[str, dict[int, float]] = defaultdict(dict)

    for file_path in monthly_files:
        period_info = parse_period_from_filename(file_path)
        if period_info is None:
            continue

        _, _, period_key = period_info

        disease_data = parser(file_path)
        for disease, value in disease_data.items():
            all_data[disease][period_key] = value

    return dict(all_data)


def calculate_seasonal_baseline(
    all_data: dict[str, dict[int, float]], recent_periods: list[int], years: int = 5
) -> dict[str, dict[int, float]]:
    """季節性ベースラインを計算 (CDCベストプラクティス)

    同週/同月の過去N年平均を計算

    Args:
        all_data: 全期間のデータ (疾患名 -> {期間番号: 値})
        recent_periods: 直近の期間リスト (例: [202549, 202550])
        years: 過去何年分を使うか (デフォルト: 5年)

    Returns:
        疾患名 -> {期間番号: ベースライン値}
    """
    baselines: dict[str, dict[int, float]] = {}

    for disease, periods_data in all_data.items():
        baseline_data = {}

        for period in recent_periods:
            # 期間を年と週/月に分解
            year = period // 100
            period_num = period % 100

            # 同じ週/月の過去years年分のデータを取得
            historical_values = []
            for past_year in range(year - years, year):
                past_period = past_year * 100 + period_num
                if past_period in periods_data:
                    historical_values.append(periods_data[past_period])

            # 平均を計算 (時系列グラフの連続性のため、データが1つでもあれば計算)
            # CDCベストプラクティスでは3年以上推奨だが、連続性を優先
            # データが少ない場合は統計的信頼性は低いが、グラフの欠損を防ぐ
            if len(historical_values) >= 1:
                baseline_data[period] = sum(historical_values) / len(historical_values)
            # else: 過去データが全くない場合のみスキップ

        baselines[disease] = baseline_data

    return baselines


def calculate_deviation_rate(
    data: dict[str, dict[int, float]], baseline: dict[str, dict[int, float]]
) -> dict[str, dict[int, float]]:
    """ベースラインからの乖離率を計算

    Args:
        data: 実測値 (疾患名 -> {期間番号: 値})
        baseline: ベースライン (疾患名 -> {期間番号: 値})

    Returns:
        疾患名 -> {期間番号: 乖離率(%)} のdict
    """
    deviation_rates: dict[str, dict[int, float]] = {}

    for disease, periods_data in data.items():
        if disease not in baseline:
            continue

        rate_data = {}
        for period, value in periods_data.items():
            # ベースラインが存在しない場合 (データ不足) は乖離率を計算しない
            if period not in baseline[disease]:
                continue

            baseline_value = baseline[disease][period]

            # 乖離率を計算
            # 時系列グラフの連続性のため、可能な限り値を設定する
            if baseline_value > 0:
                # 通常の計算
                deviation = ((value - baseline_value) / baseline_value) * 100
                rate_data[period] = deviation
            elif baseline_value == 0 and value == 0:
                # ベースラインも実測値も0: 乖離率0%
                rate_data[period] = 0.0
            elif baseline_value == 0 and value > 0:
                # ベースライン0で実測値が正: 「新規発生」として固定値100%
                # CDCでは計算スキップだが、可視化では連続性とトップN選択のため固定値を使用
                # 100%は「ベースラインの2倍」に相当し、適度な警告レベルを示す
                rate_data[period] = 100.0
            # else: baseline_value < 0 の場合はスキップ (異常値)

        deviation_rates[disease] = rate_data

    return deviation_rates


def select_top_deviation_diseases(
    deviation_rates: Mapping[str, Mapping[int, float | None]], top_n: int = 5
) -> tuple[list[tuple[str, float]], bool]:
    """乖離率グラフ用のトップN疾患を選定 (CDC流: 期間全体での最大正乖離)

    最新1期間だけで判定すると、期間内で大きく流行した疾患も
    現在値がbaseline以下まで戻ると消えてしまう。CDC FluView同様に
    期間全体を視野に入れて選定する。

    選定ロジック:
    1. 期間内のいずれかで baseline を超えた疾患があれば、その最大正乖離値で
       降順ソートしてトップN件を選ぶ (流行検知の本来の意図を維持)
    2. 期間中ずっと baseline 以下の場合は、絶対値最大の乖離率(符号付き)で
       降順ソートしてトップN件を選ぶ (空グラフ回避のフォールバック)

    Args:
        deviation_rates: 疾患名 -> {期間番号: 乖離率(%) または None}
            None は乖離率が計算できなかった期間を表し、選定から除外される。
            型は Mapping (共変) で受けるため、calculate_deviation_rate() の
            戻り値 dict[str, dict[int, float]] (None を含まない) もそのまま渡せる。
        top_n: 表示する疾患数

    Returns:
        ([(疾患名, 代表乖離率値), ...], fallback_used)
        代表乖離率値は符号付き:
          - 通常パス: 期間内の最大正乖離率 (常に正)
          - フォールバック: 期間内で絶対値が最大の乖離率 (負になりうる)
        fallback_used が True のとき、正乖離が一つも存在せず絶対値で選定したことを示す。
        入力 deviation_rates が空、または全疾患が None のみの場合は ([], True) を返す。

    Raises:
        ValueError: top_n が負数の場合 (Python の負スライス挙動によって意図しない
            「末尾以外を返却」が起きるのを防ぐためのフェイルファスト)
    """
    if top_n < 0:
        raise ValueError(f"top_n must be non-negative, got {top_n}")

    primary_scores: dict[str, float] = {}
    fallback_scores: dict[str, float] = {}

    for disease, periods in deviation_rates.items():
        non_null_values = [v for v in periods.values() if v is not None]
        if not non_null_values:
            continue
        # 絶対値最大の値を符号を保ったまま記録 (= 期間内で最も極端な乖離)
        fallback_scores[disease] = max(non_null_values, key=abs)
        # ちょうど baseline (v == 0) はフォールバック対象 ("正乖離なし" 扱い)
        positive_values = [v for v in non_null_values if v > 0]
        if positive_values:
            primary_scores[disease] = max(positive_values)

    if primary_scores:
        ranked = sorted(primary_scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_n], False

    ranked_fallback = sorted(fallback_scores.items(), key=lambda x: abs(x[1]), reverse=True)
    return ranked_fallback[:top_n], True


def build_ranking_eligibility(
    counts: Mapping[str, Mapping[int, float]],
    count_baseline: Mapping[str, Mapping[int, float]],
    periods: list[int],
    min_observed: float = DEVIATION_MIN_OBSERVED_CASES,
    min_baseline: float = DEVIATION_MIN_BASELINE_CASES,
) -> dict[str, set[int]]:
    """乖離率ランキングに使える (疾患, 期間) を実数の件数下限で判定する

    期間 p が使えるのは「実測件数 >= min_observed かつ ベースライン件数 >= min_baseline」のとき。
    baseline=0 の期間 (描画上は固定 100%) はベースライン件数の下限で自動的に不適格になる。

    Args:
        counts: 実数の全期間データ (疾患名 -> {期間番号: 件数})
        count_baseline: counts に calculate_seasonal_baseline を適用した実数ベースライン
        periods: 判定対象の期間 (チャートの窓)
        min_observed: 実測件数の下限
        min_baseline: ベースライン件数の下限

    Returns:
        疾患名 -> 適格な期間番号の集合 (適格な期間が無い疾患は含まない)
    """
    eligible: dict[str, set[int]] = {}
    for disease, disease_counts in counts.items():
        disease_baseline = count_baseline.get(disease, {})
        ok_periods = {
            p
            for p in periods
            if p in disease_counts
            and p in disease_baseline
            and disease_counts[p] >= min_observed
            and disease_baseline[p] >= min_baseline
        }
        if ok_periods:
            eligible[disease] = ok_periods
    return eligible


def filter_rates_for_ranking(
    data: Mapping[str, Mapping[int, float]],
    deviation_rates: Mapping[str, Mapping[int, float]],
    eligible_periods: Mapping[str, set[int]] | None,
) -> dict[str, dict[int, float]]:
    """乖離率ランキング (select_top_deviation_diseases) に渡す乖離率を絞り込む

    - 最新期間 (data の全期間の最大) に実測データが無い疾患は除外する
      (報告されなくなった疾患が過去の値で順位を占有しないため。乖離率の有無ではなく
      実測データの有無で判定するので、ベースラインが無い期間でも全疾患が消えることはない)
    - eligible_periods が与えられれば、適格な期間の乖離率だけを残す (fallback 経路も同じ)

    Args:
        data: チャートの実測値 (疾患名 -> {期間番号: 値})
        deviation_rates: calculate_deviation_rate の戻り値
        eligible_periods: build_ranking_eligibility の戻り値。None なら期間の絞り込みはしない

    Returns:
        疾患名 -> {期間番号: 乖離率} のdict
    """
    all_periods = {p for periods in data.values() for p in periods}
    if not all_periods:
        return {}
    latest_period = max(all_periods)

    filtered: dict[str, dict[int, float]] = {}
    for disease, rates in deviation_rates.items():
        if latest_period not in data.get(disease, {}):
            continue
        if eligible_periods is None:
            filtered[disease] = dict(rates)
        else:
            allowed = eligible_periods.get(disease, set())
            filtered[disease] = {p: v for p, v in rates.items() if p in allowed}
    return filtered


def select_top_absolute_diseases(data: Mapping[str, Mapping[int, float]], top_n: int = 5) -> list[tuple[str, float]]:
    """絶対数グラフ用のトップN疾患を選定 (最新期間の値が大きい順)

    generate_absolute_chart() 内の選定ロジックと同一仕様。色マップ構築のために
    事前選定が必要なケースで使用する。

    Raises:
        ValueError: top_n が負数の場合 (Python の負スライス挙動によって意図しない
            「末尾以外を返却」が起きるのを防ぐためのフェイルファスト)
    """
    if top_n < 0:
        raise ValueError(f"top_n must be non-negative, got {top_n}")

    all_periods = sorted({p for periods in data.values() for p in periods})
    if not all_periods:
        return []
    latest_period = max(all_periods)
    latest_values = {disease: periods.get(latest_period, 0) for disease, periods in data.items()}
    return sorted(latest_values.items(), key=lambda x: x[1], reverse=True)[:top_n]


def build_consistent_style_map(
    absolute_diseases: list[str],
    deviation_diseases: list[str],
    primary_palette_name: str = "colorblind",
    extra_palette_name: str = "Set2",
    primary_markers: tuple[str, ...] = _PRIMARY_MARKERS,
    extra_markers: tuple[str, ...] = _EXTRA_MARKERS,
) -> dict[str, DiseaseStyle]:
    """推移チャートと乖離率チャートで一貫した描画スタイル (色+マーカー) を構築

    推移 (絶対数) チャートに登場する疾患には primary パレット/マーカーから
    固定スタイルを割り当て、乖離率チャートで新たに登場する (推移にいない)
    疾患には extra パレット/マーカーから別系統のスタイルを割り当てる。

    色覚多様性への配慮:
      - 既定パレットは seaborn の colorblind / Set2 を使用 (CB-friendly)
      - 色だけでなくマーカー形状も疾患ごとに一意化することで、色の識別が
        難しい利用者にも個々の疾患を追跡可能にする

    Args:
        absolute_diseases: 推移チャートに表示される疾患名のリスト (表示順)
        deviation_diseases: 乖離率チャートに表示される疾患名のリスト (表示順)
        primary_palette_name: 推移用パレット名 (seaborn palette)
        extra_palette_name: 乖離率専用パレット名 (推移とは異なる系統)
        primary_markers: 推移チャート用マーカー形状のシーケンス
        extra_markers: 乖離率専用マーカー形状のシーケンス

    Returns:
        {疾患名: DiseaseStyle(color, marker)} のスタイルマップ

    Raises:
        ValueError: 疾患数がマーカー数を超えた場合 (マーカー形状の一意性が
            破れるため。色のみだと色覚異常者で識別困難な疾患ペアが生じる
            設計契約上、マーカー一意性は冗長エンコーディングの前提となる)
    """
    if len(absolute_diseases) > len(primary_markers):
        raise ValueError(
            f"absolute_diseases ({len(absolute_diseases)} items) exceeds "
            f"primary_markers capacity ({len(primary_markers)}); "
            f"marker uniqueness cannot be guaranteed."
        )

    style_map: dict[str, DiseaseStyle] = {}

    if absolute_diseases:
        primary_palette = sns.color_palette(primary_palette_name, n_colors=len(absolute_diseases))
        for i, disease in enumerate(absolute_diseases):
            style_map[disease] = DiseaseStyle(color=primary_palette[i], marker=primary_markers[i])

    extra_only = [d for d in deviation_diseases if d not in style_map]
    if len(extra_only) > len(extra_markers):
        raise ValueError(
            f"deviation-only diseases ({len(extra_only)} items) exceeds "
            f"extra_markers capacity ({len(extra_markers)}); "
            f"marker uniqueness cannot be guaranteed."
        )
    if extra_only:
        extra_palette = sns.color_palette(extra_palette_name, n_colors=max(len(extra_only), 1))
        for i, disease in enumerate(extra_only):
            style_map[disease] = DiseaseStyle(color=extra_palette[i], marker=extra_markers[i])

    return style_map


def _format_period_label(min_period: int, max_period: int, period_type: str) -> str:
    """期間ラベルを生成 (X軸用)

    Args:
        min_period: 最小期間 (YYYYPP形式)
        max_period: 最大期間 (YYYYPP形式)
        period_type: 期間タイプ ('week' or 'month')

    Returns:
        フォーマットされた期間ラベル
    """
    if period_type == "week":
        return f"{min_period // 100}年第{min_period % 100}週 - {max_period // 100}年第{max_period % 100}週"
    return f"{min_period // 100}年{min_period % 100}月 - {max_period // 100}年{max_period % 100}月"


def format_legend_label(
    disease: str,
    value_text: str,
    last_period: int,
    final_period: int,
    period_type: str,
    provisional: bool = False,
) -> str:
    """凡例ラベルを生成する

    「最新」は表示値が最終期間の値のときだけ使い、それ以外は値の期間を明示する
    (最終期間に値が無い疾患の過去の値を「最新」と誤読させないため)。

    Args:
        disease: 疾患名
        value_text: 表示する値 (例: "12.3", "+45%")
        last_period: 表示値の期間 (YYYYPP形式)
        final_period: チャートの最終期間 (YYYYPP形式)
        period_type: 期間タイプ ('week' or 'month')
        provisional: 最終期間の値が速報値なら True

    Returns:
        凡例ラベル
    """
    if last_period == final_period:
        prefix = "最新・速報" if provisional else "最新"
        return f"{disease} ({prefix}: {value_text})"
    year, number = divmod(last_period, 100)
    period_text = f"{year}年第{number}週" if period_type == "week" else f"{year}年{number}月"
    return f"{disease} (最終 {period_text}: {value_text})"


def _apply_cdc_styling(ax, fig) -> None:
    """CDCスタイルをグラフに適用

    Args:
        ax: Matplotlibの軸オブジェクト
        fig: Matplotlibの図オブジェクト
    """
    # グリッド: Y軸のみ表示
    ax.grid(True, axis="y", alpha=0.2, linestyle="-", linewidth=0.5)
    ax.grid(False, axis="x")

    # 背景色: 白
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    # 枠線: 薄いグレー
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("#CCCCCC")


def _setup_x_axis_ticks(ax, all_periods: list[int], period_type: str, japanese_font) -> None:
    """X軸の目盛りを設定

    Args:
        ax: Matplotlibの軸オブジェクト
        all_periods: 全期間のリスト
        period_type: 期間タイプ ('week' or 'month')
        japanese_font: 日本語フォントプロパティ (None可)
    """
    if period_type == "week":
        # 週番号が5の倍数の期間を目盛り候補にし、窓内の最初の目盛りと年が変わる最初の期間には
        # 年を付ける (YYYY/W)。週番号だけだと年跨ぎで 52 と 1 が隣接し「521」と誤読されるため
        year_tick_positions: list[int] = []
        plain_tick_positions: list[int] = []
        for i, period in enumerate(all_periods):
            is_year_start = i > 0 and period // 100 != all_periods[i - 1] // 100
            is_candidate = period % 100 % 5 == 0
            if is_year_start or (is_candidate and not year_tick_positions and not plain_tick_positions):
                year_tick_positions.append(i)
            elif is_candidate:
                plain_tick_positions.append(i)

        # 年付き目盛りから2位置未満の年なし目盛りはラベルが重なるので出さない
        plain_tick_positions = [i for i in plain_tick_positions if all(abs(i - y) >= 2 for y in year_tick_positions)]

        tick_positions = sorted(year_tick_positions + plain_tick_positions)
        tick_labels_list = [
            (
                f"{all_periods[i] // 100}/{all_periods[i] % 100}"
                if i in year_tick_positions
                else str(all_periods[i] % 100)
            )
            for i in tick_positions
        ]
    else:  # month
        # 12ヶ月を全て表示
        tick_positions = list(range(len(all_periods)))
        tick_labels_list = [str(all_periods[i] % 100) for i in tick_positions]

    ax.set_xticks(tick_positions)
    tick_labels = ax.set_xticklabels(tick_labels_list, rotation=0, ha="center", fontsize=10)

    if japanese_font:
        for label in tick_labels:
            label.set_fontproperties(japanese_font)


def _setup_chart_labels(ax, xlabel_text: str, ylabel: str, title: str, japanese_font) -> None:
    """チャートの軸ラベル、タイトル、凡例を設定

    Args:
        ax: Matplotlibの軸オブジェクト
        xlabel_text: X軸ラベル
        ylabel: Y軸ラベル
        title: グラフタイトル
        japanese_font: 日本語フォントプロパティ (None可)
    """
    if japanese_font:
        # FontPropertiesをサイズ別にcopyして渡す (fontsize引数の上書き問題を回避)
        ax.set_xlabel(xlabel_text, fontproperties=_copy_font_properties(japanese_font, 11))
        ax.set_ylabel(ylabel, fontproperties=_copy_font_properties(japanese_font, 12))
        ax.set_title(title, fontweight="bold", fontproperties=_copy_font_properties(japanese_font, 20), pad=10)
        ax.legend(loc="upper left", prop=_copy_font_properties(japanese_font, 12), frameon=False)
    else:
        ax.set_xlabel(xlabel_text, fontsize=11)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.set_title(title, fontsize=20, fontweight="bold", pad=10)
        ax.legend(loc="upper left", fontsize=12, frameon=False)


# 全数週次の最新週は初回公表 (速報) 値で、後日の追加報告で修正される
_PROVISIONAL_NOTE = "※ 最新週は速報値 (後日の追加報告で修正され、多くは上方修正される)"


def _draw_footer(fig, footer_lines: list[str], japanese_font) -> None:
    """フッター (注釈とデータソース) を図の下端に描画する"""
    footer_text = "\n".join(footer_lines)
    if japanese_font:
        # FontPropertiesをサイズ指定でcopy (fontsize上書き問題を回避)
        fig.text(
            0.99,
            0.01,
            footer_text,
            ha="right",
            va="bottom",
            color="#666666",
            fontproperties=_copy_font_properties(japanese_font, 8),
        )
    else:
        fig.text(0.99, 0.01, footer_text, ha="right", va="bottom", fontsize=8, color="#666666")


def _footer_bottom(footer_lines: list[str]) -> float:
    """フッター行数に応じた tight_layout の下端 (2行で従来どおり 6%)"""
    return max(0.06, 0.03 * len(footer_lines))


def generate_absolute_chart(
    data: dict[str, dict[int, float]],
    output_path: Path,
    title: str,
    ylabel: str,
    data_source: str,
    period_type: str = "week",
    top_n: int = 5,
    style_map: dict[str, DiseaseStyle] | None = None,
    provisional_latest: bool = False,
) -> None:
    """絶対数推移グラフを生成 (CDCスタイル)

    Args:
        data: 疾患名 -> {期間番号: 値}
        output_path: 出力ファイルパス
        title: グラフタイトル
        ylabel: Y軸ラベル
        data_source: データソース表示
        period_type: 期間タイプ ('week' or 'month')
        top_n: トップN疾患を表示
        style_map: 疾患名 -> DiseaseStyle(color, marker) のマップ (省略時はseabornデフォルトcycler+'o')
        provisional_latest: 最終期間の値が速報値なら True (凡例とフッターに明示する)
    """
    if not data:
        print("警告: データが空のため、グラフを生成できません")
        return

    # 全期間の期間番号を取得
    all_periods = sorted({p for periods in data.values() for p in periods})

    # 全ての疾患の期間データが空でないか確認
    if not all_periods:
        print("警告: 全ての疾患データが空のため、グラフを生成できません")
        return

    # 日本語フォントを初期化 (遅延評価)
    JAPANESE_FONT = get_japanese_font()

    # 最新期間のトップN疾患を選択
    top_diseases = select_top_absolute_diseases(data, top_n=top_n)
    print(f"[chart] {output_path.stem}: {' | '.join(d for d, _ in top_diseases)}")

    # グラフ作成 (800x500px固定サイズ)
    fig, ax = plt.subplots(figsize=(8, 5))

    # 期間の最小・最大を取得
    min_period = min(all_periods)
    max_period = max(all_periods)

    for disease, _ in top_diseases:
        # 全期間に対してデータをマッピング(欠損値はNone)
        values = [data[disease].get(p) for p in all_periods]

        # 最新値を取得(Noneでない最後の値)とその期間
        last_idx = next((i for i in range(len(values) - 1, -1, -1) if values[i] is not None), len(values) - 1)
        latest_value = values[last_idx] or 0

        # 桁数を値に応じて調整(定点データは小数)
        value_format = f"{latest_value:.1f}" if latest_value >= 10 else f"{latest_value:.2f}"

        # 折れ線グラフ (CDCスタイル) - 凡例に最新値を含める
        label_with_value = format_legend_label(
            disease, value_format, all_periods[last_idx], max_period, period_type, provisional=provisional_latest
        )
        style = style_map[disease] if style_map and disease in style_map else None
        plot_kwargs: dict = {"marker": style.marker, "color": style.color} if style else {"marker": "o"}
        line = ax.plot(
            range(len(all_periods)),
            values,
            linewidth=2.5,
            label=label_with_value,
            markersize=_MARKER_SIZE,
            **plot_kwargs,
        )

        # 最新データポイントにアノテーションを追加 (共通関数を使用)
        _add_annotation(ax, values, value_format, line[0].get_color(), JAPANESE_FONT, check_non_zero=True)

    # X軸ラベル (期間を明示)
    xlabel_text = _format_period_label(min_period, max_period, period_type)

    # 軸ラベル、タイトル、凡例を設定
    _setup_chart_labels(ax, xlabel_text, ylabel, title, JAPANESE_FONT)

    # CDCスタイルを適用
    _apply_cdc_styling(ax, fig)

    # X軸目盛りを設定
    _setup_x_axis_ticks(ax, all_periods, period_type, JAPANESE_FONT)

    # データソースと注釈 (下側の確保したスペースに配置)
    note_text = "※ 最新週の患者数トップ5を表示" if period_type == "week" else "※ 最新月の患者数トップ5を表示"
    footer_lines = [note_text, *([_PROVISIONAL_NOTE] if provisional_latest else []), data_source]

    # レイアウト調整 (上2%=タイトル用、下端はフッター行数に応じて確保)
    plt.tight_layout(rect=(0, _footer_bottom(footer_lines), 1, 0.98))
    _draw_footer(fig, footer_lines, JAPANESE_FONT)

    plt.savefig(output_path, dpi=100)
    plt.close()

    print(f"✅ {title}グラフを生成: {output_path} (800x500px)")


def generate_deviation_chart(
    data: dict[str, dict[int, float]],
    baseline: dict[str, dict[int, float]],
    output_path: Path,
    title: str,
    data_source: str,
    period_type: str = "week",
    top_n: int = 5,
    style_map: dict[str, DiseaseStyle] | None = None,
    eligible_periods: dict[str, set[int]] | None = None,
    provisional_latest: bool = False,
) -> None:
    """ベースライン乖離率グラフを生成 (CDCスタイル)

    Args:
        data: 疾患名 -> {期間番号: 値}
        baseline: 疾患名 -> {期間番号: ベースライン値}
        output_path: 出力ファイルパス
        title: グラフタイトル
        data_source: データソース表示
        period_type: 期間タイプ ('week' or 'month')
        top_n: トップN疾患を表示
        style_map: 疾患名 -> DiseaseStyle(color, marker) のマップ (省略時はseabornデフォルトcycler+'o')
        eligible_periods: build_ranking_eligibility の戻り値 (順位付けに使える期間)。
            None なら最新期間フィルタだけを適用する
        provisional_latest: 最終期間の値が速報値なら True (凡例とフッターに明示する)
    """
    if not data or not baseline:
        print("警告: データが空のため、グラフを生成できません")
        return

    # 乖離率を計算
    deviation_rates = calculate_deviation_rate(data, baseline)

    if not deviation_rates:
        print("警告: 乖離率データが空のため、グラフを生成できません")
        return

    # 乖離率データの全期間をチェック
    if not any(periods for periods in deviation_rates.values()):
        print("警告: 乖離率データの期間が空のため、グラフを生成できません")
        return

    # 全期間の期間番号を取得
    all_periods = sorted({p for periods in data.values() for p in periods})

    # 全ての疾患の期間データが空でないか確認
    if not all_periods:
        print("警告: 全ての疾患データが空のため、グラフを生成できません")
        return

    # 日本語フォントを初期化 (遅延評価)
    JAPANESE_FONT = get_japanese_font()

    # 期間全体で baseline を超えた疾患 (最大正乖離) を優先選定。
    # 最新期間1点だけで判定すると、期間内で流行した疾患が現在 baseline 以下に
    # 戻った瞬間に全て消えて空グラフになるため (CDC FluView 同様 full-season 方式)。
    # 順位付けには最新期間に報告のある疾患の、件数下限を満たす期間だけを使う
    # (main() の style map 用の選定と同じ入力にして、両チャートの色の一貫性を保つ)
    top_diseases, fallback_used = select_top_deviation_diseases(
        filter_rates_for_ranking(data, deviation_rates, eligible_periods), top_n=top_n
    )
    print(f"[chart] {output_path.stem}: {' | '.join(d for d, _ in top_diseases)}")

    # グラフ作成 (800x500px固定サイズ)
    fig, ax = plt.subplots(figsize=(8, 5))

    # 期間の最小・最大を取得
    min_period = min(all_periods)
    max_period = max(all_periods)

    for disease, _ in top_diseases:
        if disease not in deviation_rates:
            continue

        # 全期間に対してデータをマッピング(欠損値はNone)
        values = [deviation_rates[disease].get(p) for p in all_periods]

        # 最新値を取得(Noneでない最後の値)とその期間
        last_idx = next((i for i in range(len(values) - 1, -1, -1) if values[i] is not None), len(values) - 1)
        latest_value = values[last_idx] or 0

        # 折れ線グラフ (CDCスタイル) - 凡例に最新値を含める
        label_with_value = format_legend_label(
            disease,
            f"{latest_value:+.0f}%",
            all_periods[last_idx],
            max_period,
            period_type,
            provisional=provisional_latest,
        )
        style = style_map[disease] if style_map and disease in style_map else None
        plot_kwargs: dict = {"marker": style.marker, "color": style.color} if style else {"marker": "o"}
        line = ax.plot(
            range(len(all_periods)),
            values,
            linewidth=2.5,
            label=label_with_value,
            markersize=_MARKER_SIZE,
            **plot_kwargs,
        )

        # 最新データポイントにアノテーションを追加 (共通関数を使用)
        _add_annotation(ax, values, f"{latest_value:+.0f}%", line[0].get_color(), JAPANESE_FONT, check_non_zero=True)

    # ベースライン (0%ライン) を表示
    if len(all_periods) > 0:
        ax.axhline(y=0, color="#999999", linestyle="--", linewidth=1, alpha=0.7)

    # X軸ラベル (期間を明示)
    xlabel_text = _format_period_label(min_period, max_period, period_type)

    # 軸ラベル、タイトル、凡例を設定
    _setup_chart_labels(ax, xlabel_text, "季節性ベースラインからの乖離率 (%)", title, JAPANESE_FONT)

    # CDCスタイルを適用
    _apply_cdc_styling(ax, fig)

    # X軸目盛りを設定
    _setup_x_axis_ticks(ax, all_periods, period_type, JAPANESE_FONT)

    # データソースと注釈 (下側の確保したスペースに配置)
    if fallback_used:
        note_text = f"※ 期間中ベースラインを超える疾患なし — 参考として乖離絶対値の大きい疾患を最大{top_n}つ表示"
    else:
        note_text = f"※ 期間中の最大正乖離(流行兆候)が大きい疾患を最大{top_n}つ表示"
    ranking_note = "※ 順位付けは最新期間に報告のある疾患のみ"
    if eligible_periods is not None:
        ranking_note += (
            f"。実測{DEVIATION_MIN_OBSERVED_CASES}例未満または"
            f"ベースライン{DEVIATION_MIN_BASELINE_CASES:g}例未満の期間は使わない"
        )
    footer_lines = [note_text, ranking_note, *([_PROVISIONAL_NOTE] if provisional_latest else []), data_source]

    # レイアウト調整 (上2%=タイトル用、下端はフッター行数に応じて確保)
    plt.tight_layout(rect=(0, _footer_bottom(footer_lines), 1, 0.98))
    _draw_footer(fig, footer_lines, JAPANESE_FONT)

    plt.savefig(output_path, dpi=100)
    plt.close()

    print(f"✅ {title}グラフを生成: {output_path} (800x500px)")


def _ranking_eligibility_for(
    counts_all: dict[str, dict[int, float]], window_data: dict[str, dict[int, float]]
) -> dict[str, set[int]]:
    """実数の全期間データから、チャートの窓の各期間が順位付けに使えるかを判定する"""
    window_periods = sorted({p for periods in window_data.values() for p in periods})
    count_baseline = calculate_seasonal_baseline(counts_all, window_periods, years=5)
    return build_ranking_eligibility(counts_all, count_baseline, window_periods)


def main() -> int:
    """メイン処理

    Returns:
        終了コード (0: 正常、1: 日本語フォントを用意できない、2: 入力ファイルが無い)。
        異常時は PNG を 1 枚も書かない (豆腐や空のグラフを公開しないため)
    """
    print("📊 感染症データ可視化グラフ生成 (CDCスタイル)")
    print("=" * 50)

    # データディレクトリ
    data_dir = Path("data/raw")
    output_dir = Path("docs/images")

    # 日本語フォントを最初に確認する (取得できなければ PNG を書く前に失敗させる)
    if get_japanese_font() is None:
        print(
            "::error title=Japanese font unavailable::日本語フォントを取得・照合・登録できないため"
            "グラフを生成しません (FONT_URL / FONT_SHA256 を確認してください)"
        )
        return 1

    print("\n📥 データ読み込み中...")

    # 直近52週(1年間)/12ヶ月のデータ
    sentinel_weekly_data = get_recent_weeks_data(data_dir, num_weeks=52)
    notifiable_weekly_data = get_notifiable_weeks_data(data_dir, num_weeks=52)
    monthly_data = get_recent_months_data(data_dir, num_months=12)

    print(f"✅ 定点週次: {len(sentinel_weekly_data)}種類")
    print(f"✅ 全数報告週次: {len(notifiable_weekly_data)}種類")
    print(f"✅ 定点月次: {len(monthly_data)}種類")

    # 全データ (季節性ベースライン計算用)
    print("\n📥 季節性ベースライン計算用データ読み込み中...")
    all_sentinel_weeks = get_all_weeks_data(data_dir)
    all_notifiable_weeks = get_all_notifiable_weeks_data(data_dir)
    all_months = get_all_months_data(data_dir)

    # 乖離率ランキングの件数下限用の実数 (定点は男女合計。全数の報告数はもともと実数)
    sentinel_week_counts = get_all_weeks_data(data_dir, parser=parse_sentinel_gender_counts)
    sentinel_month_counts = get_all_months_data(data_dir, parser=parse_sentinel_gender_counts)

    if not sentinel_weekly_data and not notifiable_weekly_data and not monthly_data:
        print("::error title=Chart input error::no chart input files under data/raw")
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n🎨 グラフ生成中...")

    # データセット対 (推移 + 乖離率) ごとに色マップを共有し、
    # 両チャートに登場する疾患は同色、乖離率のみの疾患は別系統色を割り当てる。
    # 乖離率の選定はチャート内と同じく filter_rates_for_ranking を通した値で行う。

    # 1+2. 週次定点
    if sentinel_weekly_data:
        sw_abs_top = select_top_absolute_diseases(sentinel_weekly_data, top_n=5)
        sw_dev_diseases: list[str] = []
        seasonal_baseline: dict[str, dict[int, float]] = {}
        sw_eligible = _ranking_eligibility_for(sentinel_week_counts, sentinel_weekly_data)
        if all_sentinel_weeks:
            recent_week_periods = sorted({p for periods in sentinel_weekly_data.values() for p in periods})
            seasonal_baseline = calculate_seasonal_baseline(all_sentinel_weeks, recent_week_periods, years=5)
            sw_dev_rates = calculate_deviation_rate(sentinel_weekly_data, seasonal_baseline)
            sw_dev_top, _ = select_top_deviation_diseases(
                filter_rates_for_ranking(sentinel_weekly_data, sw_dev_rates, sw_eligible), top_n=5
            )
            sw_dev_diseases = [d for d, _ in sw_dev_top]
        sw_style_map = build_consistent_style_map([d for d, _ in sw_abs_top], sw_dev_diseases)

        generate_absolute_chart(
            sentinel_weekly_data,
            output_dir / "sentinel_weekly_absolute.png",
            title="定点報告疾患の週次推移",
            ylabel="患者数 (定点医療機関あたり)",
            data_source="データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
            period_type="week",
            top_n=5,
            style_map=sw_style_map,
        )

        if all_sentinel_weeks:
            generate_deviation_chart(
                sentinel_weekly_data,
                seasonal_baseline,
                output_dir / "sentinel_weekly_deviation.png",
                title="定点報告疾患の週次乖離率 (流行検知)",
                data_source="データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
                period_type="week",
                top_n=5,
                style_map=sw_style_map,
                eligible_periods=sw_eligible,
            )

    # 3+4. 週次全数 (最新週は速報値)
    if notifiable_weekly_data:
        nw_abs_top = select_top_absolute_diseases(notifiable_weekly_data, top_n=5)
        nw_dev_diseases: list[str] = []
        notifiable_seasonal_baseline: dict[str, dict[int, float]] = {}
        nw_eligible: dict[str, set[int]] = {}
        if all_notifiable_weeks:
            recent_notifiable_periods = sorted({p for periods in notifiable_weekly_data.values() for p in periods})
            notifiable_seasonal_baseline = calculate_seasonal_baseline(
                all_notifiable_weeks, recent_notifiable_periods, years=5
            )
            # 報告数はそのまま実数なので、ベースラインもそのまま件数下限の判定に使える
            nw_eligible = build_ranking_eligibility(
                all_notifiable_weeks, notifiable_seasonal_baseline, recent_notifiable_periods
            )
            nw_dev_rates = calculate_deviation_rate(notifiable_weekly_data, notifiable_seasonal_baseline)
            nw_dev_top, _ = select_top_deviation_diseases(
                filter_rates_for_ranking(notifiable_weekly_data, nw_dev_rates, nw_eligible), top_n=5
            )
            nw_dev_diseases = [d for d, _ in nw_dev_top]
        nw_style_map = build_consistent_style_map([d for d, _ in nw_abs_top], nw_dev_diseases)

        generate_absolute_chart(
            notifiable_weekly_data,
            output_dir / "notifiable_weekly_absolute.png",
            title="全数報告疾患の週次推移",
            ylabel="報告数 (実数)",
            data_source="データソース: 東京都感染症発生動向調査(全数週次報告)",
            period_type="week",
            top_n=5,
            style_map=nw_style_map,
            provisional_latest=True,
        )

        if all_notifiable_weeks:
            generate_deviation_chart(
                notifiable_weekly_data,
                notifiable_seasonal_baseline,
                output_dir / "notifiable_weekly_deviation.png",
                title="全数報告疾患の週次乖離率 (流行検知)",
                data_source="データソース: 東京都感染症発生動向調査(全数週次報告)",
                period_type="week",
                top_n=5,
                style_map=nw_style_map,
                eligible_periods=nw_eligible,
                provisional_latest=True,
            )

    # 5+6. 月次定点
    if monthly_data:
        mo_abs_top = select_top_absolute_diseases(monthly_data, top_n=5)
        mo_dev_diseases: list[str] = []
        monthly_seasonal_baseline: dict[str, dict[int, float]] = {}
        mo_eligible = _ranking_eligibility_for(sentinel_month_counts, monthly_data)
        if all_months:
            recent_month_periods = sorted({p for periods in monthly_data.values() for p in periods})
            monthly_seasonal_baseline = calculate_seasonal_baseline(all_months, recent_month_periods, years=5)
            mo_dev_rates = calculate_deviation_rate(monthly_data, monthly_seasonal_baseline)
            mo_dev_top, _ = select_top_deviation_diseases(
                filter_rates_for_ranking(monthly_data, mo_dev_rates, mo_eligible), top_n=5
            )
            mo_dev_diseases = [d for d, _ in mo_dev_top]
        mo_style_map = build_consistent_style_map([d for d, _ in mo_abs_top], mo_dev_diseases)

        generate_absolute_chart(
            monthly_data,
            output_dir / "sentinel_monthly_absolute.png",
            title="定点報告疾患の月次推移",
            ylabel="患者数 (定点医療機関あたり)",
            data_source="データソース: 東京都感染症発生動向調査(定点月次・性別報告)",
            period_type="month",
            top_n=5,
            style_map=mo_style_map,
        )

        if all_months:
            generate_deviation_chart(
                monthly_data,
                monthly_seasonal_baseline,
                output_dir / "sentinel_monthly_deviation.png",
                title="定点報告疾患の月次乖離率 (流行検知)",
                data_source="データソース: 東京都感染症発生動向調査(定点月次・性別報告)",
                period_type="month",
                top_n=5,
                style_map=mo_style_map,
                eligible_periods=mo_eligible,
            )

    print("\n✅ グラフ生成完了 (6枚)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
