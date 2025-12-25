#!/usr/bin/env python3
"""
fix_fullwidth_symbols.py のテストスイート

CLAUDE.md セクション3.4のガイドラインに従い、
カバレッジ80%以上を目指した包括的なテストを実装。
"""

import sys
from pathlib import Path
from unittest.mock import patch

# テスト対象モジュールをインポート
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from fix_fullwidth_symbols import FULLWIDTH_TO_HALFWIDTH, fix_fullwidth_symbols, main


class TestFixFullwidthSymbols:
    """fix_fullwidth_symbols関数のテスト"""

    def test_fix_fullwidth_parentheses(self, tmp_path):
        """全角括弧を半角に変換"""
        test_file = tmp_path / "test.py"
        test_file.write_text("# テスト関数\uff08全角括弧\uff09\n", encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is True

        content = test_file.read_text(encoding="utf-8")
        assert "(" in content
        assert ")" in content
        assert "\uff08" not in content
        assert "\uff09" not in content

    def test_fix_fullwidth_colon(self, tmp_path):
        """全角コロンを半角に変換"""
        test_file = tmp_path / "test.py"
        test_file.write_text("説明\uff1aこれは全角コロンです\n", encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is True

        content = test_file.read_text(encoding="utf-8")
        assert ":" in content
        assert "\uff1a" not in content

    def test_fix_fullwidth_tilde(self, tmp_path):
        """全角チルダを半角に変換"""
        test_file = tmp_path / "test.py"
        test_file.write_text("範囲\uff5e100まで\n", encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is True

        content = test_file.read_text(encoding="utf-8")
        assert "~" in content
        assert "\uff5e" not in content

    def test_fix_multiple_symbols(self, tmp_path):
        """複数の全角記号を一度に変換"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def test\uff08\uff09\uff1a  # テスト関数\uff08全角\uff09\uff5e範囲\n", encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is True

        content = test_file.read_text(encoding="utf-8")
        assert ":" in content
        assert "(" in content
        assert ")" in content
        assert "~" in content
        assert "\uff1a" not in content
        assert "\uff08" not in content
        assert "\uff09" not in content
        assert "\uff5e" not in content

    def test_no_fullwidth_symbols(self, tmp_path):
        """全角記号がない場合は変更なし"""
        test_file = tmp_path / "test.py"
        original_content = "def test():  # Normal comment (halfwidth)\n"
        test_file.write_text(original_content, encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is False

        content = test_file.read_text(encoding="utf-8")
        assert content == original_content

    def test_preserve_utf8_encoding(self, tmp_path):
        """UTF-8エンコーディングを維持"""
        test_file = tmp_path / "test.py"
        test_file.write_text("# 日本語コメント\uff08テスト\uff09🎉\n", encoding="utf-8")

        fix_fullwidth_symbols(test_file)

        content = test_file.read_text(encoding="utf-8")
        assert "日本語" in content
        assert "🎉" in content  # 絵文字も保持
        assert "(テスト)" in content  # 全角→半角変換

    def test_idempotency(self, tmp_path):
        """複数回実行しても結果が変わらない"""
        test_file = tmp_path / "test.py"
        test_file.write_text("# テスト\uff08全角\uff09\n", encoding="utf-8")

        # 1回目の実行
        result1 = fix_fullwidth_symbols(test_file)
        assert result1 is True
        content1 = test_file.read_text(encoding="utf-8")

        # 2回目の実行
        result2 = fix_fullwidth_symbols(test_file)
        assert result2 is False  # 変更なし
        content2 = test_file.read_text(encoding="utf-8")

        assert content1 == content2

    def test_handle_encoding_error(self, tmp_path, capsys):
        """エンコーディングエラーを適切に処理"""
        test_file = tmp_path / "test.py"
        # Shift_JISで保存(UTF-8として読むとエラー)
        test_file.write_bytes("テスト(全角)".encode("shift_jis"))

        result = fix_fullwidth_symbols(test_file)
        assert result is False

        captured = capsys.readouterr()
        assert "警告" in captured.out or "読み込めませんでした" in captured.out

    def test_markdown_file(self, tmp_path):
        """Markdownファイルも処理"""
        test_file = tmp_path / "README.md"
        test_file.write_text("# タイトル\n\n説明\uff08全角括弧\uff09\n", encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is True

        content = test_file.read_text(encoding="utf-8")
        assert "(全角括弧)" in content

    def test_preserve_multiline_structure(self, tmp_path):
        """複数行の構造を保持"""
        test_file = tmp_path / "test.py"
        original_lines = [
            "def test\uff08\uff09\uff1a\n",
            "    # コメント\uff08全角\uff09\n",
            "    pass\n",
        ]
        test_file.write_text("".join(original_lines), encoding="utf-8")

        fix_fullwidth_symbols(test_file)

        content = test_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        assert len(lines) == 4  # 最後の空行を含む
        assert "def test():" in lines[0]
        assert "(全角)" in lines[1]


class TestMain:
    """main関数のテスト"""

    def test_main_no_arguments(self, capsys):
        """引数なしの場合はエラー"""
        with patch("sys.argv", ["fix_fullwidth_symbols.py"]):
            result = main()
            assert result == 1

        captured = capsys.readouterr()
        assert "使用方法" in captured.out

    def test_main_with_valid_file(self, tmp_path, capsys):
        """修正が必要なファイルで成功"""
        test_file = tmp_path / "test.py"
        test_file.write_text("# テスト\uff08全角\uff09\n", encoding="utf-8")

        with patch("sys.argv", ["fix_fullwidth_symbols.py", str(test_file)]):
            result = main()
            assert result == 1  # 修正があった場合は1を返す

        captured = capsys.readouterr()
        assert "修正" in captured.out
        assert "1個のファイルを修正しました" in captured.out

    def test_main_with_no_changes_needed(self, tmp_path):
        """修正不要なファイルで成功"""
        test_file = tmp_path / "test.py"
        test_file.write_text("# Test (halfwidth)\n", encoding="utf-8")

        with patch("sys.argv", ["fix_fullwidth_symbols.py", str(test_file)]):
            result = main()
            assert result == 0  # 修正なしの場合は0を返す

    def test_main_with_nonexistent_file(self, tmp_path, capsys):
        """存在しないファイルでエラー"""
        test_file = tmp_path / "nonexistent.py"

        with patch("sys.argv", ["fix_fullwidth_symbols.py", str(test_file)]):
            result = main()
            assert result == 1  # エラーがあった場合は1を返す

        captured = capsys.readouterr()
        assert "見つかりません" in captured.out

    def test_main_with_multiple_files(self, tmp_path, capsys):
        """複数ファイルを処理"""
        file1 = tmp_path / "test1.py"
        file2 = tmp_path / "test2.md"

        file1.write_text("# テスト\uff08全角\uff09\n", encoding="utf-8")
        file2.write_text("# Markdown\uff08全角\uff09\n", encoding="utf-8")

        with patch("sys.argv", ["fix_fullwidth_symbols.py", str(file1), str(file2)]):
            result = main()
            assert result == 1  # 修正があった

        captured = capsys.readouterr()
        assert "2個のファイルを修正しました" in captured.out

    def test_main_skips_non_target_files(self, tmp_path):
        """Python・Markdown以外のファイルはスキップ"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("テスト\uff08全角\uff09\n", encoding="utf-8")

        with patch("sys.argv", ["fix_fullwidth_symbols.py", str(test_file)]):
            result = main()
            assert result == 0  # スキップされるので修正なし

        # ファイルは変更されていない
        content = test_file.read_text(encoding="utf-8")
        assert "\uff08" in content  # 全角のまま


class TestFullwidthToHalfwidthMap:
    """FULLWIDTH_TO_HALFWIDTH定数のテスト"""

    def test_map_contains_expected_symbols(self):
        """必要な変換マップが定義されている"""
        assert "\uff08" in FULLWIDTH_TO_HALFWIDTH  # 全角左括弧
        assert "\uff09" in FULLWIDTH_TO_HALFWIDTH  # 全角右括弧
        assert "\uff1a" in FULLWIDTH_TO_HALFWIDTH  # 全角コロン
        assert "\uff5e" in FULLWIDTH_TO_HALFWIDTH  # 全角チルダ

    def test_map_values_are_halfwidth(self):
        """変換先が全て半角"""
        for fullwidth, halfwidth in FULLWIDTH_TO_HALFWIDTH.items():
            assert len(halfwidth) == 1
            assert ord(halfwidth) < 128  # ASCII範囲内
            # fullwidthは全角文字(Unicodeコードポイントが0xFF00以上)
            assert ord(fullwidth) >= 0xFF00


class TestEdgeCases:
    """エッジケースのテスト"""

    def test_empty_file(self, tmp_path):
        """空ファイルを処理"""
        test_file = tmp_path / "empty.py"
        test_file.write_text("", encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is False

        content = test_file.read_text(encoding="utf-8")
        assert content == ""

    def test_very_large_file(self, tmp_path):
        """大きなファイルを処理"""
        test_file = tmp_path / "large.py"
        # 1000行のファイル
        lines = ["# テスト\uff08全角\uff09\n"] * 1000
        test_file.write_text("".join(lines), encoding="utf-8")

        result = fix_fullwidth_symbols(test_file)
        assert result is True

        content = test_file.read_text(encoding="utf-8")
        assert content.count("(全角)") == 1000

    def test_mixed_fullwidth_halfwidth(self, tmp_path):
        """全角と半角が混在"""
        test_file = tmp_path / "mixed.py"
        test_file.write_text("# Test (half) and\uff08full\uff09\n", encoding="utf-8")

        fix_fullwidth_symbols(test_file)

        content = test_file.read_text(encoding="utf-8")
        assert "(half)" in content
        assert "(full)" in content
        assert "\uff08" not in content

    def test_consecutive_fullwidth_symbols(self, tmp_path):
        """連続する全角記号"""
        test_file = tmp_path / "test.py"
        test_file.write_text("\uff08\uff08テスト\uff09\uff09\n", encoding="utf-8")

        fix_fullwidth_symbols(test_file)

        content = test_file.read_text(encoding="utf-8")
        assert content == "((テスト))\n"

    def test_fullwidth_in_string_literals(self, tmp_path):
        """文字列リテラル内の全角記号も変換"""
        test_file = tmp_path / "test.py"
        test_file.write_text('message = "エラー\uff08全角\uff09"\n', encoding="utf-8")

        fix_fullwidth_symbols(test_file)

        content = test_file.read_text(encoding="utf-8")
        assert "(全角)" in content

    def test_fullwidth_in_docstring(self, tmp_path):
        """docstring内の全角記号も変換"""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            '''def test\uff08\uff09\uff1a
    """テスト関数\uff08全角\uff09"""
    pass
''',
            encoding="utf-8",
        )

        fix_fullwidth_symbols(test_file)

        content = test_file.read_text(encoding="utf-8")
        assert "(全角)" in content
        assert "\uff08" not in content
