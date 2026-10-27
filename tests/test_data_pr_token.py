"""Opt-in GitHub App token for the automated data PRs (issue #763).

A PR opened with the job's GITHUB_TOKEN leaves its `pull_request` CI runs waiting for approval, so
the data workflows can push and open the PR with a GitHub App installation token instead. The App
path is off unless `DATA_PR_USE_APP` is `true`; once selected it fails closed instead of falling
back to GITHUB_TOKEN. The push authenticates only with the selected token, through a helper that
reads it from the environment, never with the token `actions/checkout` could have persisted.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = PROJECT_ROOT / ".github" / "actions" / "data-pr-token" / "action.yml"
WORKFLOW_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"
CREATE_PR_SCRIPT = PROJECT_ROOT / "scripts" / "create_pr.sh"

# The official action at a full commit SHA, its release in a `# vX.Y.Z` comment on the same line. Which
# SHA and release are right is not fixed here: Dependabot proposes them and the vetting script checks
# that the tag resolves to the SHA and that no advisory covers it (issue #763).
APP_TOKEN_ACTION = re.compile(r"actions/create-github-app-token@[0-9a-f]{40}")
APP_TOKEN_RELEASE_COMMENT = r" # v\d+\.\d+\.\d+"
TOKEN_STEP = "Create data PR token"
PR_STEP = "Create Pull Request"
TOKEN_STEP_WITH = {
    "use-app": "${{ vars.DATA_PR_USE_APP }}",
    "client-id": "${{ vars.DATA_PR_APP_CLIENT_ID }}",
    "private-key": "${{ secrets.DATA_PR_APP_PRIVATE_KEY }}",
    "github-token": "${{ secrets.GITHUB_TOKEN }}",
}
# (workflow file, job, the condition under which a PR, and so a token, is needed)
CALLERS = [
    pytest.param(
        "_fetch-data-common.yml",
        "fetch-data",
        "env.HAS_DATA_CHANGES == 'true' && inputs.dry_run != true",
        id="fetch",
    ),
    pytest.param(
        "process-data.yml", "process-data", "env.HAS_CHANGES == 'true' && env.DRY_RUN != 'true'", id="process"
    ),
    pytest.param("migrate-metadata.yml", "migrate", "steps.stage.outputs.has_changes == 'true'", id="migrate"),
]


def load_action() -> dict[str, Any]:
    action = yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))
    assert isinstance(action, dict)
    return action


def action_step(step_id: str) -> dict[str, Any]:
    [step] = [step for step in load_action()["runs"]["steps"] if step.get("id") == step_id]
    assert isinstance(step, dict)
    return step


def pinned_app_token(uses: str, action_text: str) -> bool:
    if not APP_TOKEN_ACTION.fullmatch(uses):
        return False
    line = re.compile(rf"^\s*uses: {re.escape(uses)}{APP_TOKEN_RELEASE_COMMENT}$", re.MULTILINE)
    return line.search(action_text) is not None


def job_steps(workflow_name: str, job: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load((WORKFLOW_DIRECTORY / workflow_name).read_text(encoding="utf-8"))
    steps = workflow["jobs"][job]["steps"]
    assert isinstance(steps, list)
    return steps


def step_named(steps: list[dict[str, Any]], name: str) -> tuple[int, dict[str, Any]]:
    for index, step in enumerate(steps):
        if step.get("name") == name:
            return index, step
    pytest.fail(f"missing step: {name}")


# --- the shared action ---------------------------------------------------------------------------


def test_app_token_comes_from_the_pinned_official_action_only_when_selected() -> None:
    step = action_step("app-token")

    assert pinned_app_token(step["uses"], ACTION_PATH.read_text(encoding="utf-8"))
    assert step["if"] == "steps.mode.outputs.use-app == 'true'"


NEXT_SHA = "a1" * 20


@pytest.mark.parametrize(
    ("uses", "comment", "pinned"),
    [
        pytest.param(f"actions/create-github-app-token@{NEXT_SHA}", " # v3.4.0", True, id="later-release"),
        pytest.param("actions/create-github-app-token@v3.4.0", " # v3.4.0", False, id="tag-ref"),
        pytest.param(f"actions/create-github-app-token@{NEXT_SHA[:39]}", " # v3.4.0", False, id="short-sha"),
        pytest.param(f"actions/create-github-app-token@{NEXT_SHA.upper()}", " # v3.4.0", False, id="upper-sha"),
        pytest.param(f"someone/create-github-app-token@{NEXT_SHA}", " # v3.4.0", False, id="other-owner"),
        pytest.param(f"actions/create-github-app-token-fork@{NEXT_SHA}", " # v3.4.0", False, id="other-repo"),
        pytest.param(f"actions/create-github-app-token/sub@{NEXT_SHA}", " # v3.4.0", False, id="subpath"),
        pytest.param(f"actions/create-github-app-token@{NEXT_SHA}", "", False, id="no-comment"),
        pytest.param(f"actions/create-github-app-token@{NEXT_SHA}", " # v3", False, id="floating-comment"),
    ],
)
def test_app_token_pin_check_accepts_a_new_release_only_as_a_full_sha_of_the_official_action(
    uses: str, comment: str, pinned: bool
) -> None:
    """Issue #763: Dependabot now proposes this pin, so the test must not hard-code one SHA."""
    action_text = f"  steps:\n    - id: app-token\n      uses: {uses}{comment}\n"

    assert pinned_app_token(uses, action_text) is pinned


