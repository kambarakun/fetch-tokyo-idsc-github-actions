#!/usr/bin/env python3
"""
感染症データの可視化グラフ生成スクリプト

最新の感染症データから以下のグラフを生成:
1. 主要感染症の週次推移(直近12週)
2. 最新週のトップ10疾患
"""

import csv
from collections import defaultdict
from pathlib import Path
from urllib import request

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
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
        url = "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf"
        try:
            request.urlretrieve(url, font_path)
            print(f"✅ フォントをダウンロード: {font_path}")
        except Exception as e:
            print(f"⚠️ フォントのダウンロードに失敗: {e}")
            return None

    # フォントを登録してFontPropertiesオブジェクトを返す
    try:
        # フォントを明示的に追加
        fm.fontManager.addfont(str(font_path))

        # キャッシュを再構築
        fm._load_fontmanager(try_read_cache=False)
        # FontPropertiesオブジェクトを作成
        font_prop = fm.FontProperties(fname=str(font_path))

        # グローバル設定も試みる
        plt.rcParams["font.family"] = font_prop.get_name()
        plt.rcParams["axes.unicode_minus"] = False

        print(f"✅ 日本語フォントを設定: {font_prop.get_name()}")
        return font_prop
    except Exception as e:
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


# フォント設定を実行(グローバル変数として保存)
JAPANESE_FONT = setup_japanese_font()

# Seabornスタイル設定
sns.set_style("whitegrid")
sns.set_palette("husl")


def read_csv_shift_jis(file_path: Path) -> list[list[str]]:
    """Shift_JISエンコードのCSVファイルを読み込む"""
    with file_path.open(encoding="shift_jis", errors="ignore") as f:
        reader = csv.reader(f)
        return list(reader)


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
        if disease_name and value_str and value_str not in ["*", "-", "", "0"]:
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


