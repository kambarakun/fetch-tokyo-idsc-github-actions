#!/usr/bin/env python3
"""
README.md統計情報更新スクリプト

メタデータから統計情報を抽出し、README.mdの指定セクションを動的に更新します。
"""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


def get_metadata_stats() -> dict:
    """メタデータディレクトリから統計情報を取得"""
    metadata_dir = Path("data/raw/.metadata")

    if not metadata_dir.exists():
        return {
            "total_files": 0,
            "date_range": "データなし",
            "latest_update": "N/A",
            "data_types": {},
            "year_range": "N/A",
            "latest_week": "N/A",
            "latest_month": "N/A",
        }

    # 全メタデータファイルを処理
    all_files = []
    data_type_counts: Counter[str] = Counter()
    years = []
    weekly_data = []
    monthly_data = []
    # データタイプごとの期間情報を保存
    data_type_periods: dict[str, list[tuple[int, int]]] = {}

    for json_file in metadata_dir.glob("*.json"):
        if json_file.name == "hash_index.json":
            continue

        try:
            with json_file.open(encoding="utf-8") as f:
                data = json.load(f)

            all_files.append(data)

            # データタイプ別カウント
            data_type = data.get("data_type")
            if data_type:
                data_type_counts[data_type] += 1

            # 年の収集
            if "temporal" in data and "year" in data["temporal"]:
                years.append(data["temporal"]["year"])

                # 週次・月次データの収集
                period_type = data["temporal"].get("period_type")
                period = data["temporal"].get("period")
                year = data["temporal"]["year"]

                if period_type == "weekly":
                    weekly_data.append((year, period))
                elif period_type == "monthly":
                    monthly_data.append((year, period))

                # データタイプごとの期間情報を収集
                if data_type and year and period:
                    if data_type not in data_type_periods:
                        data_type_periods[data_type] = []
                    data_type_periods[data_type].append((year, period))

        except Exception as e:
            print(f"警告: {json_file.name} の読み込みに失敗: {e}")
            continue

    # 統計情報の集計
    if not all_files:
        return {
            "total_files": 0,
            "date_range": "データなし",
            "latest_update": "N/A",
            "data_types": {},
            "year_range": "N/A",
            "latest_week": "N/A",
            "latest_month": "N/A",
        }

    # 最終更新日時の取得
    latest_modified = max(
        (
            datetime.fromisoformat(f["modified"]).replace(tzinfo=None) if "modified" in f else datetime.now()
            for f in all_files
        ),
        default=datetime.now(),
    )

    # 最新データ取得日時の取得 (created フィールドから)
    latest_created = max(
        (
            datetime.fromisoformat(f["created"]).replace(tzinfo=None) if "created" in f else datetime.now()
            for f in all_files
        ),
        default=datetime.now(),
    )

    # 年の範囲
    min_year = min(years) if years else "N/A"
    max_year = max(years) if years else "N/A"

    # 最新の週次・月次データ
    latest_week_tuple = max(weekly_data) if weekly_data else (0, 0)
    latest_week = f"{latest_week_tuple[0]}年第{latest_week_tuple[1]}週" if weekly_data else "N/A"

    latest_month_tuple = max(monthly_data) if monthly_data else (0, 0)
    latest_month = f"{latest_month_tuple[0]}年{latest_month_tuple[1]}月" if monthly_data else "N/A"

    # データ期間を簡潔に表示 (週次と月次の両方)
    min_week_tuple = min(weekly_data) if weekly_data else (0, 0)
    week_range = (
        f"{min_week_tuple[0]}年第{min_week_tuple[1]}週 〜 {latest_week_tuple[0]}年第{latest_week_tuple[1]}週"
        if weekly_data
        else "N/A"
    )

    min_month_tuple = min(monthly_data) if monthly_data else (0, 0)
    month_range = (
        f"{min_month_tuple[0]}年{min_month_tuple[1]}月 〜 {latest_month_tuple[0]}年{latest_month_tuple[1]}月"
        if monthly_data
        else "N/A"
    )

    # 週数・月数をカウント (重複を除外)
    unique_weeks = len(set(weekly_data))
    unique_months = len(set(monthly_data))

    return {
        "total_files": len(all_files),
        "week_range": week_range,
        "month_range": month_range,
        "latest_fetch": latest_created.strftime("%Y-%m-%d %H:%M JST"),
        "latest_update": latest_modified.strftime("%Y-%m-%d %H:%M JST"),
        "data_types": dict(data_type_counts.most_common()),
        "data_type_periods": data_type_periods,
        "year_range": f"{min_year}年〜{max_year}年",
        "latest_week": latest_week,
        "latest_month": latest_month,
        "years": sorted(set(years)),
        "week_count": unique_weeks,
        "month_count": unique_months,
    }


