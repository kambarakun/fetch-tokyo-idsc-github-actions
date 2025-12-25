#!/usr/bin/env python3
"""
tree形式のコメント位置統一をチェックするスクリプト

Markdownファイル内のtree構造 (```text ブロック) において、
インラインコメント (#) の位置が統一されているかを検証します。
"""

import re
import sys
from pathlib import Path


def extract_tree_blocks(content: str, filename: str) -> list[tuple[int, list[str]]]:
    """Markdownファイルからtree構造のコードブロックを抽出

    Args:
        content: ファイルの内容
        filename: ファイル名 (エラーメッセージ用)

    Returns:
        (開始行番号, tree構造の行リスト) のタプルのリスト
    """
    blocks = []
    lines = content.split("\n")
    in_tree_block = False
    current_block: list[str] = []
    block_start_line = 0

    for i, line in enumerate(lines, start=1):
        # ```text で始まるコードブロックを検出
        if line.strip() == "```text":
            in_tree_block = True
            block_start_line = i + 1  # コードブロック内の最初の行番号
            current_block = []
        elif in_tree_block and line.strip().startswith("```"):
            # コードブロック終了 (3つ以上のバッククォート)
            in_tree_block = False
            # tree構造かどうかを判定 (├──, └──, │ などが含まれるか)
            if any(any(char in line for char in ["├", "└", "│"]) for line in current_block):
                blocks.append((block_start_line, current_block))
            current_block = []
        elif in_tree_block:
            current_block.append(line)

    return blocks


def check_comment_alignment(tree_lines: list[str], start_line: int, filename: str) -> list[str]:
    """tree構造のコメント位置統一をチェック

    Args:
        tree_lines: tree構造の行リスト
        start_line: 開始行番号 (エラーメッセージ用)
        filename: ファイル名 (エラーメッセージ用)

    Returns:
        エラーメッセージのリスト (問題がなければ空リスト)
    """
    errors = []
    comment_positions = []

    for i, line in enumerate(tree_lines, start=start_line):
        # コメント記号 # の位置を検出
        # tree構造内のコメントのみを対象(2つ以上のスペース + # + スペース)
        # これにより、URLのアンカー (#anchor) や見出し (###) を誤検出しない
        match = re.search(r"\s{2,}#\s", line)
        if match:
            # # の位置 (0始まりのインデックス)
            # match.start()はスペース列の開始位置なので、
            # 最後のスペースの次(#の位置)を計算
            pos = match.end() - 2  # "  # " の # の位置
            comment_positions.append((i, pos, line))

    if not comment_positions:
        # コメントがない場合はチェック不要
        return []

    # 全てのコメント位置を取得
    positions = [pos for _, pos, _ in comment_positions]
    unique_positions = set(positions)

    if len(unique_positions) > 1:
        # 位置が統一されていない
        errors.append(f"\n{filename}: コメント位置が統一されていません")
        errors.append(f"  検出された位置: {sorted(unique_positions)} カラム")
        errors.append("  詳細:")

        # 各位置ごとにグループ化
        for pos in sorted(unique_positions):
            matching_lines = [(line_no, line) for line_no, p, line in comment_positions if p == pos]
            errors.append(f"\n  {pos}カラム目 ({len(matching_lines)}行):")
            for line_no, line in matching_lines[:3]:  # 最初の3行のみ表示
                errors.append(f"    L{line_no}: {line.rstrip()}")
            if len(matching_lines) > 3:
                errors.append(f"    ... 他{len(matching_lines) - 3}行")

    return errors


def check_file(filepath: Path) -> bool:
    """ファイルをチェック

    Args:
        filepath: チェック対象のファイルパス

    Returns:
        問題がなければTrue、問題があればFalse
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"警告: {filepath} を読み込めませんでした(エンコーディングエラー)")
        return True

    tree_blocks = extract_tree_blocks(content, str(filepath))

    if not tree_blocks:
        # tree構造が見つからない場合はOK
        return True

    all_errors = []
    for start_line, tree_lines in tree_blocks:
        errors = check_comment_alignment(tree_lines, start_line, str(filepath))
        all_errors.extend(errors)

    if all_errors:
        print("\n".join(all_errors))
        print("\n修正方法:")
        print("  1. 最も長いパス名を見つける")
        print("  2. その末尾から2スペース空けた位置に全ての # を揃える")
        print("  3. 短いパス名はスペースで位置調整")
        print("\n参考: CLAUDE.md セクション 5.6")
        return False

    return True


def main() -> int:
    """メイン処理

    Returns:
        終了コード (0: 成功、1: エラー検出)
    """
    if len(sys.argv) < 2:
        print("使用方法: check_tree_comment_alignment.py <file1> [file2] ...")
        return 1

    files = [Path(arg) for arg in sys.argv[1:]]
    all_ok = True

    for filepath in files:
        if not filepath.exists():
            print(f"エラー: {filepath} が見つかりません")
            all_ok = False
            continue

        if filepath.suffix.lower() not in [".md", ".markdown"]:
            # Markdownファイル以外はスキップ
            continue

        if not check_file(filepath):
            all_ok = False

    if not all_ok:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
