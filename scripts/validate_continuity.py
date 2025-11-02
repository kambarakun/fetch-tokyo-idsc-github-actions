#!/usr/bin/env python3
"""
データの連続性を検証するスクリプト

週次・月次データの欠損を検出し、レポートを生成します。
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


@dataclass
class ContinuityReport:
    """連続性検証レポート"""

    data_type: str
    start_year: int
    end_year: int
    expected_count: int
    actual_count: int
    missing_periods: list[dict[str, Any]] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    is_valid: bool = True
    error_messages: list[str] = field(default_factory=list)


class ContinuityValidator:
    """データ連続性検証クラス"""

    # ファイル名パースの定数
    MIN_FILENAME_PARTS = 4  # 最小限必要なファイル名の部品数（notifiable_weekly_2025_01形式で4パーツ）

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.logger = logging.getLogger(__name__)
        # 日本時間を使用
        self.jst = ZoneInfo("Asia/Tokyo")

    def validate_all(self, start_year: int | None = None, end_year: int | None = None) -> dict[str, ContinuityReport]:
        """全データタイプの連続性を検証

        Args:
            start_year: 開始年（Noneの場合は最も古いデータから）
            end_year: 終了年（Noneの場合は現在年まで）

        Returns:
            データタイプごとの検証レポート
        """
        reports = {}

        # データタイプのリスト
        data_types = [
            "sentinel_weekly_gender",
            "sentinel_weekly_age",
            "sentinel_weekly_health_center",
            "sentinel_weekly_medical_district",
            "notifiable_weekly",
            "sentinel_monthly_gender",
            "sentinel_monthly_age",
            "sentinel_monthly_health_center",
            "sentinel_monthly_medical_district",
        ]

        for data_type in data_types:
            report = self.validate_data_type(data_type, start_year, end_year)
            reports[data_type] = report
            if not report.is_valid:
                self.logger.warning(f"{data_type}: {len(report.missing_periods)}件の欠損を検出")

        return reports

    def validate_data_type(
        self, data_type: str, start_year: int | None = None, end_year: int | None = None
    ) -> ContinuityReport:
        """特定のデータタイプの連続性を検証

        Args:
            data_type: データタイプ
            start_year: 開始年
            end_year: 終了年

        Returns:
            検証レポート
        """
        # ファイルのリストを取得（大文字小文字を区別しない）
        # 注：glob自体は大文字小文字を区別するため、後続処理で対応
        files = []
        for file_path in self.data_dir.glob("*.csv"):
            if file_path.name.lower().startswith(f"{data_type.lower()}_"):
                files.append(file_path)

        if not files:
            return ContinuityReport(
                data_type=data_type,
                start_year=start_year or 2000,
                end_year=end_year or datetime.now().year,
                expected_count=0,
                actual_count=0,
                is_valid=False,
                error_messages=["データファイルが見つかりません"],
            )

        # 月次か週次かを判定
        is_monthly = "monthly" in data_type

        # ファイルから年と期間を抽出
        existing_periods = set()
        years = set()
        for file_path in files:
            parts = file_path.stem.split("_")
            if len(parts) >= self.MIN_FILENAME_PARTS:
                try:
                    year = int(parts[-2])
                    period = int(parts[-1])
                    existing_periods.add((year, period))
                    years.add(year)
                except ValueError:
                    continue

        # 開始年と終了年を決定
        if start_year is None:
            start_year = min(years) if years else 2000
        if end_year is None:
            end_year = max(years) if years else datetime.now(self.jst).year

        # 期待される期間を生成
        expected_periods = self._generate_expected_periods(data_type, start_year, end_year, is_monthly)

        # 欠損期間を特定
        missing_periods = []
        for year, period in expected_periods:
            if (year, period) not in existing_periods:
                missing_periods.append(
                    {
                        "year": year,
                        "period": period,
                        "type": "monthly" if is_monthly else "weekly",
                        "filename": f"{data_type}_{year}_{period:02d}.csv",
                    }
                )

        # レポート作成
        report = ContinuityReport(
            data_type=data_type,
            start_year=start_year,
            end_year=end_year,
            expected_count=len(expected_periods),
            actual_count=len(existing_periods),
            missing_periods=missing_periods,
            is_valid=len(missing_periods) == 0,
        )

        if missing_periods:
            report.error_messages.append(f"{len(missing_periods)}件の欠損期間があります")

        return report

    def _generate_expected_periods(
        self, data_type: str, start_year: int, end_year: int, is_monthly: bool
    ) -> set[tuple[int, int]]:
        """期待される期間のセットを生成

        Args:
            data_type: データタイプ
            start_year: 開始年
            end_year: 終了年
            is_monthly: 月次データかどうか

        Returns:
            (年, 期間)のタプルのセット
        """
        expected = set()
        current_date = datetime.now(self.jst)

        for year in range(start_year, end_year + 1):
            if is_monthly:
                # 月次データの場合
                max_month = 12 if year < current_date.year else current_date.month
                for month in range(1, max_month + 1):
                    expected.add((year, month))
            else:
                # 週次データの場合
                max_week = self._get_weeks_in_year(year)
                if year == current_date.year:
                    # 現在年の場合は現在週まで
                    max_week = min(max_week, current_date.isocalendar()[1])

                for week in range(1, max_week + 1):
                    expected.add((year, week))

        return expected

    def _get_weeks_in_year(self, year: int) -> int:
        """指定年の週数を取得"""
        return date(year, 12, 28).isocalendar()[1]

    def generate_report(self, reports: dict[str, ContinuityReport], output_format: str = "json") -> str:
        """レポートを生成

        Args:
            reports: 検証レポートの辞書
            output_format: 出力形式（json, text, markdown）

        Returns:
            フォーマットされたレポート文字列
        """
        if output_format == "json":
            return self._generate_json_report(reports)
        if output_format == "text":
            return self._generate_text_report(reports)
        if output_format == "markdown":
            return self._generate_markdown_report(reports)
        raise ValueError(f"不正な出力形式: {output_format}")

    def _generate_json_report(self, reports: dict[str, ContinuityReport]) -> str:
        """JSON形式のレポートを生成"""
        report_dict = {}
        for data_type, report in reports.items():
            report_dict[data_type] = {
                "start_year": report.start_year,
                "end_year": report.end_year,
                "expected_count": report.expected_count,
                "actual_count": report.actual_count,
                "missing_count": len(report.missing_periods),
                "is_valid": report.is_valid,
                "missing_periods": report.missing_periods,
                "error_messages": report.error_messages,
            }
        return json.dumps(report_dict, indent=2, ensure_ascii=False)

    def _generate_text_report(self, reports: dict[str, ContinuityReport]) -> str:
        """テキスト形式のレポートを生成"""
        lines = ["=" * 80]
        lines.append("データ連続性検証レポート")
        lines.append("=" * 80)
        lines.append("")

        total_missing = 0
        invalid_types = []

        for data_type, report in reports.items():
            lines.append(f"## {data_type}")
            lines.append(f"  期間: {report.start_year}年 - {report.end_year}年")
            lines.append(f"  期待数: {report.expected_count}件")
            lines.append(f"  実際: {report.actual_count}件")

            if report.is_valid:
                lines.append("  状態: ✅ 正常（欠損なし）")
            else:
                lines.append(f"  状態: ❌ 欠損あり（{len(report.missing_periods)}件）")
                invalid_types.append(data_type)
                total_missing += len(report.missing_periods)

                # 最初の5件の欠損を表示
                if report.missing_periods:
                    lines.append("  欠損例:")
                    for missing in report.missing_periods[:5]:
                        lines.append(f"    - {missing['year']}年 {missing['period']}期")
                    if len(report.missing_periods) > 5:
                        lines.append(f"    ... 他{len(report.missing_periods) - 5}件")

            lines.append("")

        # サマリー
        lines.append("=" * 80)
        lines.append("サマリー")
        lines.append("=" * 80)
        if total_missing == 0:
            lines.append("✅ すべてのデータが揃っています")
        else:
            lines.append(f"⚠️ 合計 {total_missing} 件の欠損が見つかりました")
            lines.append(f"影響を受けるデータタイプ: {', '.join(invalid_types)}")

        return "\n".join(lines)

    def _generate_markdown_report(self, reports: dict[str, ContinuityReport]) -> str:
        """Markdown形式のレポートを生成"""
        lines = ["# データ連続性検証レポート"]
        lines.append("")
        lines.append(f"実行日時: {datetime.now(self.jst).strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # サマリーテーブル
        lines.append("## サマリー")
        lines.append("")
        lines.append("| データタイプ | 期間 | 期待数 | 実際 | 欠損 | 状態 |")
        lines.append("|------------|------|--------|------|------|------|")

        for data_type, report in reports.items():
            status = "✅" if report.is_valid else "❌"
            period = f"{report.start_year}-{report.end_year}"
            missing_count = len(report.missing_periods)
            lines.append(
                f"| {data_type} | {period} | {report.expected_count} | {report.actual_count} | {missing_count} | {status} |"
            )

        lines.append("")

        # 欠損詳細
        has_missing = any(not report.is_valid for report in reports.values())
        if has_missing:
            lines.append("## 欠損詳細")
            lines.append("")

            for data_type, report in reports.items():
                if not report.is_valid:
                    lines.append(f"### {data_type}")
                    lines.append("")
                    lines.append(f"- **欠損数**: {len(report.missing_periods)}件")
                    lines.append("- **欠損期間**:")

                    # 欠損を年ごとにグループ化
                    by_year: dict[int, list[int]] = {}
                    for missing in report.missing_periods:
                        year = missing["year"]
                        if year not in by_year:
                            by_year[year] = []
                        by_year[year].append(missing["period"])

                    for year in sorted(by_year.keys()):
                        periods = sorted(by_year[year])
                        if len(periods) <= 10:
                            period_str = ", ".join(str(p) for p in periods)
                        else:
                            period_str = f"{', '.join(str(p) for p in periods[:10])}, ... (計{len(periods)}件)"
                        lines.append(f"  - {year}年: {period_str}")

                    lines.append("")

        return "\n".join(lines)


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="データの連続性を検証")
    parser.add_argument("data_dir", type=str, help="データディレクトリのパス")
    parser.add_argument("--start-year", type=int, help="開始年")
    parser.add_argument("--end-year", type=int, help="終了年")
    parser.add_argument(
        "--data-type",
        type=str,
        help="検証するデータタイプ（指定しない場合は全て）",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["json", "text", "markdown"],
        default="text",
        help="出力形式",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="出力ファイルパス（指定しない場合は標準出力）",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="ログレベル",
    )

    args = parser.parse_args()

    # ロギング設定
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")

    # バリデータ初期化
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"エラー: データディレクトリが存在しません: {data_dir}", file=sys.stderr)
        sys.exit(1)

    validator = ContinuityValidator(data_dir)

    # 検証実行
    if args.data_type:
        # 特定のデータタイプのみ
        report = validator.validate_data_type(args.data_type, args.start_year, args.end_year)
        reports = {args.data_type: report}
    else:
        # 全データタイプ
        reports = validator.validate_all(args.start_year, args.end_year)

    # レポート生成
    output = validator.generate_report(reports, args.format)

    # 出力
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output)
        print(f"レポートを保存しました: {output_path}")
    else:
        print(output)

    # 終了コード（欠損がある場合は1）
    has_missing = any(not report.is_valid for report in reports.values())
    sys.exit(1 if has_missing else 0)


if __name__ == "__main__":
    main()
