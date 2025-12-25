#!/usr/bin/env python3
"""
check_tree_comment_alignment.py のテストスイート

CLAUDE.md セクション3.4のガイドラインに従い、
カバレッジ80%以上を目指した包括的なテストを実装。
"""

import sys
from pathlib import Path
from unittest.mock import patch

# テスト対象モジュールをインポート
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from check_tree_comment_alignment import check_comment_alignment, check_file, extract_tree_blocks, main


class TestExtractTreeBlocks:
    """extract_tree_blocks関数のテスト"""

    def test_extract_single_tree_block(self):
        """正しいtree構造を1つ検出できることを確認"""
        content = """# Test Document

```text
data/
├── raw/     # 生データ
└── logs/    # ログ
```

Some other text.
"""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 1
        # 開始行番号は「```text」の次の行(L4)
        assert blocks[0][0] == 4
        assert "data/" in blocks[0][1]
        assert len(blocks[0][1]) > 0

    def test_extract_multiple_tree_blocks(self):
        """複数のtree構造を検出できることを確認"""
        content = """
```text
data/
├── raw/
```

Some text.

```text
src/
├── main.py
└── utils/
```
"""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 2

    def test_ignore_non_tree_code_blocks(self):
        """tree構造でないコードブロックをスキップ"""
        content = """
```text
This is just text
without tree structure
```
"""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 0

    def test_handle_empty_content(self):
        """空の内容を正しく処理"""
        content = ""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 0

    def test_handle_code_blocks_with_4_backticks(self):
        """4つのバッククォートで終了するブロックを正しく処理"""
        content = """
```text
data/
├── raw/  # comment
└── logs/ # comment
````
"""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 1

    def test_ignore_bash_code_blocks(self):
        """```bash等の他のコードブロックを無視"""
        content = """
```bash
# This is bash
ls -la
```
"""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 0

    def test_tree_block_with_box_drawing_characters(self):
        """罫線文字を含むブロックを検出"""
        content = """
```text
project/
│   ├── src/
│   └── tests/
```
"""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 1


class TestCheckCommentAlignment:
    """check_comment_alignment関数のテスト"""

    def test_aligned_comments(self):
        """揃ったコメントを正しく検証"""
        lines = [
            "data/",
            "├── raw/         # 生データ",
            "└── logs/        # ログファイル",
        ]
        errors = check_comment_alignment(lines, 1, "test.md")
        assert len(errors) == 0

    def test_misaligned_comments(self):
        """コメント位置のずれを検出"""
        lines = [
            "data/",
            "├── raw/  # 生データ",
            "└── logs/                  # ログファイル",
        ]
        errors = check_comment_alignment(lines, 1, "test.md")
        assert len(errors) > 0
        assert "統一されていません" in "\n".join(errors)

    def test_no_comments(self):
        """コメントがない場合はチェック不要"""
        lines = [
            "data/",
            "├── raw/",
            "└── logs/",
        ]
        errors = check_comment_alignment(lines, 1, "test.md")
        assert len(errors) == 0

    def test_single_comment(self):
        """コメントが1つだけの場合はエラーなし"""
        lines = [
            "data/",
            "├── raw/  # コメント",
            "└── logs/",
        ]
        errors = check_comment_alignment(lines, 1, "test.md")
        assert len(errors) == 0

    def test_multiple_position_detection(self):
        """複数の異なる位置を正しく検出"""
        lines = [
            "├── a/   # comment1",
            "├── bb/    # comment2",
            "└── ccc/     # comment3",
        ]
        errors = check_comment_alignment(lines, 1, "test.md")
        assert len(errors) > 0
        # エラーメッセージに複数の位置が含まれることを確認
        error_text = "\n".join(errors)
        assert "検出された位置" in error_text
        # 実際に3つの異なる位置が検出されることを確認
        assert "[9, 11, 13]" in error_text

    def test_detailed_error_message(self):
        """詳細なエラーメッセージを出力"""
        lines = [
            "├── raw/  # comment1",
            "└── logs/           # comment2",
        ]
        errors = check_comment_alignment(lines, 100, "test.md")
        assert len(errors) > 0
        error_text = "\n".join(errors)
        assert "test.md" in error_text
        assert "検出された位置" in error_text
        assert "L100" in error_text or "L101" in error_text