def get_recent_weeks_data(data_dir: Path, num_weeks: int = 12) -> dict[str, dict[int, float]]:
    """直近N週のデータを取得

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
        # ファイル名から年週を抽出: sentinel_weekly_gender_YYYY_WW.csv
        parts = file_path.stem.split("_")
        if len(parts) >= 5:  # sentinel, weekly, gender, YYYY, WW
            try:
                year = int(parts[3])
                week = int(parts[4])
                week_key = year * 100 + week  # 202550形式
            except (ValueError, IndexError):
                continue

            disease_data = parse_sentinel_weekly_gender(file_path)
            for disease, value in disease_data.items():
                all_data[disease][week_key] = value

    return dict(all_data)


def generate_weekly_trend_chart(data: dict[str, dict[int, float]], output_path: Path, top_n: int = 5) -> None:
    """週次推移グラフを生成(CDCスタイル)"""
    if not data:
        print("警告: データが空のため、グラフを生成できません")
        return

    # 最新週のトップN疾患を選択
    latest_week = max(max(weeks.keys()) for weeks in data.values() if weeks)
    latest_values = {disease: weeks.get(latest_week, 0) for disease, weeks in data.items()}
    top_diseases = sorted(latest_values.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # グラフ作成 (800x500px固定サイズ)
    fig, ax = plt.subplots(figsize=(8, 5))

    for disease, _ in top_diseases:
        weeks = sorted(data[disease].keys())
        values = [data[disease][w] for w in weeks]

        # 週番号を読みやすい形式に変換
        week_labels = [f"{w // 100}年第{w % 100}週" for w in weeks]

        # 折れ線グラフ(CDCスタイル:シンプルに)
        ax.plot(range(len(weeks)), values, marker="o", linewidth=2.5, label=disease, markersize=5)

    # 軸ラベルとタイトルを設定(日本語フォント適用)
    if JAPANESE_FONT:
        ax.set_xlabel("", fontsize=12, fontproperties=JAPANESE_FONT)  # X軸ラベル削除(CDCスタイル)
        ax.set_ylabel("定点あたり患者数", fontsize=11, fontproperties=JAPANESE_FONT)
        ax.set_title(
            "主要感染症の週次推移",
            fontsize=13,
            fontweight="bold",
            fontproperties=JAPANESE_FONT,
            pad=15,
        )
        ax.legend(loc="upper left", fontsize=9, prop=JAPANESE_FONT, frameon=False)
    else:
        ax.set_xlabel("", fontsize=12)
        ax.set_ylabel("定点あたり患者数", fontsize=11)
        ax.set_title("主要感染症の週次推移", fontsize=13, fontweight="bold", pad=15)
        ax.legend(loc="upper left", fontsize=9, frameon=False)

    # CDCスタイル: グリッド線を薄く、水平線のみ
    ax.grid(True, axis="y", alpha=0.2, linestyle="-", linewidth=0.5)
    ax.grid(False, axis="x")

    # 背景を白に
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    # 枠線を薄く
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("#CCCCCC")

    # X軸のラベルを調整(全部表示すると混雑するので一部のみ)
    tick_positions = range(0, len(weeks), max(1, len(weeks) // 6))
    ax.set_xticks(tick_positions)
    tick_labels = ax.set_xticklabels([week_labels[i] for i in tick_positions], rotation=45, ha="right", fontsize=9)
    if JAPANESE_FONT:
        for label in tick_labels:
            label.set_fontproperties(JAPANESE_FONT)

    # データソースを追加(CDCスタイル)
    if JAPANESE_FONT:
        fig.text(
            0.99,
            0.01,
            "データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
            ha="right",
            fontsize=7,
            color="#666666",
            fontproperties=JAPANESE_FONT,
        )
    else:
        fig.text(
            0.99,
            0.01,
            "データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
            ha="right",
            fontsize=7,
            color="#666666",
        )

    plt.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close()

    print(f"✅ 週次推移グラフを生成: {output_path} (800x500px)")


def generate_top_diseases_chart(latest_data: dict[str, float], output_path: Path, top_n: int = 10) -> None:
    """最新週のトップN疾患の横棒グラフを生成(CDCスタイル)"""
    if not latest_data:
        print("警告: データが空のため、グラフを生成できません")
        return

    # トップN疾患を選択
    top_diseases = sorted(latest_data.items(), key=lambda x: x[1], reverse=True)[:top_n]
    diseases = [d[0] for d in top_diseases]
    values = [d[1] for d in top_diseases]

    # グラフ作成 (800x500px固定サイズ)
    fig, ax = plt.subplots(figsize=(8, 5))

    # 色分け(上位3つを強調、CDCスタイル)
    colors = ["#e74c3c" if i < 3 else "#3498db" for i in range(len(diseases))]

    ax.barh(range(len(diseases)), values, color=colors, alpha=0.85)
    ax.set_yticks(range(len(diseases)))

    # Y軸ラベル(疾患名)に日本語フォント適用
    ytick_labels = ax.set_yticklabels(diseases, fontsize=10)
    if JAPANESE_FONT:
        for label in ytick_labels:
            label.set_fontproperties(JAPANESE_FONT)

    # 軸ラベルとタイトルを設定(日本語フォント適用、CDCスタイル)
    if JAPANESE_FONT:
        ax.set_xlabel("定点あたり患者数", fontsize=11, fontproperties=JAPANESE_FONT)
        ax.set_title(
            f"感染症トップ{top_n}",
            fontsize=13,
            fontweight="bold",
            fontproperties=JAPANESE_FONT,
            pad=15,
        )
    else:
        ax.set_xlabel("定点あたり患者数", fontsize=11)
        ax.set_title(f"感染症トップ{top_n}", fontsize=13, fontweight="bold", pad=15)

    # CDCスタイル: グリッド線を薄く、縦線のみ
    ax.grid(True, axis="x", alpha=0.2, linestyle="-", linewidth=0.5)
    ax.grid(False, axis="y")

    # 背景を白に
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    # 枠線を薄く
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
        spine.set_color("#CCCCCC")

    # 値をバーの右側に表示(CDCスタイル:シンプルに)
    for i, (_disease, value) in enumerate(top_diseases):
        text = ax.text(value + 0.5, i, f"{value:.2f}", va="center", fontsize=9)
        if JAPANESE_FONT:
            text.set_fontproperties(JAPANESE_FONT)

    # データソースを追加(CDCスタイル)
    if JAPANESE_FONT:
        fig.text(
            0.99,
            0.01,
            "データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
            ha="right",
            fontsize=7,
            color="#666666",
            fontproperties=JAPANESE_FONT,
        )
    else:
        fig.text(
            0.99,
            0.01,
            "データソース: 東京都感染症発生動向調査(定点週次・性別報告)",
            ha="right",
            fontsize=7,
            color="#666666",
        )

    plt.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close()

    print(f"✅ トップ疾患グラフを生成: {output_path} (800x500px)")


def main():
    """メイン処理"""
    print("📊 感染症データ可視化グラフ生成")
    print("=" * 50)

    # データディレクトリ
    data_dir = Path("data/raw")
    output_dir = Path("docs/images")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 直近12週のデータを取得
    print("\n📥 データ読み込み中...")
    recent_data = get_recent_weeks_data(data_dir, num_weeks=12)

    if not recent_data:
        print("❌ データが見つかりませんでした")
        return

    print(f"✅ {len(recent_data)}種類の感染症データを読み込みました")

    # 最新週のデータを取得
    latest_week = max(max(weeks.keys()) for weeks in recent_data.values() if weeks)
    latest_data = {disease: weeks.get(latest_week, 0) for disease, weeks in recent_data.items()}

    # グラフ生成
    print("\n🎨 グラフ生成中...")
    generate_weekly_trend_chart(recent_data, output_dir / "weekly_trend.png", top_n=5)
    generate_top_diseases_chart(latest_data, output_dir / "top_diseases.png", top_n=10)

    print("\n✅ グラフ生成完了")


if __name__ == "__main__":
    main()
