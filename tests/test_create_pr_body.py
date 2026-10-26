"""The automated PR body shows what the run actually received and decided (issue #733).

`scripts/create_pr.sh` runs end to end with stub `git` / `gh`; the gate is evaluated by the real
`scripts/auto_merge_gate.sh` from the raw step results.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CREATE_PR_SCRIPT = PROJECT_ROOT / "scripts" / "create_pr.sh"

FAKE_COMMAND = """\
import json, os, shutil, sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["FAKE_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "git":
    if args[:3] == ["diff", "--cached", "--name-status"]:
        print("A\\tdata/raw/x.csv")
    elif args[:3] == ["diff", "--cached", "--name-only"]:
        print("data/raw/x.csv")
    sys.exit(0)
if args[:2] == ["pr", "create"]:
    shutil.copyfile(args[args.index("--body-file") + 1], os.environ["FAKE_GH_BODY_OUT"])
    print(os.environ.get("FAKE_GH_PR_URL", "https://github.com/o/r/pull/123"))
    sys.exit(0)
if args[:2] == ["pr", "merge"]:
    sys.exit(int(os.environ.get("FAKE_GH_MERGE_EXIT", "0")))
if args[:2] == ["label", "create"]:
    sys.exit(0)
sys.exit(f"unexpected call: {name} {args}")
"""

# Step results of a healthy scheduled run; the gate is evaluated by create_pr.sh from these.
HEALTHY_FETCH = {
    "FETCH_STATUS": "success",
    "PROCESS_RESULT": "success",
    "RAW_CHANGED_COUNT": "1",
    "PROCESSING_COVERAGE_STATUS": "complete",
    "VALIDATION_BEFORE_SUCCESS": "true",
    "VALIDATION_SUCCESS": "true",
    "VERIFY_CONTINUITY": "true",
    "CONTINUITY_VALID": "true",
    "SCHEMA_VALID": "true",
}
DAILY = {
    **HEALTHY_FETCH,
    "START_YEAR": "2026",
    "END_YEAR": "2026",
    "MODE": "incremental",
    "TARGET_WEEKS": "37,38,39",
    "TARGET_MONTHS": "08,09",
    "AUTO_MERGE": "true",
}
MANUAL = {
    **HEALTHY_FETCH,
    "VERIFY_CONTINUITY": "false",
    "START_YEAR": "2024",
    "END_YEAR": "2024",
    "MODE": "force",
    "DATA_TYPES": "notifiable_weekly",
    "TARGET_WEEKS": "10",
}


class PullRequest:
    def __init__(self, result: subprocess.CompletedProcess[str], tmp_path: Path) -> None:
        self.result = result
        body = tmp_path / "body.md"
        self.body = body.read_text(encoding="utf-8") if body.exists() else ""
        self.calls: list[list[str]] = [
            json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.github_env = dict(
            line.split("=", 1) for line in (tmp_path / "github_env").read_text(encoding="utf-8").splitlines()
        )
        self.runner_temp = tmp_path / "runner"


def create_pr(tmp_path: Path, workflow_name: str, **env: str) -> PullRequest:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("git", "gh"):
        command = bin_dir / name
        command.write_text(f"#!{sys.executable}\n{FAKE_COMMAND}", encoding="utf-8")
        command.chmod(command.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "github_env").write_text("", encoding="utf-8")
    (tmp_path / "runner").mkdir()
    run_env: dict[str, Any] = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "GITHUB_ENV": str(tmp_path / "github_env"),
        "GITHUB_TOKEN": "fake-token",
        "GITHUB_REPOSITORY": "o/r",
        "CURRENT_DATE": "2026-09-20",
        "FETCH_TIMESTAMP": "20260920_080000",
        "GITHUB_RUN_ID": "4242",
        "NEW_FILES": "1",
        "MODIFIED_FILES": "0",
        "FAKE_CALL_LOG": str(tmp_path / "calls.jsonl"),
        "FAKE_GH_BODY_OUT": str(tmp_path / "body.md"),
        **env,
    }
    result = subprocess.run(
        ["bash", str(CREATE_PR_SCRIPT), workflow_name, "表示名"],
        cwd=tmp_path,
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    return PullRequest(result, tmp_path)


def test_daily_body_shows_the_target_periods_and_mode(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **DAILY)

    assert pr.result.returncode == 0, pr.result.stderr
    assert "- **対象期間**: 2026年 - 2026年" in pr.body
    assert "- **対象週**: 37,38,39" in pr.body
    assert "- **対象月**: 08,09" in pr.body
    assert "- **データタイプ**: 全タイプ" in pr.body
    assert "- **収集モード**: `incremental`" in pr.body
    assert "- [x] データ取得" in pr.body
    assert "- [x] データ検証 (validate-data)" in pr.body
    assert "- [x] データ連続性検証" in pr.body
    assert "ファイル検証済み" not in pr.body
    assert "チェック方式" not in pr.body
    assert "### ⚠️ 警告" not in pr.body
    assert ["gh", "pr", "merge", "123", "--auto", "--squash", "--delete-branch"] in pr.calls


def test_daily_body_shows_gate_inputs_behind_coverage_and_process_blockers(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **{**DAILY, "PROCESSING_COVERAGE_STATUS": "incomplete"})

    assert "- **処理カバレッジ**: incomplete" in pr.body
    assert "- **raw 変更件数**: 1" in pr.body
    assert "- **schema**: passed" in pr.body
    assert "- **ブロック理由**: coverage" in pr.body


def test_daily_failed_pre_validation_is_unchecked_and_warned(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **{**DAILY, "VALIDATION_BEFORE_SUCCESS": "false"})

    assert pr.result.returncode == 0, pr.result.stderr
    assert "- [ ] データ検証 (validate-data) ⚠️ **失敗・要確認**" in pr.body
    assert "### ⚠️ 警告" in pr.body


def test_failed_continuity_is_unchecked(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **{**DAILY, "CONTINUITY_VALID": "false"})

    assert "- [ ] データ連続性検証 ⚠️ **欠損あり (詳細はログを確認)**" in pr.body


def test_manual_body_shows_the_requested_types_mode_and_unrun_validation(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data", **MANUAL)

    assert pr.result.returncode == 0, pr.result.stderr
    assert "- **データタイプ**: `notifiable_weekly`" in pr.body
    assert "- **収集モード**: `force`" in pr.body
    assert "- **対象週**: 10" in pr.body
    assert "対象月" not in pr.body
    assert "- [ ] データ検証 (未実施)" in pr.body
    assert "データ連続性検証" not in pr.body
    assert "データタイプ**: ALL" not in pr.body
    assert "全ファイル取得" not in pr.body


def test_check_range_values_are_restricted_to_their_character_set(tmp_path: Path) -> None:
    pr = create_pr(
        tmp_path, "fetch-data", **{**MANUAL, "DATA_TYPES": "notifiable_weekly`<!--", "TARGET_WEEKS": "10<!--"}
    )

    assert "- **データタイプ**: `notifiable_weekly`" in pr.body
    assert "- **対象週**: 10\n" in pr.body
    assert "<!--" not in pr.body


def test_failed_fetch_blocks_requested_auto_merge_with_a_warning(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **{**DAILY, "FETCH_STATUS": "failed"})

    assert pr.result.returncode == 0, pr.result.stderr
    assert "- [ ] データ取得 ⚠️ **失敗**" in pr.body
    assert (
        "::warning title=Auto-merge blocked::PR #123 is waiting for manual review (blockers: fetch)" in pr.result.stdout
    )
    assert not any(call[1:3] == ["pr", "merge"] for call in pr.calls)
    assert pr.github_env["AUTO_MERGE_GATE_STATUS"] == "blocked"


def test_auto_merge_setup_failure_is_exported_and_annotated(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **DAILY, FAKE_GH_MERGE_EXIT="2")

    assert pr.result.returncode == 0, pr.result.stderr
    assert pr.github_env["AUTO_MERGE_SETUP_FAILED"] == "true"
    assert (
        "::warning title=Auto-merge setup failed::gh pr merge --auto failed for PR #123 (exit 2); manual merge is required"
        in pr.result.stdout
    )


def test_unreadable_pr_number_marks_auto_merge_setup_failed(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **DAILY, FAKE_GH_PR_URL="created")

    assert pr.result.returncode == 0, pr.result.stderr
    assert pr.github_env["AUTO_MERGE_SETUP_FAILED"] == "true"
    assert "::warning title=Auto-merge setup failed::" in pr.result.stdout
    assert not any(call[1:3] == ["pr", "merge"] for call in pr.calls)


def test_successful_auto_merge_setup_is_not_marked_failed(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **DAILY)

    assert "AUTO_MERGE_SETUP_FAILED" not in pr.github_env
    assert "::warning" not in pr.result.stdout


def test_body_file_is_private_and_removed(tmp_path: Path) -> None:
    pr = create_pr(tmp_path, "fetch-data-daily", **DAILY)

    [create] = [call for call in pr.calls if call[1:3] == ["pr", "create"]]
    body_file = Path(create[create.index("--body-file") + 1])
    assert body_file.parent == pr.runner_temp
    assert body_file.name.startswith("pr_body.")
    assert not body_file.exists()


@pytest.mark.parametrize(
    ("workflow_name", "env"),
    [
        pytest.param(
            "fetch-data-weekly",
            {**HEALTHY_FETCH, "START_YEAR": "2025", "END_YEAR": "2026", "MODE": "force"},
            id="weekly",
        ),
        pytest.param("process-data", {"PROCESS_RESULT": "success", "TARGET_FILES": "data/raw/a.csv"}, id="process"),
    ],
)
def test_other_workflows_still_produce_a_body(tmp_path: Path, workflow_name: str, env: dict[str, str]) -> None:
    pr = create_pr(tmp_path, workflow_name, **env)

    assert pr.result.returncode == 0, pr.result.stderr
    assert "## 🤖 自動データ更新" in pr.body
    assert "ファイル検証済み" not in pr.body


def test_weekly_body_uses_the_combined_check_range(tmp_path: Path) -> None:
    pr = create_pr(
        tmp_path, "fetch-data-weekly", **{**HEALTHY_FETCH, "START_YEAR": "2025", "END_YEAR": "2026", "MODE": "force"}
    )

    assert "- **対象期間**: 2025年 - 2026年" in pr.body
    assert "- **収集モード**: `force`" in pr.body
    assert "- [x] データ検証 (validate-data)" in pr.body
    assert "既存ファイルの更新確認" not in pr.body
