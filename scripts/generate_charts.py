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

import csv
import hashlib
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import requests
import seaborn as sns


def setup_japanese_font():
    """日本語フォントを設定(Noto Sans CJK JPを使用)"""
    # フォントディレクトリ
    font_dir = Path.home() / ".local" / "share" / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    font_path = font_dir / "NotoSansCJKjp-Regular.otf"

    # フォントが存在しない場合はダウンロード
    if not font_path.exists():
        print("📥 日本語フォント (Noto Sans CJK JP) をダウンロード中...")
        # セキュリティ: 許可されたURLのみ使用可能
        ALLOWED_FONT_URL = "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf"
        # セキュリティ: 期待されるSHA256ハッシュ (フォントファイルの整合性検証用)
        # 注: 初回実行時にダウンロードしたフォントのハッシュを確認して設定すること
        # EXPECTED_SHA256 = "..."  # 本番環境では実際のハッシュ値を設定

        try:
            # タイムアウト30秒、リダイレクト禁止でセキュリティ強化
            response = requests.get(ALLOWED_FONT_URL, timeout=30, allow_redirects=False)
            response.raise_for_status()

            # ダウンロードしたデータのSHA256ハッシュを計算
            downloaded_data = response.content
            sha256_hash = hashlib.sha256(downloaded_data).hexdigest()
            print(f"[INFO] ダウンロードしたフォントのSHA256: {sha256_hash}")

            # 注: 本番環境では以下のハッシュ検証を有効化すること
            # if sha256_hash != EXPECTED_SHA256:
            #     print("[WARNING] セキュリティエラー: フォントファイルのハッシュが一致しません")
            #     print(f"   期待値: {EXPECTED_SHA256}")
            #     print(f"   実際値: {sha256_hash}")
            #     return None

            # ダウンロードしたデータを保存
            font_path.write_bytes(downloaded_data)
            print(f"[SUCCESS] フォントをダウンロード: {font_path}")
        except requests.exceptions.RequestException as e:
            print(f"[WARNING] フォントのダウンロードに失敗: {e}")
            return None
        except OSError as e:
            print(f"[WARNING] フォントの保存に失敗: {e}")
            return None

    # フォントを登録してFontPropertiesオブジェクトを返す
    try:
        # フォントを明示的に追加 (matplotlib 3.10.8以降はキャッシュ自動更新)
        fm.fontManager.addfont(str(font_path))

        # FontPropertiesオブジェクトを作成
        font_prop = fm.FontProperties(fname=str(font_path))

        # グローバル設定も試みる
        plt.rcParams["font.family"] = font_prop.get_name()
        plt.rcParams["axes.unicode_minus"] = False

        print(f"✅ 日本語フォントを設定: {font_prop.get_name()}")
    except (OSError, ValueError) as e:
        print(f"⚠️ フォントの設定に失敗: {e}")
        # フォールバック: システムの日本語フォントを試す
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [
            "Hiragino Sans",
            "Hiragino Kaku Gothic Pro",
            "Yu Gothic",
            "Meirio",
        ]
        plt.rcParams["axes.unicode_minus"] = False
        return None
    else:
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


def parse_sentinel_weekly_gender(csv_path: Path) -> dict[str, float]:
    """定点週次・性別データから疾患別患者数を抽出

    Returns:
        疾患名 -> 定点あたり患者数 (男女合計列) のdict
    """
    rows = read_csv_shift_jis(csv_path)

    # ヘッダー行を探す(疾病名男性女性男女合計を含む行)
    header_row = None
    total_col_idx = None

    for i, row in enumerate(rows):
        if len(row) >= 4 and "疾病名" in str(row[0]):
            header_row = i
            # 男女合計列のインデックスを探す
            for j, cell in enumerate(row):
                if "男女合計" in str(cell):
                    total_col_idx = j
                    break
            break

    if header_row is None or total_col_idx is None:
        return {}

    # データ行を読み込み(ヘッダーの次の行から)
    disease_data = {}
    for i in range(header_row + 1, len(rows)):
        row = rows[i]
        if len(row) <= total_col_idx:
            continue

        disease_name = str(row[0]).strip()
        value_str = str(row[total_col_idx]).strip()

        # 疾患名と患者数が有効な場合のみ追加
        # 注: 0のデータも含める (線の連続性のため)
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
        # 注: 0のデータも含める (線の連続性のため)
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