def format_data_type_table(data_types: dict[str, int], data_type_periods: dict[str, list[tuple[int, int]]]) -> str:
    """データ種別を表形式で整形 (期間と欠損情報を含む)"""
    # データ種別の日本語名マッピング
    type_names = {
        "sentinel_weekly_health_center": "定点週次・保健所別",
        "sentinel_weekly_age": "定点週次・年齢群",
        "notifiable_weekly": "全数週次",
        "sentinel_weekly_medical_district": "定点週次・医療圏別",
        "sentinel_weekly_gender": "定点週次・性別",
        "sentinel_monthly_medical_district": "定点月次・医療圏別",
        "sentinel_monthly_health_center": "定点月次・保健所別",
        "sentinel_monthly_age": "定点月次・年齢群",
        "sentinel_monthly_gender": "定点月次・性別",
    }

    # 表のヘッダー
    lines = [
        "| データ種別 | 件数 | データ期間 | 欠損 |",
        "|-----------|------|-----------|------|",
    ]

    # データ行
    for data_type, count in data_types.items():
        display_name = type_names.get(data_type, data_type)

        # 期間情報を取得
        periods = data_type_periods.get(data_type, [])
        if periods:
            min_period = min(periods)
            max_period = max(periods)

            # 週次 or 月次かを判定
            is_weekly = "weekly" in data_type
            if is_weekly:
                period_range = f"{min_period[0]}年第{min_period[1]}週〜{max_period[0]}年第{max_period[1]}週"
            else:
                period_range = f"{min_period[0]}年{min_period[1]}月〜{max_period[0]}年{max_period[1]}月"

            # 欠損を検出 (簡易版: 最初と最後の期間から期待される件数を計算)
            missing = _detect_missing_periods(data_type, periods)
        else:
            period_range = "N/A"
            missing = "N/A"

        lines.append(f"| {display_name} | {count:,}件 | {period_range} | {missing} |")

    return "\n".join(lines)


def _detect_missing_periods(data_type: str, periods: list[tuple[int, int]]) -> str:
    """欠損期間を検出"""
    if not periods:
        return "N/A"

    min_period = min(periods)
    max_period = max(periods)

    # 期待される期間をすべて生成
    is_weekly = "weekly" in data_type
    expected_periods = set()

    if is_weekly:
        # 週次データ: 各年52-53週
        for year in range(min_period[0], max_period[0] + 1):
            max_week = 53 if year in [2004, 2009, 2015, 2020] else 52
            start_week = min_period[1] if year == min_period[0] else 1
            end_week = max_period[1] if year == max_period[0] else max_week
            for week in range(start_week, end_week + 1):
                expected_periods.add((year, week))
    else:
        # 月次データ: 各年12ヶ月
        for year in range(min_period[0], max_period[0] + 1):
            start_month = min_period[1] if year == min_period[0] else 1
            end_month = max_period[1] if year == max_period[0] else 12
            for month in range(start_month, end_month + 1):
                expected_periods.add((year, month))

    # 実際の期間との差分を計算
    actual_periods = set(periods)
    missing_periods = expected_periods - actual_periods

    if not missing_periods:
        return "なし"

    # 欠損が多い場合は件数のみ表示
    if len(missing_periods) > 5:
        return f"{len(missing_periods)}件"

    # 欠損が少ない場合は詳細を表示
    missing_list = sorted(missing_periods)
    if is_weekly:
        missing_str = ", ".join([f"{y}年第{p}週" for y, p in missing_list[:5]])
    else:
        missing_str = ", ".join([f"{y}年{p}月" for y, p in missing_list[:5]])

    if len(missing_periods) > 5:
        missing_str += f"他{len(missing_periods) - 5}件"

    return missing_str


