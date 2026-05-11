#!/usr/bin/env python3
"""
check_deprecated_cli_usage.py のテストスイート

issue #312 廃止shimsの旧導線検出ガードのテスト。
過去に `\\b` のエスケープミスで全パターンが機能不全だった経緯があるため、
パターン検出の網羅性とdedup挙動を回帰テストとして固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

# テスト対象モジュールをインポート
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from check_deprecated_cli_usage import DEPRECATED_SCRIPT_NAMES, PATTERNS, Violation, check_file, main, run_check


class TestPatterns:
    """正規表現パターンが想定通り word boundary で検出できることを確認"""

    def test_patterns_match_python_direct_invocation(self):
        """python scripts/X.py をマッチ"""
        for name in DEPRECATED_SCRIPT_NAMES:
            text = f"python scripts/{name}.py --foo"
            hit = any(p.search(text) for p in PATTERNS)
            assert hit, f"{name}: 'python scripts/{name}.py' 検出失敗"

    def test_patterns_match_python3_direct_invocation(self):
        """python3 scripts/X.py もマッチ"""
        text = "python3 scripts/fetch_data.py"
        assert any(p.search(text) for p in PATTERNS)

    def test_patterns_match_uv_run_python_invocation(self):
        """uv run python scripts/X.py をマッチ"""
        for name in DEPRECATED_SCRIPT_NAMES:
            text = f"uv run python scripts/{name}.py --opt"
            hit = any(p.search(text) for p in PATTERNS)
            assert hit, f"{name}: 'uv run python scripts/{name}.py' 検出失敗"

    def test_patterns_match_python_m_module_invocation(self):
        """python -m scripts.X をマッチ (新規追加パターン)"""
        for name in DEPRECATED_SCRIPT_NAMES:
            text = f"python -m scripts.{name} --arg"
            hit = any(p.search(text) for p in PATTERNS)
            assert hit, f"{name}: 'python -m scripts.{name}' 検出失敗"

    def test_patterns_match_from_import(self):
        """from scripts.X import をマッチ"""
        for name in DEPRECATED_SCRIPT_NAMES:
            text = f"from scripts.{name} import something"
            hit = any(p.search(text) for p in PATTERNS)
            assert hit, f"{name}: 'from scripts.{name} import' 検出失敗"

    def test_patterns_match_import(self):
        """import scripts.X をマッチ"""
        for name in DEPRECATED_SCRIPT_NAMES:
            text = f"import scripts.{name}"
            hit = any(p.search(text) for p in PATTERNS)
            assert hit, f"{name}: 'import scripts.{name}' 検出失敗"

    def test_patterns_do_not_match_unrelated_script(self):
        """対象外のスクリプト名 (例: scripts/generate_charts.py) は無視"""
        text = "uv run python scripts/generate_charts.py"
        assert not any(p.search(text) for p in PATTERNS)

    def test_patterns_do_not_match_substring_collision(self):
        """対象名がsubstring一致する別名 (例: fetch_data_v2) には反応しない (word boundary)"""
        text = "import scripts.fetch_data_v2"
        # `\b` で fetch_data の直後に _v2 が続くと境界がないため非マッチ
        assert not any(p.search(text) for p in PATTERNS)


class TestCheckFile:
    """check_file関数のテスト"""

    def test_detect_single_violation(self, tmp_path: Path) -> None:
        """1行違反を検出"""
        f = tmp_path / "doc.md"
        f.write_text("実行: uv run python scripts/fetch_data.py --dry-run\n", encoding="utf-8")
        violations = check_file(f)
        assert len(violations) == 1
        assert violations[0].line_no == 1

    def test_no_violation_for_modern_command(self, tmp_path: Path) -> None:
        """新CLI経路 `uv run fetch-data` は検出されない"""
        f = tmp_path / "doc.md"
        f.write_text("実行: uv run fetch-data --dry-run\n", encoding="utf-8")
        violations = check_file(f)
        assert violations == []

    def test_dedup_same_line_multiple_pattern_match(self, tmp_path: Path) -> None:
        """同一行で複数パターン一致しても1件のみ報告"""
        f = tmp_path / "doc.md"
        # `python scripts/X.py` と `uv run python scripts/X.py` の両パターンが一致する行
        f.write_text("uv run python scripts/process_data.py\n", encoding="utf-8")
        violations = check_file(f)
        assert len(violations) == 1, f"重複報告された: {violations}"

    def test_inline_allow_directive_skips_line(self, tmp_path: Path) -> None:
        """行末に deprecated-usage: allow が付いた行はスキップ"""
        f = tmp_path / "doc.md"
        f.write_text(
            "echo 'sample: uv run python scripts/fetch_data.py'  # deprecated-usage: allow\n",
            encoding="utf-8",
        )
        violations = check_file(f)
        assert violations == []

    def test_multi_line_multi_violation(self, tmp_path: Path) -> None:
        """複数行に違反が散在する場合、各行が報告される"""
        f = tmp_path / "doc.md"
        f.write_text(
            "L1: python scripts/fetch_data.py\n" "L2: 通常テキスト\n" "L3: import scripts.process_data\n",
            encoding="utf-8",
        )
        violations = check_file(f)
        assert {v.line_no for v in violations} == {1, 3}


class TestRunCheck:
    """run_check / main のスモークテスト"""

    def test_run_check_returns_list(self) -> None:
        """run_check はリストを返す (現リポジトリでは違反ゼロを期待)"""
        result = run_check()
        assert isinstance(result, list)
        # main後の状態ではゼロ件を期待
        assert all(isinstance(v, Violation) for v in result)

    def test_main_returns_zero_when_clean(self, capsys) -> None:
        """違反なし → 終了コード0"""
        # run_check が空を返すよう前提条件を満たしている (Phase 5完了後の状態)
        rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "No deprecated" in out
