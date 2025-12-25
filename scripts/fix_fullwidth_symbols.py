#!/usr/bin/env python3
"""
全角記号を半角に自動修正するスクリプト

PythonファイルとMarkdownファイル内の全角括弧、コロン、チルダを半角に変換します。
これにより、Ruff RUF002/RUF003のエラーを自動的に修正します。
"""

import sys
from pathlib import Path

# 全角→半角の変換マップ
FULLWIDTH_TO_HALFWIDTH = {
    "(": "(",
    ")": ")",
    ":": ":",
    "~": "~",
}


def fix_fullwidth_symbols(filepath: Path) -> bool:
    """ファイル内の全角記号を半角に変換

    Args:
        filepath: 修正対象のファイルパス

    Returns:
        修正があった場合はTrue、変更なしの場合はFalse
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"警告: {filepath} を読み込めませんでした (エンコーディングエラー)")
        return False

    modified = False

    # 全角記号を半角に変換
    for fullwidth, halfwidth in FULLWIDTH_TO_HALFWIDTH.items():
        if fullwidth in content:
            content = content.replace(fullwidth, halfwidth)
            modified = True

    if modified:
        filepath.write_text(content, encoding="utf-8")
        print(f"✅ 修正: {filepath}")
        return True

    return False


def main() -> int:
    """メイン処理

    Returns:
        終了コード (0: 成功、1: エラー検出)
    """
    if len(sys.argv) < 2:
        print("使用方法: fix_fullwidth_symbols.py <file1> [file2] ...")
        return 1

    files = [Path(arg) for arg in sys.argv[1:]]
    modified_count = 0

    for filepath in files:
        if not filepath.exists():
            print(f"エラー: {filepath} が見つかりません")
            continue

        # Python・Markdownファイルのみ処理
        if filepath.suffix.lower() not in [".py", ".md", ".markdown"]:
            continue

        if fix_fullwidth_symbols(filepath):
            modified_count += 1

    if modified_count > 0:
        print(f"\n{modified_count}個のファイルを修正しました")
        return 1  # 修正があった場合は1を返す (pre-commitが再チェックするため)

    return 0


if __name__ == "__main__":
    sys.exit(main())
