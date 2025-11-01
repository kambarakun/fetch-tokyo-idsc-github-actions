#!/usr/bin/env python3
"""
週次/月次CSVの欠番チェックスクリプト
"""

import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


def weeks_in_year(year: int) -> int:
    """指定年の週数を取得(ISO週番号基準)"""
    return date(year, 12, 28).isocalendar()[1]


# ファイル名パターン
PAT_WEEK = re.compile(r"(?P<base>.+?_weekly(?:_[^_]+)??)_(?P<year>\d{4})_(?P<idx>\d{1,2})(?:_|-)")
PAT_MONTH = re.compile(r"(?P<base>.+?_monthly(?:_[^_]+)??)_(?P<year>\d{4})_(?P<idx>\d{1,2})(?:_|-)")


def collect(data_dir: Path):
    """ファイルを収集して解析"""
    weekly, monthly = defaultdict(set), defaultdict(set)

    for p in data_dir.rglob("*.csv"):
        m = PAT_WEEK.search(p.name)
        if m:
            d = m.groupdict()
            weekly[(d["base"], int(d["year"]))].add(int(d["idx"]))
            continue

        m = PAT_MONTH.search(p.name)
        if m:
            d = m.groupdict()
            monthly[(d["base"], int(d["year"]))].add(int(d["idx"]))

    return weekly, monthly


def analyse(found, current_limit, max_func):
    """欠番を分析"""
    today = datetime.now()
    missing = defaultdict(lambda: defaultdict(list))

    for (base, year), idxs in found.items():
        limit = current_limit if year == today.year else max_func(year)
        expect = set(range(1, limit + 1))
        lost = sorted(expect - idxs)
        if lost:
            missing[base][year] = lost
    return missing


def report(title, info):
    """レポート出力"""
    print(f"\n=== {title} ===")
    if not info:
        print("✓ 欠番なし")
        return

    total_missing = 0
    for base in sorted(info):
        print(f"[{base}]")
        for y, lost in sorted(info[base].items()):
            print(f"  ✗ {y}: {', '.join(map(str, lost))}")
            total_missing += len(lost)

    print(f"\n合計欠番数: {total_missing}")


def main():
    """メイン処理"""
    if len(sys.argv) != 2:
        data_dir = Path("data/raw")  # デフォルトディレクトリ
    else:
        data_dir = Path(sys.argv[1])

    data_dir = data_dir.expanduser().resolve()

    if not data_dir.is_dir():
        print(f"ディレクトリが見つかりません: {data_dir}")
        print("使用方法: python check_missing.py [data_directory]")
        sys.exit(1)

    print(f"データディレクトリ: {data_dir}")

    w_map, m_map = collect(data_dir)
    today = datetime.now()

    # 現在の週と月
    current_week = today.isocalendar()[1]
    current_month = today.month

    print(f"現在: {today.year}年 第{current_week}週 / {current_month}月")

    # 欠番分析
    miss_w = analyse(w_map, current_week, weeks_in_year)
    miss_m = analyse(m_map, current_month, lambda _: 12)

    # レポート出力
    report("週次ファイルの欠番", miss_w)
    report("月次ファイルの欠番", miss_m)

    # 統計情報
    total_weekly = sum(len(idxs) for idxs in w_map.values())
    total_monthly = sum(len(idxs) for idxs in m_map.values())

    print("\n=== 統計情報 ===")
    print(f"週次ファイル: {total_weekly}件")
    print(f"月次ファイル: {total_monthly}件")
    print(f"合計: {total_weekly + total_monthly}件")


if __name__ == "__main__":
    main()