def test_app_token_pin_check_reads_the_comment_from_the_same_uses_line() -> None:
    uses = f"actions/create-github-app-token@{NEXT_SHA}"
    action_text = f"    - id: app-token\n      # v3.4.0\n      uses: {uses}\n"

    assert not pinned_app_token(uses, action_text)


def test_app_token_is_scoped_to_this_repository_with_write_on_contents_and_pull_requests_only() -> None:
    step = action_step("app-token")

    # Pull requests write also covers creating labels and labelling the PR; no issues permission.
    # `owner` without `repositories` would widen the token to every repository of the installation,
    # and `skip-token-revoke` would keep it alive after the job.
    assert step["with"] == {
        "client-id": "${{ inputs.client-id }}",
        "private-key": "${{ inputs.private-key }}",
        "repositories": "${{ github.repository }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }


def test_selected_token_is_the_app_token_when_selected_and_github_token_otherwise() -> None:
    action = load_action()

    assert action["outputs"]["token"]["value"] == (
        "${{ steps.mode.outputs.use-app == 'true' && steps.app-token.outputs.token || inputs.github-token }}"
    )
    # The `||` above would fall back on an empty App token, so an empty one fails the step instead.
    guard = action_step("guard")
    assert guard["if"] == "steps.mode.outputs.use-app == 'true' && steps.app-token.outputs.token == ''"
    assert "exit 1" in guard["run"]


def test_mode_step_reads_the_private_key_only_as_a_presence_flag() -> None:
    step = action_step("mode")

    assert step["env"] == {
        "USE_APP": "${{ inputs.use-app }}",
        "CLIENT_ID": "${{ inputs.client-id }}",
        "HAS_PRIVATE_KEY": "${{ inputs.private-key != '' }}",
    }
    assert "${{" not in step["run"]


def run_mode(tmp_path: Path, use_app: str, client_id: str = "", has_private_key: bool = False) -> tuple[int, str, str]:
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", action_step("mode")["run"]],
        env={
            "PATH": os.environ["PATH"],
            "GITHUB_OUTPUT": str(output),
            "USE_APP": use_app,
            "CLIENT_ID": client_id,
            "HAS_PRIVATE_KEY": str(has_private_key).lower(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr, output.read_text(encoding="utf-8")


@pytest.mark.parametrize("use_app", ["", "false"])
@pytest.mark.parametrize("configured", [True, False], ids=["credentials-set", "credentials-unset"])
def test_unset_or_false_keeps_github_token(tmp_path: Path, use_app: str, configured: bool) -> None:
    code, _, output = run_mode(tmp_path, use_app, "Iv1.client" if configured else "", configured)

    assert code == 0
    assert output == "use-app=false\n"


def test_true_with_both_credentials_selects_the_app(tmp_path: Path) -> None:
    code, _, output = run_mode(tmp_path, "true", "Iv1.client", True)

    assert code == 0
    assert output == "use-app=true\n"


@pytest.mark.parametrize(
    ("client_id", "has_private_key", "missing"),
    [
        ("", True, ["DATA_PR_APP_CLIENT_ID"]),
        ("Iv1.client", False, ["DATA_PR_APP_PRIVATE_KEY"]),
        ("", False, ["DATA_PR_APP_CLIENT_ID", "DATA_PR_APP_PRIVATE_KEY"]),
    ],
)
def test_true_with_missing_credentials_fails_closed(
    tmp_path: Path, client_id: str, has_private_key: bool, missing: list[str]
) -> None:
    code, log, output = run_mode(tmp_path, "true", client_id, has_private_key)

    assert code == 1
    assert "::error" in log
    for name in missing:
        assert name in log
    assert output == ""


@pytest.mark.parametrize("use_app", ["TRUE", "yes", "1", " true", "enable-it"])
def test_invalid_selector_fails_without_echoing_it(tmp_path: Path, use_app: str) -> None:
    code, log, output = run_mode(tmp_path, use_app, "Iv1.client", True)

    assert code == 1
    assert "::error" in log
    assert "DATA_PR_USE_APP" in log
    assert "enable-it" not in log
    assert output == ""


# --- the three callers ---------------------------------------------------------------------------


@pytest.mark.parametrize(("workflow_name", "job", "condition"), CALLERS)
def test_checkout_persists_no_credentials(workflow_name: str, job: str, condition: str) -> None:
    _, checkout = step_named(job_steps(workflow_name, job), "Checkout repository")

    assert checkout["with"]["persist-credentials"] is False
    assert "token" not in checkout["with"]


@pytest.mark.parametrize(("workflow_name", "job", "condition"), CALLERS)
def test_token_is_minted_right_before_the_pr_and_only_when_a_pr_is_needed(
    workflow_name: str, job: str, condition: str
) -> None:
    steps = job_steps(workflow_name, job)
    token_index, token_step = step_named(steps, TOKEN_STEP)
    pr_index, pr_step = step_named(steps, PR_STEP)

    assert token_step["id"] == "data-pr-token"
    assert token_step["uses"] == "./.github/actions/data-pr-token"
    assert token_step["with"] == TOKEN_STEP_WITH
    # The App token expires after an hour: mint it after every long step, never for dry runs or no-change runs.
    assert token_index + 1 == pr_index
    assert token_step["if"] == pr_step["if"] == condition


@pytest.mark.parametrize(("workflow_name", "job", "condition"), CALLERS)
def test_pull_request_step_uses_the_selected_token(workflow_name: str, job: str, condition: str) -> None:
    _, pr_step = step_named(job_steps(workflow_name, job), PR_STEP)

    assert pr_step["env"]["GITHUB_TOKEN"] == "${{ steps.data-pr-token.outputs.token }}"


@pytest.mark.parametrize(("workflow_name", "job", "condition"), CALLERS)
def test_private_key_reaches_only_the_token_step(workflow_name: str, job: str, condition: str) -> None:
    text = (WORKFLOW_DIRECTORY / workflow_name).read_text(encoding="utf-8")

    assert text.count("secrets.DATA_PR_APP_PRIVATE_KEY") == 1


@pytest.fixture
def stage_repo(tmp_path: Path) -> Iterator[tuple[Path, dict[str, str], str]]:
    _, stage = step_named(job_steps("migrate-metadata.yml", "migrate"), "Stage migrated metadata")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
    }
    repo = tmp_path / "repo"
    (repo / "data" / "raw" / ".metadata").mkdir(parents=True)
    (repo / "data" / "processed" / ".metadata").mkdir(parents=True)
    (repo / "data" / "raw" / ".metadata" / "a.json").write_text("{}", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    (tmp_path / "github_output").write_text("", encoding="utf-8")
    yield repo, env, stage["run"]


def test_migration_stage_reports_changes(stage_repo: tuple[Path, dict[str, str], str]) -> None:
    repo, env, script = stage_repo
    (repo / "data" / "processed" / ".metadata" / "b.json").write_text("{}", encoding="utf-8")

    subprocess.run(["bash", "-e", "-c", script], cwd=repo, env=env, check=True, capture_output=True)

    assert Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "has_changes=true\n"


def test_migration_stage_without_changes_needs_no_pr(stage_repo: tuple[Path, dict[str, str], str]) -> None:
    repo, env, script = stage_repo

    subprocess.run(["bash", "-e", "-c", script], cwd=repo, env=env, check=True, capture_output=True)

    assert Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "has_changes=false\n"


# --- create_pr.sh push and PR authentication -----------------------------------------------------

SELECTED = "selected-token"
STALE = "stale-checkout-token"

FAKE_GH = """\
#!/usr/bin/env bash
printf '%s\\t%s\\n' "$1 $2" "${GH_TOKEN:-}" >> "$FAKE_GH_LOG"
if [ "$1 $2" = "pr create" ]; then
  echo "https://github.com/o/r/pull/7"
fi
"""


class GitServer:
    """Smart-HTTP git remote (`git http-backend`) that accepts only `x-access-token:<SELECTED>`."""

    def __init__(self, root: Path, real_git: str) -> None:
        self.root = root
        self.authorizations: list[list[str]] = []
        expected = "Basic " + base64.b64encode(f"x-access-token:{SELECTED}".encode()).decode()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.handle_git()

            def do_POST(self) -> None:
                self.handle_git()

            def handle_git(self) -> None:
                received = self.headers.get_all("Authorization") or []
                server.authorizations.append(received)
                if received != [expected]:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="test"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path, _, query = self.path.partition("?")
                cgi_env = {
                    "PATH": os.environ["PATH"],
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_PROJECT_ROOT": str(server.root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "REMOTE_USER": "x-access-token",
                    "REQUEST_METHOD": self.command,
                    "PATH_INFO": path,
                    "QUERY_STRING": query,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)),
                    "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
                    "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
                }
                output = subprocess.run(
                    [real_git, "http-backend"], input=body, env=cgi_env, capture_output=True, check=True
                ).stdout
                head, _, payload = output.partition(b"\r\n\r\n")
                status = 200
                headers = []
                for line in head.decode().split("\r\n"):
                    name, _, value = line.partition(":")
                    if name.lower() == "status":
                        status = int(value.split()[0])
                    else:
                        headers.append((name, value.strip()))
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> GitServer:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def run_create_pr(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], GitServer, Path, list[str]]:
    """Run create_pr.sh against a local HTTP remote, as after a checkout that persisted credentials."""
    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Log git's argv, then run the real git: the push must really authenticate.
    (bin_dir / "git").write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$FAKE_GIT_LOG"\nexec "{real_git}" "$@"\n', encoding="utf-8"
    )
    (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
    for name in ("git", "gh"):
        (bin_dir / name).chmod(0o755)
    (tmp_path / "runner").mkdir()
    (tmp_path / "github_env").write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "GITHUB_ENV": str(tmp_path / "github_env"),
        "GITHUB_REPOSITORY": "o/r",
        "CURRENT_DATE": "2026-10-27",
        "FETCH_TIMESTAMP": "20261027_090000",
        "GITHUB_RUN_ID": "4242",
        "PROCESS_RESULT": "success",
        "FAKE_GIT_LOG": str(tmp_path / "git.log"),
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "GITHUB_TOKEN": SELECTED,
        # A job-level GH_TOKEN must not make gh act with a different token than the push.
        "GH_TOKEN": "unrelated-token",
    }
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    subprocess.run([real_git, "init", "-q", "--bare", str(remotes / "r.git")], env=env, check=True)
    repo = tmp_path / "repo"
    subprocess.run([real_git, "init", "-q", "-b", "main", str(repo)], env=env, check=True)
    (repo / "data" / "processed").mkdir(parents=True)
    (repo / "data" / "processed" / "normalized_x.csv").write_text("a\n1\n", encoding="utf-8")
    with GitServer(remotes, real_git) as server:
        env["GITHUB_SERVER_URL"] = server.url
        for args in (
            ["commit", "-q", "--allow-empty", "-m", "init"],
            ["remote", "add", "origin", f"{server.url}/r.git"],
            # What actions/checkout leaves behind when credentials are persisted.
            ["config", f"http.{server.url}/.extraheader", f"AUTHORIZATION: basic {STALE}"],
            ["add", "data"],
        ):
            subprocess.run([real_git, *args], cwd=repo, env=env, check=True, capture_output=True)
        result = subprocess.run(
            ["bash", str(CREATE_PR_SCRIPT), "process-data", "手動実行"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    git_log = (tmp_path / "git.log").read_text(encoding="utf-8").splitlines()
    return result, server, repo, git_log


def test_push_authenticates_only_with_the_selected_token(tmp_path: Path) -> None:
    result, server, repo, _ = run_create_pr(tmp_path)

    assert result.returncode == 0, result.stderr
    branch = "data-process-20261027_090000-4242"
    pushed = subprocess.run(
        ["git", "--git-dir", str(server.root / "r.git"), "rev-parse", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert pushed.stdout.strip() == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    # The checkout's header is really there, so a push that still sent it would fail this test.
    assert STALE in (repo / ".git" / "config").read_text(encoding="utf-8")
    assert server.authorizations
    assert all(STALE not in header for headers in server.authorizations for header in headers)


def test_selected_token_never_reaches_argv_remote_url_or_git_config(tmp_path: Path) -> None:
    result, _, repo, git_log = run_create_pr(tmp_path)

    assert result.returncode == 0, result.stderr
    assert any(line.split()[-2:] == ["origin", "data-process-20261027_090000-4242"] for line in git_log)
    assert all(SELECTED not in line for line in git_log)
    assert SELECTED not in (repo / ".git" / "config").read_text(encoding="utf-8")


def test_pull_request_is_created_with_the_token_that_pushed(tmp_path: Path) -> None:
    result, _, _, _ = run_create_pr(tmp_path)

    assert result.returncode == 0, result.stderr
    gh_calls = [line.split("\t") for line in (tmp_path / "gh.log").read_text(encoding="utf-8").splitlines()]
    assert ["pr create", SELECTED] in gh_calls
    assert {token for _, token in gh_calls} == {SELECTED}