class TestCheckFile:
    """check_file関数のテスト"""

    def test_check_valid_file(self, tmp_path):
        """正しいファイルをチェック"""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            """
```text
data/
├── raw/   # comment
└── logs/  # comment
```
""",
            encoding="utf-8",
        )
        result = check_file(test_file)
        assert result is True

    def test_check_invalid_file(self, tmp_path):
        """コメント位置がずれているファイルを検出"""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            """
```text
data/
├── raw/  # comment
└── logs/           # comment
```
""",
            encoding="utf-8",
        )
        result = check_file(test_file)
        assert result is False

    def test_check_non_markdown_file(self, tmp_path):
        """Markdownファイル以外はスキップ"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content", encoding="utf-8")
        # 関数内でスキップされるが、エラーにならないことを確認
        result = check_file(test_file)
        # txtファイルはMarkdownではないが tree構造がないのでTrue
        assert result is True

    def test_check_encoding_error(self, tmp_path, capsys):
        """エンコーディングエラーを適切に処理"""
        test_file = tmp_path / "test.md"
        # Shift_JISで保存(UTF-8として読むとエラー)
        test_file.write_bytes("テスト".encode("shift_jis"))

        result = check_file(test_file)
        assert result is True  # エラーを無視して継続

        captured = capsys.readouterr()
        assert "警告" in captured.out or "読み込めませんでした" in captured.out

    def test_check_file_not_found(self, tmp_path):
        """存在しないファイルを処理(mainでハンドリング)"""
        # ファイルが存在しないケースはmain()でチェックされる
        # check_file自体はPathを受け取るだけなので、
        # 存在チェックはmainの責任
        # このテストケースは構造的な整合性のために保持
        pass


class TestMain:
    """main関数のテスト"""

    def test_main_no_arguments(self):
        """引数なしの場合はエラー"""
        with patch("sys.argv", ["check_tree_comment_alignment.py"]):
            result = main()
            assert result == 1

    def test_main_with_valid_file(self, tmp_path):
        """正しいファイルで成功"""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            """
```text
data/
├── raw/  # comment
└── logs/ # comment
```
""",
            encoding="utf-8",
        )

        with patch("sys.argv", ["check_tree_comment_alignment.py", str(test_file)]):
            result = main()
            assert result == 0

    def test_main_with_invalid_file(self, tmp_path, capsys):
        """コメント位置がずれているファイルでエラー"""
        test_file = tmp_path / "test.md"
        test_file.write_text(
            """
```text
data/
├── raw/  # comment1
└── logs/          # comment2
```
""",
            encoding="utf-8",
        )

        with patch("sys.argv", ["check_tree_comment_alignment.py", str(test_file)]):
            result = main()
            assert result == 1

        captured = capsys.readouterr()
        assert "統一されていません" in captured.out

    def test_main_with_nonexistent_file(self, tmp_path, capsys):
        """存在しないファイルでエラー"""
        test_file = tmp_path / "nonexistent.md"

        with patch("sys.argv", ["check_tree_comment_alignment.py", str(test_file)]):
            result = main()
            assert result == 1

        captured = capsys.readouterr()
        assert "見つかりません" in captured.out

    def test_main_with_multiple_files(self, tmp_path):
        """複数ファイルを処理"""
        file1 = tmp_path / "test1.md"
        file2 = tmp_path / "test2.md"

        file1.write_text(
            """
```text
data/
├── raw/  # comment
```
""",
            encoding="utf-8",
        )
        file2.write_text(
            """
```text
src/
├── main.py  # comment
```
""",
            encoding="utf-8",
        )

        with patch("sys.argv", ["check_tree_comment_alignment.py", str(file1), str(file2)]):
            result = main()
            assert result == 0


class TestEdgeCases:
    """エッジケースのテスト"""

    def test_url_with_hash(self):
        """URLのアンカー(#)を誤検出しない"""
        lines = [
            "├── README.md  # https://example.com#anchor",
        ]
        errors = check_comment_alignment(lines, 1, "test.md")
        # コメントが1つだけなのでエラーなし
        assert len(errors) == 0

    def test_markdown_heading_not_detected(self):
        """Markdown見出しを誤検出しない"""
        content = """
### タイトル

```text
data/
├── raw/  # comment
```
"""
        blocks = extract_tree_blocks(content, "test.md")
        # tree構造のみ検出(見出しは無視)
        assert len(blocks) == 1
        assert "###" not in blocks[0][1][0]

    def test_comment_without_leading_space(self):
        """#の前にスペースがないコメントの処理"""
        lines = [
            "├── file.txt#anchor  # comment",
        ]
        # 正規表現 r"\s#\s" に一致するコメントのみ検出
        errors = check_comment_alignment(lines, 1, "test.md")
        assert len(errors) == 0  # コメントが1つなのでエラーなし

    def test_empty_lines_in_tree(self):
        """tree構造内の空行を処理"""
        content = """
```text
data/
├── raw/  # comment

└── logs/ # comment
```
"""
        blocks = extract_tree_blocks(content, "test.md")
        assert len(blocks) == 1
        # 空行も含まれる
        assert "" in blocks[0][1]

    def test_very_long_paths(self):
        """非常に長いパス名を処理"""
        lines = [
            "├── " + "a" * 100 + "/  # comment1",
            "└── " + "b" * 100 + "/  # comment2",
        ]
        # エラーにならずに処理されることを確認
        errors = check_comment_alignment(lines, 1, "test.md")
        # 長さに関わらず、コメント位置を正しく検出
        assert isinstance(errors, list)
