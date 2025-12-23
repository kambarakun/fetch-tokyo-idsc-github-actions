#!/usr/bin/env python3
"""
README.md統計情報更新スクリプト

メタデータから統計情報を抽出し、README.mdの指定セクションを動的に更新します。
"""

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# 表示する項目数の上限
MAX_DISPLAY_ITEMS = 50


def _get_current_jst_timestamp() -> str:
    """現在のJST日時を統一フォーマットで取得

    ZoneInfo取得に失敗した場合はUTCにフォールバックします。

    Returns:
        str: "YYYY-MM-DD HH:MM JST" 形式の文字列 (JSTが利用可能な場合)
             "YYYY-MM-DD HH:MM UTC" 形式の文字列 (フォールバック時)
    """
    try:
        jst = ZoneInfo("Asia/Tokyo")
        return datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")
    except Exception:
        # フォールバック: UTCを使用
        utc_time = datetime.now(UTC)
        return utc_time.strftime("%Y-%m-%d %H:%M UTC")


def _has_53_weeks(year: int) -> bool:
    """ISO 8601週番号で53週を持つ年かを判定

    53週を持つ年は、以下の条件のいずれかを満たす:
    1. 木曜日で始まる年
    2. 水曜日で始まる閏年
    """
    from datetime import date

    # 12月31日の週番号を確認
    dec_31 = date(year, 12, 31)
    iso_year, iso_week, _ = dec_31.isocalendar()

    # 12月31日がISO週番号で53週目にある場合、その年は53週を持つ
    return iso_year == year and iso_week == 53