def get_all_weeks_data(data_dir: Path) -> dict[str, dict[int, float]]:
    """全週次データを取得 (季節性ベースライン計算用)

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

        disease_data = parse_sentinel_weekly_gender(file_path)
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


def get_all_months_data(data_dir: Path) -> dict[str, dict[int, float]]:
    """全月次データを取得 (季節性ベースライン計算用)

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

        disease_data = parse_sentinel_monthly_gender(file_path)
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

            # 平均を計算 (データが3年分以上ある場合のみ)
            # データ不足の場合はキーを設定しない (None の代わり)
            if len(historical_values) >= 3:
                baseline_data[period] = sum(historical_values) / len(historical_values)
            # else: データ不足時はキーを設定しない

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
            if baseline_value > 0:
                # 通常の計算
                deviation = ((value - baseline_value) / baseline_value) * 100
                rate_data[period] = deviation
            elif baseline_value == 0:
                # ベースラインが0の場合
                if value == 0:
                    # 実測値も0なら乖離率0% (変化なし)
                    rate_data[period] = 0.0
                else:
                    # 実測値>0でベースライン0の場合は非常に大きな正の乖離
                    # グラフの連続性のため、上限値として+999%を設定
                    rate_data[period] = 999.0
            # else: baseline_value < 0 は理論上ありえないのでスキップ

        deviation_rates[disease] = rate_data

    return deviation_rates


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
        # 週番号ベースで5の倍数を表示 (最小・最大は必ず含む)
        week_numbers = [p % 100 for p in all_periods]
        min_week = min(week_numbers)
        max_week = max(week_numbers)

        # 表示する週番号を決定 (5の倍数 + 最小・最大)
        display_weeks = set()
        display_weeks.add(min_week)  # 最小週
        display_weeks.add(max_week)  # 最大週

        # 5の倍数を追加
        for week in range(0, 55, 5):  # 0, 5, 10, ..., 50
            if min_week <= week <= max_week:
                display_weeks.add(week)

        # インデックスと週番号のマッピング
        tick_positions = []
        tick_labels_list = []
        for i, week in enumerate(week_numbers):
            if week in display_weeks:
                tick_positions.append(i)
                tick_labels_list.append(str(week))
    else:  # month
        # 12ヶ月を全て表示
        tick_positions = list(range(len(all_periods)))
        tick_labels_list = [str(all_periods[i] % 100) for i in tick_positions]

    ax.set_xticks(tick_positions)
    tick_labels = ax.set_xticklabels(tick_labels_list, rotation=0, ha="center", fontsize=10)

    if japanese_font:
        for label in tick_labels:
            label.set_fontproperties(japanese_font)


def _setup_chart_labels(ax, xlabel_text: str, ylabel: str, title: str, note_text: str, japanese_font) -> None:
    """チャートの軸ラベル、タイトル、凡例を設定

    Args:
        ax: Matplotlibの軸オブジェクト
        xlabel_text: X軸ラベル
        ylabel: Y軸ラベル
        title: グラフタイトル
        note_text: 注釈テキスト (未使用 - データソースと統合)
        japanese_font: 日本語フォントプロパティ (None可)
    """
    if japanese_font:
        ax.set_xlabel(xlabel_text, fontsize=11, fontproperties=japanese_font)
        ax.set_ylabel(ylabel, fontsize=12, fontproperties=japanese_font)
        ax.set_title(title, fontsize=24, fontweight="bold", fontproperties=japanese_font, pad=15)
        ax.legend(loc="upper left", fontsize=12, prop=japanese_font, frameon=False)
    else:
        ax.set_xlabel(xlabel_text, fontsize=11)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.set_title(title, fontsize=24, fontweight="bold", pad=15)
        ax.legend(loc="upper left", fontsize=12, frameon=False)