def update_readme(stats: dict) -> bool:
    """README.mdの統計セクションを更新"""
    readme_path = Path("README.md")

    if not readme_path.exists():
        print("エラー: README.mdが見つかりません")
        return False

    # README.mdを読み込み
    with readme_path.open(encoding="utf-8") as f:
        content = f.read()

    # 統計情報セクションを生成
    stats_section = f"""<!-- start data-statistics -->
## 📊 データ収集状況（自動更新）

### 📅 最新データ

| 項目 | 値 |
|------|-----|
| **最新週次データ** | {stats['latest_week']} |
| **最新月次データ** | {stats['latest_month']} |
| **最新データ取得日時** | {stats['latest_fetch']} |
| **ファイル最終更新日時** | {stats['latest_update']} |

### 📈 収集統計

| 項目 | 値 |
|------|-----|
| **総データ件数** | {stats['total_files']:,}件 |
| **週次データ期間** | {stats['week_range']} |
| **月次データ期間** | {stats['month_range']} |
| **収集週数** | {stats['week_count']:,}週 |
| **収集月数** | {stats['month_count']:,}ヶ月 |
| **データ種別数** | {len(stats['data_types'])}種類 |

### 📋 データ種別内訳

{format_data_type_table(stats['data_types'], stats['data_type_periods'])}

> 💡 このセクションは `scripts/update_readme_stats.py` により自動生成されています。
<!-- end data-statistics -->"""

    # マーカーが存在するかチェック
    if "<!-- start data-statistics -->" in content:
        # 既存のセクションを更新
        pattern = r"<!-- start data-statistics -->.*?<!-- end data-statistics -->"
        new_content = re.sub(pattern, stats_section, content, flags=re.DOTALL)
    else:
        # マーカーが存在しない場合は、バッジセクションの後に挿入
        # バッジの最後 (License行の後) を探す
        license_pattern = r"(\[!\[License\].*?\n)"
        match = re.search(license_pattern, content)

        if match:
            # ライセンスバッジの後に挿入
            insert_pos = match.end()
            new_content = content[:insert_pos] + "\n" + stats_section + "\n" + content[insert_pos:]
        else:
            # バッジが見つからない場合は先頭に挿入
            first_heading = re.search(r"^##\s+", content, re.MULTILINE)
            if first_heading:
                insert_pos = first_heading.start()
                new_content = content[:insert_pos] + stats_section + "\n\n" + content[insert_pos:]
            else:
                print("警告: 適切な挿入位置が見つかりません")
                return False

    # 変更があった場合のみ書き込み
    if new_content != content:
        with readme_path.open("w", encoding="utf-8") as f:
            f.write(new_content)
        print("✅ README.md の統計情報を更新しました")
        return True
    print("ℹ️ README.md に変更はありません")
    return False


def main():
    """メイン処理"""
    print("📊 README.md統計情報更新スクリプト")
    print("=" * 50)

    # メタデータから統計情報を取得
    print("メタデータを読み込んでいます...")
    stats = get_metadata_stats()

    print(f"✅ {stats['total_files']}件のメタデータを読み込みました")
    print(f"   週次: {stats['week_range']}")
    print(f"   月次: {stats['month_range']}")
    print(f"   最終更新: {stats['latest_update']}")

    # README.mdを更新
    print("\nREADME.mdを更新しています...")
    updated = update_readme(stats)

    if updated:
        print("\n✅ 更新完了")
    else:
        print("\n✅ 処理完了（変更なし）")


if __name__ == "__main__":
    main()