def get_metadata_stats() -> dict[str, Any]:
    """メタデータディレクトリから統計情報を取得

    Returns:
        dict[str, Any]: 統計情報を含む辞書
            - total_files (int): メタデータファイルの総数
            - week_range (str): 週次データの期間範囲
            - month_range (str): 月次データの期間範囲
            - latest_fetch (str): 最新データ取得日時 (JST)
            - last_stats_update (str): 最終統計更新日時 (JST)
            - data_types (dict[str, int]): データ種別ごとのファイル数
            - data_type_periods (dict[str, list[tuple[int, int]]]): データ種別ごとの期間リスト
            - year_range (str): 年の範囲
            - latest_week (str): 最新の週次データ
            - latest_month (str): 最新の月次データ
            - years (list[int]): 年のリスト
            - week_count (int): 週次データの総数
            - month_count (int): 月次データの総数
            - anomalies (dict): 異常データ情報
    """
    metadata_dir = Path("data/raw/.metadata")

    if not metadata_dir.exists():
        last_stats_update_str = _get_current_jst_timestamp()
        return {
            "total_files": 0,
            "date_range": "データなし",
            "latest_fetch": "N/A",
            "last_stats_update": last_stats_update_str,
            "data_types": {},
            "year_range": "N/A",
            "latest_week": "N/A",
            "latest_month": "N/A",
            "anomalies": {"errors": {}, "warnings": {}, "quality_issues": {}},
        }

    # 全メタデータファイルを処理
    all_files: list[dict] = []
    data_type_counts: Counter[str] = Counter()
    years: list[int] = []
    weekly_data: list[tuple[int, int]] = []
    monthly_data: list[tuple[int, int]] = []
    # データタイプごとの期間情報を保存
    data_type_periods: dict[str, list[tuple[int, int]]] = {}
    # 異常情報を収集
    anomalies: dict[str, dict[str, Any]] = {"errors": {}, "warnings": {}, "quality_issues": {}}
    # 失敗したファイルを追跡
    failed_files: list[str] = []

    for json_file in metadata_dir.glob("*.json"):
        if json_file.name == "hash_index.json":
            continue

        try:
            with json_file.open(encoding="utf-8") as f:
                data = json.load(f)

            all_files.append(data)

            # 異常情報の収集
            filename = data.get("filename", json_file.stem)

            # verification.errors
            if "verification" in data and "errors" in data["verification"]:
                for error in data["verification"]["errors"]:
                    if error not in anomalies["errors"]:
                        anomalies["errors"][error] = []
                    anomalies["errors"][error].append(filename)

            # verification.warnings
            if "verification" in data and "warnings" in data["verification"]:
                for warning in data["verification"]["warnings"]:
                    if warning not in anomalies["warnings"]:
                        anomalies["warnings"][warning] = []
                    anomalies["warnings"][warning].append(filename)

            # quality.issues (詳細情報も保存)
            if "quality" in data and "issues" in data["quality"]:
                for issue in data["quality"]["issues"]:
                    check_type = issue.get("check_type", "unknown")
                    if check_type not in anomalies["quality_issues"]:
                        anomalies["quality_issues"][check_type] = {
                            "files": [],
                            "details": [],  # affected_count, message などの詳細
                        }
                    anomalies["quality_issues"][check_type]["files"].append(filename)
                    # 詳細情報を保存 (affected_count, message など)
                    anomalies["quality_issues"][check_type]["details"].append(
                        {
                            "filename": filename,
                            "message": issue.get("message", ""),
                            "affected_count": issue.get("details", {}).get("affected_count", 0),
                            "validation_status": issue.get("validation_status", "unknown"),
                        }
                    )

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

        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"警告: {json_file.name} の読み込みに失敗: {e}")
            failed_files.append(json_file.name)
            continue

    # 失敗ファイルのサマリーを表示
    if failed_files:
        print(f"\n⚠️  {len(failed_files)}件のメタデータ読み込みに失敗しました")
        if len(failed_files) > 10:
            print("   多数のエラーが発生しています。データ品質を確認してください。")
            print(f"   失敗ファイル (最初の10件): {', '.join(failed_files[:10])}")
        else:
            print(f"   失敗ファイル: {', '.join(failed_files)}")

    # 統計情報の集計
    if not all_files:
        last_stats_update_str = _get_current_jst_timestamp()
        return {
            "total_files": 0,
            "date_range": "データなし",
            "latest_fetch": "N/A",
            "last_stats_update": last_stats_update_str,
            "data_types": {},
            "year_range": "N/A",
            "latest_week": "N/A",
            "latest_month": "N/A",
            "anomalies": {"errors": {}, "warnings": {}, "quality_issues": {}},
        }

    # 最新データ取得日時の取得 (created フィールドから)
    latest_created_dates: list[datetime] = []
    for file_data in all_files:
        if file_data.get("created"):
            try:
                # ISO形式のタイムスタンプをパース ('Z' を '+00:00' に変換)
                timestamp_str = file_data["created"].replace("Z", "+00:00")
                dt = datetime.fromisoformat(timestamp_str)

                # タイムゾーン情報がない場合はUTCとして扱う、他の場合はUTCに変換
                dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

                latest_created_dates.append(dt)
            except (ValueError, TypeError):
                pass

    latest_created = max(latest_created_dates) if latest_created_dates else datetime.min.replace(tzinfo=UTC)

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
        f"{min_week_tuple[0]}年第{min_week_tuple[1]}週 - {latest_week_tuple[0]}年第{latest_week_tuple[1]}週"
        if weekly_data
        else "N/A"
    )

    min_month_tuple = min(monthly_data) if monthly_data else (0, 0)
    month_range = (
        f"{min_month_tuple[0]}年{min_month_tuple[1]}月 - {latest_month_tuple[0]}年{latest_month_tuple[1]}月"
        if monthly_data
        else "N/A"
    )

    # 週数・月数をカウント (重複を除外)
    unique_weeks = len(set(weekly_data))
    unique_months = len(set(monthly_data))

    # datetime.min の場合は "N/A" と表示 (データなし)
    # UTC から JST (Asia/Tokyo) に変換して表示
    jst = ZoneInfo("Asia/Tokyo")
    latest_fetch_str = (
        "N/A"
        if latest_created == datetime.min.replace(tzinfo=UTC)
        else latest_created.astimezone(jst).strftime("%Y-%m-%d %H:%M JST")
    )
    # スクリプト実行日時 (最終統計更新日時) を取得
    last_stats_update_str = _get_current_jst_timestamp()

    return {
        "total_files": len(all_files),
        "week_range": week_range,
        "month_range": month_range,
        "latest_fetch": latest_fetch_str,
        "last_stats_update": last_stats_update_str,
        "data_types": dict(data_type_counts.most_common()),
        "data_type_periods": data_type_periods,
        "year_range": f"{min_year}年-{max_year}年",
        "latest_week": latest_week,
        "latest_month": latest_month,
        "years": sorted(set(years)),
        "week_count": unique_weeks,
        "month_count": unique_months,
        "anomalies": anomalies,
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
                period_range = f"{min_period[0]}年第{min_period[1]}週-{max_period[0]}年第{max_period[1]}週"
            else:
                period_range = f"{min_period[0]}年{min_period[1]}月-{max_period[0]}年{max_period[1]}月"

            # 欠損を検出 (簡易版: 最初と最後の期間から期待される件数を計算)
            missing = _detect_missing_periods(data_type, periods)
        else:
            period_range = "N/A"
            missing = "N/A"

        lines.append(f"| {display_name} | {count:,}件 | {period_range} | {missing} |")

    return "\n".join(lines)


def format_anomalies_section(anomalies: dict[str, dict[str, Any]]) -> str:
    """異常情報を折りたたみ形式で整形"""

    def _extract_file_sort_key(filename: str) -> tuple[int, int, str]:
        """ファイル名から年・期間を抽出してソートキーを生成 (新しい順)"""
        match = re.search(r"_(\d{4})_(\d{2})\.csv$", filename)
        if match:
            year = int(match.group(1))
            period = int(match.group(2))
            # 降順ソートのため負数にする
            return (-year, -period, filename)
        # パースできない場合は末尾に配置
        return (0, 0, filename)

    # 既知のエラー/警告の解説
    KNOWN_DESCRIPTIONS = {
        # verification.warnings
        "[csv_format] Inconsistent column count": "💡 CSVファイル内で行ごとにカラム数が異なります。東京都IDSCの元データには注釈行や集計期間情報が含まれているため発生します。",
        # verification.errors (将来的に追加される可能性)
        "[file_size] File too large": "⚠️ ファイルサイズが上限を超えています。データ品質を確認してください。",
        "[encoding] Invalid encoding": "⚠️ ファイルのエンコーディングがShift_JISではありません。データ取得プロセスを確認してください。",
        # quality.issues
        "gender_sum_consistency": "🔍 性別データの合計値検証で不整合が検出されました。男性+女性の合計が、元データの男女合計値と一致しません。",
    }

    # 異常の総数を計算
    total_errors = sum(len(files) for files in anomalies["errors"].values())
    total_warnings = sum(len(files) for files in anomalies["warnings"].values())
    total_quality_issues = sum(len(info["files"]) for info in anomalies["quality_issues"].values())

    if total_errors == 0 and total_warnings == 0 and total_quality_issues == 0:
        return "✅ データ品質チェック: 異常なし"

    lines = []

    # === 生データ (raw) の検証 ===
    if total_errors > 0 or total_warnings > 0:
        lines.append("#### 📁 生データ (raw) の検証")
        lines.append("")

    # エラー
    if total_errors > 0:
        lines.append(f"##### ❌ エラー ({total_errors}件)")
        lines.append("")
        for error_type, files in sorted(anomalies["errors"].items()):
            # 解説を追加
            description = KNOWN_DESCRIPTIONS.get(error_type, "")
            if description:
                lines.append(f"> {description}")
                lines.append("")

            lines.append("<details>")
            lines.append(f"<summary><strong>{error_type}</strong> ({len(files)}件)</summary>")
            lines.append("")
            lines.append("```text")
            for file in sorted(files, key=_extract_file_sort_key)[:MAX_DISPLAY_ITEMS]:  # 新しい順にソート
                lines.append(file)
            if len(files) > MAX_DISPLAY_ITEMS:
                lines.append(f"... 他{len(files) - MAX_DISPLAY_ITEMS}件")
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # 警告
    if total_warnings > 0:
        lines.append(f"##### ⚠️ 警告 ({total_warnings}件)")
        lines.append("")
        for warning_type, files in sorted(anomalies["warnings"].items()):
            # 解説を追加
            description = KNOWN_DESCRIPTIONS.get(warning_type, "")
            if description:
                lines.append(f"> {description}")
                lines.append("")

            lines.append("<details>")
            lines.append(f"<summary><strong>{warning_type}</strong> ({len(files)}件)</summary>")
            lines.append("")
            lines.append("```text")
            for file in sorted(files, key=_extract_file_sort_key)[:MAX_DISPLAY_ITEMS]:  # 新しい順にソート
                lines.append(file)
            if len(files) > MAX_DISPLAY_ITEMS:
                lines.append(f"... 他{len(files) - MAX_DISPLAY_ITEMS}件")
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # === 処理済みデータ (processed) の品質チェック ===
    lines.append("")
    lines.append("#### 📊 処理済みデータ (processed) の品質チェック")
    lines.append("")

    # データ品質の問題
    if total_quality_issues > 0:
        lines.append(f"##### 🔍 データ品質の問題 ({total_quality_issues}件)")
        lines.append("")
        for check_type, info in sorted(anomalies["quality_issues"].items()):
            files = info["files"]
            details = info["details"]

            # 解説を追加
            description = KNOWN_DESCRIPTIONS.get(check_type, "")
            if description:
                lines.append(f"> {description}")
                lines.append("")

            # 合計 affected_count を計算 (防御的な辞書アクセス)
            total_affected = sum(d.get("affected_count", 0) for d in details)

            lines.append("<details>")
            if total_affected > 0:
                lines.append(
                    f"<summary><strong>{check_type}</strong> ({len(files)}ファイル, 不整合: {total_affected}件)</summary>"
                )
            else:
                lines.append(f"<summary><strong>{check_type}</strong> ({len(files)}ファイル)</summary>")
            lines.append("")

            # ファイルごとの詳細を表示 (新しい順にソート)
            lines.append("```text")
            for detail in sorted(details, key=lambda d: _extract_file_sort_key(d.get("filename", "")))[
                :MAX_DISPLAY_ITEMS
            ]:
                affected_count = detail.get("affected_count", 0)
                if affected_count > 0:
                    lines.append(f"{detail.get('filename', 'Unknown')} (不整合: {affected_count}件)")
                else:
                    lines.append(f"{detail.get('filename', 'Unknown')}")
            if len(details) > MAX_DISPLAY_ITEMS:
                lines.append(f"... 他{len(details) - MAX_DISPLAY_ITEMS}ファイル")
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    else:
        lines.append("##### ✅ データ品質の問題")
        lines.append("")
        lines.append("問題なし")

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
        # 週次データ: 各年52-53週 (ISO 8601動的判定)
        for year in range(min_period[0], max_period[0] + 1):
            max_week = 53 if _has_53_weeks(year) else 52
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
## 📊 データ収集状況 (自動更新)

> 💡 このセクションは `scripts/update_readme_stats.py` により自動生成されています。

### 📅 最新データ

| 項目 | 値 |
|------|-----|
| **最新週次データ** | {stats['latest_week']} |
| **最新月次データ** | {stats['latest_month']} |
| **最終統計更新日時** | {stats['last_stats_update']} |
| **最新データ取得日時** | {stats['latest_fetch']} |

> 📝 **日時の説明**
> - **最終統計更新日時**: この統計情報が最後に更新された日時 (毎日自動実行)
> - **最新データ取得日時**: 東京都IDSCから新しいデータを取得した日時

### 📊 感染動向の可視化

> 💡 季節性ベースライン (同週/同月の過去5年平均) からの乖離率で流行を検知

#### 週次定点 (Sentinel Surveillance - Weekly)

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/sentinel_weekly_absolute.png" alt="週次定点・絶対数" width="100%">
      <p align="center"><sub>定点週次・絶対数 (直近52週・1年間)</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/sentinel_weekly_deviation.png" alt="週次定点・季節性乖離率" width="100%">
      <p align="center"><sub>定点週次・季節性乖離率 (%)</sub></p>
    </td>
  </tr>
</table>

#### 週次全数 (Notifiable Diseases - Weekly)

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/notifiable_weekly_absolute.png" alt="週次全数・絶対数" width="100%">
      <p align="center"><sub>全数報告週次・絶対数 (直近52週・1年間)</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/notifiable_weekly_deviation.png" alt="週次全数・季節性乖離率" width="100%">
      <p align="center"><sub>全数報告週次・季節性乖離率 (%)</sub></p>
    </td>
  </tr>
</table>

#### 月次定点 (Sentinel Surveillance - Monthly)

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/sentinel_monthly_absolute.png" alt="月次定点・絶対数" width="100%">
      <p align="center"><sub>定点月次・絶対数 (直近12ヶ月)</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/sentinel_monthly_deviation.png" alt="月次定点・季節性乖離率" width="100%">
      <p align="center"><sub>定点月次・季節性乖離率 (%)</sub></p>
    </td>
  </tr>
</table>

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

### 🔍 データ品質チェック

{format_anomalies_section(stats['anomalies'])}

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
        print("[SUCCESS] README.md の統計情報を更新しました")
        return True
    print("[INFO] README.md に変更はありません")
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
    print(f"   最終統計更新: {stats['last_stats_update']}")
    print(f"   最新データ取得: {stats['latest_fetch']}")

    # README.mdを更新
    print("\nREADME.mdを更新しています...")
    updated = update_readme(stats)

    if updated:
        print("\n✅ 更新完了")
    else:
        print("\n✅ 処理完了 (変更なし)")


if __name__ == "__main__":
    main()