def generate_absolute_chart(
    data: dict[str, dict[int, float]],
    output_path: Path,
    title: str,
    ylabel: str,
    data_source: str,
    period_type: str = "week",
    top_n: int = 5,
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
    latest_period = max(all_periods)
    latest_values = {disease: periods.get(latest_period, 0) for disease, periods in data.items()}
    top_diseases = sorted(latest_values.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # グラフ作成 (800x500px固定サイズ)
    fig, ax = plt.subplots(figsize=(8, 5))

    # 期間の最小・最大を取得
    min_period = min(all_periods)
    max_period = max(all_periods)

    for disease, _ in top_diseases:
        # 全期間に対してデータをマッピング(欠損値はNone)
        values = [data[disease].get(p) for p in all_periods]

        # 最新値を取得(Noneでない最後の値)
        latest_value = next((v for v in reversed(values) if v is not None), 0)

        # 桁数を値に応じて調整(定点データは小数)
        value_format = f"{latest_value:.1f}" if latest_value >= 10 else f"{latest_value:.2f}"

        # 折れ線グラフ (CDCスタイル) - 凡例に最新値を含める
        label_with_value = f"{disease} (最新: {value_format})"
        line = ax.plot(range(len(all_periods)), values, marker="o", linewidth=2.5, label=label_with_value, markersize=5)

        # 最新データポイントにアノテーションを追加(Noneでない最後のポイント)
        if latest_value > 0:
            # 最新値の位置を見つける
            for i in range(len(values) - 1, -1, -1):
                if values[i] is not None:
                    ax.annotate(
                        value_format,
                        xy=(i, latest_value),
                        xytext=(5, 0),
                        textcoords="offset points",
                        fontsize=9,
                        color=line[0].get_color(),
                        fontweight="bold",
                        fontproperties=JAPANESE_FONT if JAPANESE_FONT else None,
                    )
                    break

    # X軸ラベル (期間を明示)
    xlabel_text = _format_period_label(min_period, max_period, period_type)

    # 軸ラベル、タイトル、凡例を設定
    note_text = "※ 最新週の患者数トップ5を表示" if period_type == "week" else "※ 最新月の患者数トップ5を表示"
    _setup_chart_labels(ax, xlabel_text, ylabel, title, note_text, JAPANESE_FONT)

    # CDCスタイルを適用
    _apply_cdc_styling(ax, fig)

    # X軸目盛りを設定
    _setup_x_axis_ticks(ax, all_periods, period_type, JAPANESE_FONT)

    # グラフエリアを縮めて上下にスペースを確保 (上: タイトル用4%, 下: フッター用6%)
    plt.tight_layout(rect=[0, 0.06, 1, 0.96])

    # データソースと注釈 (下側の確保したスペースに配置)
    footer_text = f"{note_text}\n{data_source}"
    if JAPANESE_FONT:
        fig.text(
            0.99, 0.01, footer_text, ha="right", va="bottom", fontsize=8, color="#666666", fontproperties=JAPANESE_FONT
        )
    else:
        fig.text(0.99, 0.01, footer_text, ha="right", va="bottom", fontsize=8, color="#666666")

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

    # 最新期間でプラス方向(流行)の乖離率が大きい疾患のトップNを選択(CDCスタイル)
    latest_period = max(all_periods)
    latest_deviation_rates = {disease: periods.get(latest_period, 0) for disease, periods in deviation_rates.items()}
    # プラス方向(流行)のみを抽出してソート
    positive_deviations = {k: v for k, v in latest_deviation_rates.items() if v > 0}
    top_diseases = sorted(positive_deviations.items(), key=lambda x: x[1], reverse=True)[:top_n]

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

        # 最新値を取得(Noneでない最後の値)
        latest_value = next((v for v in reversed(values) if v is not None), 0)

        # 折れ線グラフ (CDCスタイル) - 凡例に最新値を含める
        label_with_value = f"{disease} (最新: {latest_value:+.0f}%)"
        line = ax.plot(range(len(all_periods)), values, marker="o", linewidth=2.5, label=label_with_value, markersize=5)

        # 最新データポイントにアノテーションを追加(Noneでない最後のポイント)
        if latest_value != 0:
            # 最新値の位置を見つける
            for i in range(len(values) - 1, -1, -1):
                if values[i] is not None:
                    ax.annotate(
                        f"{latest_value:+.0f}%",
                        xy=(i, latest_value),
                        xytext=(5, 0),
                        textcoords="offset points",
                        fontsize=9,
                        color=line[0].get_color(),
                        fontweight="bold",
                        fontproperties=JAPANESE_FONT if JAPANESE_FONT else None,
                    )
                    break

    # ベースライン (0%ライン) を表示
    if len(all_periods) > 0:
        ax.axhline(y=0, color="#999999", linestyle="--", linewidth=1, alpha=0.7)

    # X軸ラベル (期間を明示)
    xlabel_text = _format_period_label(min_period, max_period, period_type)

    # 軸ラベル、タイトル、凡例を設定
    note_text = "※ 季節性ベースラインより高い(プラス乖離)疾患を最大5つ表示"
    _setup_chart_labels(ax, xlabel_text, "季節性ベースラインからの乖離率 (%)", title, note_text, JAPANESE_FONT)

    # CDCスタイルを適用
    _apply_cdc_styling(ax, fig)

    # X軸目盛りを設定
    _setup_x_axis_ticks(ax, all_periods, period_type, JAPANESE_FONT)

    # グラフエリアを縮めて上下にスペースを確保 (上: タイトル用4%, 下: フッター用6%)
    plt.tight_layout(rect=[0, 0.06, 1, 0.96])

    # データソースと注釈 (下側の確保したスペースに配置)
    footer_text = f"{note_text}\n{data_source}"
    if JAPANESE_FONT:
        fig.text(
            0.99, 0.01, footer_text, ha="right", va="bottom", fontsize=8, color="#666666", fontproperties=JAPANESE_FONT
        )
    else:
        fig.text(0.99, 0.01, footer_text, ha="right", va="bottom", fontsize=8, color="#666666")

    plt.savefig(output_path, dpi=100)
    plt.close()

    print(f"✅ {title}グラフを生成: {output_path} (800x500px)")


def main():
    """メイン処理"""
    print("📊 感染症データ可視化グラフ生成 (CDCスタイル)")
    print("=" * 50)

    # データディレクトリ
    data_dir = Path("data/raw")
    output_dir = Path("docs/images")
    output_dir.mkdir(parents=True, exist_ok=True)

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

    if not sentinel_weekly_data and not notifiable_weekly_data and not monthly_data:
        print("❌ データが見つかりませんでした")
        return

    print("\n🎨 グラフ生成中...")

    # 1. 週次定点・絶対数
    if sentinel_weekly_data:
        generate_absolute_chart(
            sentinel_weekly_data,
            output_dir / "sentinel_weekly_absolute.png",
            title="定点報告疾患の週次推移",
            ylabel="患者数 (定点医療機関あたり)",
            data_source="データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
            period_type="week",
            top_n=5,
        )

    # 2. 週次定点・季節性乖離率
    if sentinel_weekly_data and all_sentinel_weeks:
        # 直近52週の期間リスト
        recent_week_periods = sorted({p for periods in sentinel_weekly_data.values() for p in periods})
        seasonal_baseline = calculate_seasonal_baseline(all_sentinel_weeks, recent_week_periods, years=5)

        generate_deviation_chart(
            sentinel_weekly_data,
            seasonal_baseline,
            output_dir / "sentinel_weekly_deviation.png",
            title="定点報告疾患の週次乖離率 (流行検知)",
            data_source="データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
            period_type="week",
            top_n=5,
        )

    # 3. 週次全数・絶対数
    if notifiable_weekly_data:
        generate_absolute_chart(
            notifiable_weekly_data,
            output_dir / "notifiable_weekly_absolute.png",
            title="全数報告疾患の週次推移",
            ylabel="報告数 (実数)",
            data_source="データソース: 東京都感染症発生動向調査(全数週次報告)",
            period_type="week",
            top_n=5,
        )

    # 4. 週次全数・季節性乖離率
    if notifiable_weekly_data and all_notifiable_weeks:
        recent_notifiable_periods = sorted({p for periods in notifiable_weekly_data.values() for p in periods})
        notifiable_seasonal_baseline = calculate_seasonal_baseline(
            all_notifiable_weeks, recent_notifiable_periods, years=5
        )

        generate_deviation_chart(
            notifiable_weekly_data,
            notifiable_seasonal_baseline,
            output_dir / "notifiable_weekly_deviation.png",
            title="全数報告疾患の週次乖離率 (流行検知)",
            data_source="データソース: 東京都感染症発生動向調査(全数週次報告)",
            period_type="week",
            top_n=5,
        )

    # 5. 月次定点・絶対数
    if monthly_data:
        generate_absolute_chart(
            monthly_data,
            output_dir / "sentinel_monthly_absolute.png",
            title="定点報告疾患の月次推移",
            ylabel="患者数 (定点医療機関あたり)",
            data_source="データソース: 東京都感染症発生動向調査(定点月次・性別報告)",
            period_type="month",
            top_n=5,
        )

    # 6. 月次定点・季節性乖離率
    if monthly_data and all_months:
        recent_month_periods = sorted({p for periods in monthly_data.values() for p in periods})
        monthly_seasonal_baseline = calculate_seasonal_baseline(all_months, recent_month_periods, years=5)

        generate_deviation_chart(
            monthly_data,
            monthly_seasonal_baseline,
            output_dir / "sentinel_monthly_deviation.png",
            title="定点報告疾患の月次乖離率 (流行検知)",
            data_source="データソース: 東京都感染症発生動向調査(定点月次・性別報告)",
            period_type="month",
            top_n=5,
        )

    print("\n✅ グラフ生成完了 (6枚)")


if __name__ == "__main__":
    main()
