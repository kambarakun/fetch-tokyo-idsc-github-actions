"""Tests for the Dependabot PR vetting script (issue #762).

Every check runs through `vet_pull_request` against a synthetic PR assembled from URL-keyed
responses, so the tests cover which file feeds which parser and which API answers which
check. The numbers mirror the PRs the issue was designed against (#745, #747, #748).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vet_dependabot_prs as vet

# The `created_at` of #748.
NOW = datetime(2026, 9, 28, 0, 7, 14, tzinfo=UTC)
REPO = "owner/name"
API = f"{vet.GITHUB_API}/repos/{REPO}"
BASE_SHA = "b" * 40
MERGE_BASE = "c" * 40
HEAD_SHA = "d" * 40
SETUP_UV_OLD = "1" * 40
SETUP_UV_NEW = "c18668ad3cf93ea998bef934396af7bb5c839dc7"
ACTIONLINT_SHA = "320fcdd9c860767cf17fab3b20e22e739d5d02b8"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "vet_dependabot_prs" / "pr-748"
PYPROJECT = '[project]\nname = "app"\nrequires-python = ">=3.11"\n'
DEPENDABOT_YML = """
version: 2
updates:
  - package-ecosystem: "github-actions"
    cooldown:
      default-days: 7
  - package-ecosystem: "uv"
    cooldown:
      default-days: 7
  - package-ecosystem: "pre-commit"
    cooldown:
      default-days: 7
"""
GREEN_RUNS = [("codecov/patch", "success"), ("lint", "success"), ("test", "success"), ("claude-review", "skipped")]


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _not_found() -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 404
    return requests.HTTPError(response=response)


def _fetchers(responses: dict[str, Any]) -> vet.Fetchers:
    def fetch_json(url: str) -> Any:
        if url not in responses:
            raise _not_found()
        return responses[url]

    def fetch_text(url: str) -> str:
        if url not in responses:
            raise _not_found()
        return responses[url]

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        assert url == vet.OSV_QUERY
        return responses.get(f"osv:{vet._osv_key(payload)}", {})

    return fetch_json, fetch_text, post_json


def _uv_lock(**versions: str) -> str:
    project = '[[package]]\nname = "app"\nversion = "1.0.0"\nsource = { editable = "." }\n\n'
    return project + "".join(
        f'[[package]]\nname = "{name}"\nversion = "{version}"\nsource = {{ registry = "https://pypi.org/simple" }}\n\n'
        for name, version in versions.items()
    )


def _workflow(action: str, sha: str, tag: str) -> str:
    comment = f" # {tag}" if tag else ""
    return f"jobs:\n  test:\n    steps:\n      - name: Step\n        uses: {action}@{sha}{comment}\n"


def _action_yml(using: str = "node24", inputs: str = "", outputs: str = "") -> str:
    text = f"name: Action\ndescription: Synthetic\nruns:\n  using: {using}\n  main: index.js\n"
    if inputs:
        text += f"inputs:\n{inputs}"
    if outputs:
        text += f"outputs:\n{outputs}"
    return text


def _metadata_url(action: str, sha: str, filename: str = "action.yml") -> str:
    """The contents API at the pinned commit; `action` may carry a subpath (`owner/repo/init`)."""
    owner, name, *subpath = action.split("/")
    return f"{vet.GITHUB_API}/repos/{owner}/{name}/contents/{'/'.join([*subpath, filename])}?ref={sha}"


def _pre_commit(repo: str, rev: str, comment: str = "") -> str:
    suffix = f"  # {comment}" if comment else ""
    return f"repos:\n  - repo: {repo}\n    rev: {rev}{suffix}\n    hooks:\n      - id: hook\n  - repo: local\n    hooks: []\n"


def _pr(
    responses: dict[str, Any],
    *,
    head_ref: str,
    files: Mapping[str, tuple[str | None, str | None]],
    number: int = 748,
    head_sha: str = HEAD_SHA,
    created_at: datetime = NOW,
    author: str = vet.DEPENDABOT_AUTHOR,
    human_commits: int = 0,
    check_runs: list[tuple[str, str]] | None = None,
) -> None:
    """Register one PR: its metadata, changed files at both refs, commits and check runs."""
    responses[f"{API}/pulls/{number}"] = {
        "number": number,
        "user": {"login": author},
        "created_at": _iso(created_at),
        "head": {"ref": head_ref, "sha": head_sha},
        "base": {"sha": BASE_SHA},
    }
    responses[f"{API}/pulls/{number}/files?per_page=100&page=1"] = [{"filename": path} for path in files]
    responses[f"{API}/pulls/{number}/commits?per_page=100&page=1"] = [{"author": {"login": vet.DEPENDABOT_AUTHOR}}] + [
        {"author": {"login": "human"}} for _ in range(human_commits)
    ]
    responses[f"{API}/compare/{BASE_SHA}...{head_sha}"] = {"merge_base_commit": {"sha": MERGE_BASE}}
    for path, (before, after) in files.items():
        if before is not None:
            responses[f"{API}/contents/{path}?ref={MERGE_BASE}"] = before
        if after is not None:
            responses[f"{API}/contents/{path}?ref={head_sha}"] = after
        # Every pinned action gets unchanged metadata unless a test says otherwise.
        for text in (before or "", after or ""):
            for action, sha in re.findall(r"uses:\s*([\w./-]+)@([0-9a-f]{40})", text):
                responses.setdefault(_metadata_url(action, sha), _action_yml())
    responses.setdefault(f"{API}/contents/pyproject.toml?ref={head_sha}", PYPROJECT)
    responses[f"{API}/contents/.github/dependabot.yml?ref={head_sha}"] = DEPENDABOT_YML
    runs = GREEN_RUNS if check_runs is None else check_runs
    responses[f"{API}/commits/{head_sha}/check-runs?per_page=100&page=1"] = {
        "total_count": len(runs),
        "check_runs": [
            {"name": name, "status": "in_progress" if state == "in_progress" else "completed", "conclusion": state}
            for name, state in runs
        ],
    }


def _pypi(
    responses: dict[str, Any],
    name: str,
    releases: dict[str, datetime],
    *,
    yanked: tuple[str, ...] = (),
    requires_python: str = ">=3.7",
) -> None:
    for version, uploaded in releases.items():
        files = [{"upload_time_iso_8601": _iso(uploaded), "yanked": version in yanked, "packagetype": "sdist"}]
        responses[vet.PYPI_RELEASE.format(name=name, version=version)] = {
            "info": {
                "yanked": version in yanked,
                "yanked_reason": "broken" if version in yanked else None,
                "requires_python": requires_python,
            },
            "urls": files,
        }
    responses[vet.PYPI_PROJECT.format(name=name)] = {
        "releases": {
            version: [{"upload_time_iso_8601": _iso(at), "yanked": version in yanked}]
            for version, at in releases.items()
        }
    }


def _tag(responses: dict[str, Any], repo: str, tag: str, sha: str, *, annotated_at: datetime | None = None) -> None:
    base = f"{vet.GITHUB_API}/repos/{repo}/git"
    if annotated_at is None:
        responses[f"{base}/ref/tags/{tag}"] = {"object": {"type": "commit", "sha": sha}}
        return
    tag_object = "e" * 40
    responses[f"{base}/ref/tags/{tag}"] = {"object": {"type": "tag", "sha": tag_object}}
    responses[f"{base}/tags/{tag_object}"] = {
        "tagger": {"date": _iso(annotated_at)},
        "object": {"type": "commit", "sha": sha},
    }


def _releases(responses: dict[str, Any], repo: str, releases: dict[str, datetime]) -> None:
    responses[f"{vet.GITHUB_API}/repos/{repo}/releases?per_page=100&page=1"] = [
        {"tag_name": tag, "published_at": _iso(at), "draft": False, "prerelease": False} for tag, at in releases.items()
    ]
    for tag, at in releases.items():
        responses[f"{vet.GITHUB_API}/repos/{repo}/releases/tags/{tag}"] = {"published_at": _iso(at)}


@pytest.fixture
def uv_pr() -> dict[str, Any]:
    """#748: ruff 0.16.7 -> 0.16.8 in the build-tools group, every check OK."""
    responses: dict[str, Any] = {}
    _pr(
        responses,
        head_ref="dependabot/uv/build-tools-564d0085cf",
        files={
            "pyproject.toml": (PYPROJECT, PYPROJECT),
            "uv.lock": (_uv_lock(ruff="0.16.7", requests="2.34.2"), _uv_lock(ruff="0.16.8", requests="2.34.2")),
        },
    )
    _pypi(
        responses,
        "ruff",
        {
            "0.16.7": datetime(2026, 9, 10, tzinfo=UTC),
            "0.16.8": datetime(2026, 9, 16, 15, 53, 57, tzinfo=UTC),
            "0.16.9": datetime(2026, 9, 24, 20, 37, tzinfo=UTC),
            "0.16.10": datetime(2026, 10, 1, tzinfo=UTC),
        },
    )
    return responses


@pytest.fixture
def action_pr() -> dict[str, Any]:
    """#745: setup-uv v10.1.0 -> v10.2.0 across two workflows, lightweight tag, no later release."""
    responses: dict[str, Any] = {}
    files = {
        f".github/workflows/{name}.yml": (
            _workflow("astral-sh/setup-uv", SETUP_UV_OLD, "v10.1.0"),
            _workflow("astral-sh/setup-uv", SETUP_UV_NEW, "v10.2.0"),
        )
        for name in ("test", "watchdog")
    }
    _pr(responses, head_ref="dependabot/github_actions/astral-sh/setup-uv-10.2.0", files=files)
    _tag(responses, "astral-sh/setup-uv", "v10.2.0", SETUP_UV_NEW)
    _releases(
        responses,
        "astral-sh/setup-uv",
        {"v10.1.0": datetime(2026, 9, 1, tzinfo=UTC), "v10.2.0": datetime(2026, 9, 21, 13, 15, 15, tzinfo=UTC)},
    )
    return responses


def _vet(responses: dict[str, Any], number: int = 748) -> vet.PullRequestVerdict:
    fetch_json, fetch_text, post_json = _fetchers(responses)
    return vet.vet_pull_request(fetch_json, fetch_text, post_json, repo=REPO, number=number)


def _checks(verdict: vet.PullRequestVerdict) -> dict[tuple[str, str], vet.CheckResult]:
    return {(check.check_id, check.dependency): check for check in verdict.checks}


def _main(monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any], *argv: str) -> int:
    monkeypatch.setattr(vet, "make_fetchers", lambda token: _fetchers(responses))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return vet.main(["--repo", REPO, *argv])


# --- parsers ---------------------------------------------------------------------------------


def test_uv_lock_bumps_are_parsed_from_merge_base_and_head(uv_pr: dict[str, Any]) -> None:
    verdict = _vet(uv_pr)

    assert verdict.ecosystem == "uv"
    assert verdict.bumps == [vet.Bump("ruff", "0.16.7", "0.16.8", "pypi")]
    # A dependency added by the lock is vetted too; the editable project itself is not a bump.
    assert vet.uv_lock_bumps(_uv_lock(), _uv_lock(idna="3.10")) == [vet.Bump("idna", None, "3.10", "pypi")]


def test_lock_with_two_versions_pairs_each_new_version_with_the_one_it_replaced() -> None:
    before = _uv_lock() + "".join(
        f'[[package]]\nname = "numpy"\nversion = "{version}"\nsource = {{ registry = "https://pypi.org/simple" }}\n\n'
        for version in ("1.9.0", "2.0.0")
    )
    after = before.replace('version = "1.9.0"', 'version = "1.10.0"')

    assert vet.uv_lock_bumps(before, after) == [vet.Bump("numpy", "1.9.0", "1.10.0", "pypi")]


def test_workflow_uses_bumps_are_parsed_with_sha_and_version_comment(action_pr: dict[str, Any]) -> None:
    verdict = _vet(action_pr)

    # Two workflows move the same pin: one bump, one row per check.
    assert verdict.bumps == [vet.Bump("astral-sh/setup-uv", "v10.1.0", "v10.2.0", "action", sha=SETUP_UV_NEW)]
    assert sum(check.check_id == "tag_sha" for check in verdict.checks) == 1
    subpath = vet.workflow_bumps(None, _workflow("github/codeql-action/init", SETUP_UV_NEW, "v4.1.0"))
    assert subpath == [vet.Bump("github/codeql-action", None, "v4.1.0", "action", sha=SETUP_UV_NEW)]


def test_pre_commit_rev_bumps_are_parsed_including_sha_pinned_rev() -> None:
    by_tag = vet.pre_commit_bumps(
        _pre_commit("https://github.com/rbubley/mirrors-prettier", "v3.8.5"),
        _pre_commit("https://github.com/rbubley/mirrors-prettier", "v3.9.8"),
    )
    by_sha = vet.pre_commit_bumps(
        _pre_commit("https://github.com/rhysd/actionlint", "a" * 40, "frozen: v1.7.11"),
        _pre_commit("https://github.com/rhysd/actionlint", ACTIONLINT_SHA, "frozen: v1.7.12"),
    )

    assert by_tag == [vet.Bump("rbubley/mirrors-prettier", "v3.8.5", "v3.9.8", "pre-commit")]
    assert by_sha == [vet.Bump("rhysd/actionlint", "v1.7.11", "v1.7.12", "pre-commit", sha=ACTIONLINT_SHA)]


# --- per-dependency checks -----------------------------------------------------------------


def test_yanked_release_is_a_block(uv_pr: dict[str, Any]) -> None:
    _pypi(uv_pr, "ruff", {"0.16.8": datetime(2026, 9, 16, tzinfo=UTC)}, yanked=("0.16.8",))

    check = _checks(_vet(uv_pr))[("yanked", "ruff")]

    assert check.verdict == "BLOCK"
    # `yanked_reason` is publisher-written free text; only the structured flag reaches the report.
    assert "broken" not in check.detail


def test_pypi_advisory_is_a_block(uv_pr: dict[str, Any]) -> None:
    uv_pr["osv:PyPI/ruff@0.16.8"] = {"vulns": [{"id": "GHSA-9wx4-h78v-vm56"}]}

    check = _checks(_vet(uv_pr))[("advisory", "ruff")]

    assert check.verdict == "BLOCK"
    assert check.links == ["https://osv.dev/vulnerability/GHSA-9wx4-h78v-vm56"]


@pytest.mark.parametrize(("tag", "expected"), [("v45.0.7", "BLOCK"), ("v46.0.1", "OK")])
def test_github_action_advisory_is_matched_against_ranges_client_side(tag: str, expected: str) -> None:
    sha = "f" * 40
    responses: dict[str, Any] = {}
    files = {".github/workflows/ci.yml": (None, _workflow("tj-actions/changed-files", sha, tag))}
    _pr(responses, head_ref="dependabot/github_actions/tj-actions/changed-files", files=files)
    _tag(responses, "tj-actions/changed-files", tag, sha)
    responses[f"{vet.GITHUB_API}/repos/tj-actions/changed-files/git/commits/{sha}"] = {
        "committer": {"date": "2026-09-01T00:00:00Z"}
    }
    responses[f"{vet.GITHUB_API}/repos/tj-actions/changed-files/releases?per_page=100&page=1"] = []
    # OSV ignores `version` for this ecosystem, so the script must ask without it.
    responses["osv:GitHub Actions/tj-actions/changed-files@*"] = {
        "vulns": [
            {
                "id": "GHSA-mrrh-fwg8-r2c3",
                "affected": [
                    {
                        "package": {"ecosystem": "GitHub Actions", "name": "tj-actions/changed-files"},
                        "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "46.0.1"}]}],
                        "versions": [],
                    }
                ],
            },
            {
                # An advisory for another action must not match by accident.
                "id": "GHSA-other",
                "affected": [{"package": {"ecosystem": "GitHub Actions", "name": "other/action"}, "versions": [tag]}],
            },
        ]
    }

    assert _checks(_vet(responses))[("advisory", "tj-actions/changed-files")].verdict == expected


def test_github_action_advisory_listing_the_exact_version_is_a_block(action_pr: dict[str, Any]) -> None:
    action_pr["osv:GitHub Actions/astral-sh/setup-uv@*"] = {
        "vulns": [
            {
                "id": "GHSA-listed",
                "affected": [
                    {"package": {"ecosystem": "GitHub Actions", "name": "astral-sh/setup-uv"}, "versions": ["10.2.0"]}
                ],
            }
        ]
    }

    check = _checks(_vet(action_pr))[("advisory", "astral-sh/setup-uv")]

    assert check.verdict == "BLOCK"
    assert "GHSA-listed" in check.detail


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        (["2.0.0"], "OK"),  # v10.2.0 is not before the only limit
        (["2.0.0", "11.0.0"], "BLOCK"),  # ... but is before one of several
        (["*"], "BLOCK"),  # `*` is an infinite limit
    ],
)
def test_osv_limit_events_bound_the_affected_range(action_pr: dict[str, Any], limits: list[str], expected: str) -> None:
    events = [{"introduced": "1.0.0"}] + [{"limit": limit} for limit in limits]
    action_pr["osv:GitHub Actions/astral-sh/setup-uv@*"] = {
        "vulns": [
            {
                "id": "GHSA-limited",
                "affected": [
                    {
                        "package": {"ecosystem": "GitHub Actions", "name": "astral-sh/setup-uv"},
                        "ranges": [{"type": "ECOSYSTEM", "events": events}],
                    }
                ],
            }
        ]
    }

    assert _checks(_vet(action_pr))[("advisory", "astral-sh/setup-uv")].verdict == expected


def _setup_uv_advisory(responses: dict[str, Any], events: list[dict[str, str]], key: str = "*") -> None:
    responses[f"osv:GitHub Actions/astral-sh/setup-uv@{key}"] = {
        "vulns": [
            {
                "id": "GHSA-ranged",
                "affected": [
                    {
                        "package": {"ecosystem": "GitHub Actions", "name": "astral-sh/setup-uv"},
                        "ranges": [{"type": "ECOSYSTEM", "events": events}],
                    }
                ],
            }
        ]
    }


def test_unsorted_osv_events_are_sorted_before_evaluation(action_pr: dict[str, Any]) -> None:
    # Valid but unsorted intervals [10, 11) and [1, 2): v10.2.0 sits in the first one.
    events = [{"introduced": "10.0.0"}, {"fixed": "11.0.0"}, {"introduced": "1.0.0"}, {"fixed": "2.0.0"}]
    _setup_uv_advisory(action_pr, events)

    assert _checks(_vet(action_pr))[("advisory", "astral-sh/setup-uv")].verdict == "BLOCK"


def test_osv_pagination_is_followed_before_reporting_no_advisory(action_pr: dict[str, Any]) -> None:
    # OSV may answer with nothing but a page token; the match can sit on a later page.
    action_pr["osv:GitHub Actions/astral-sh/setup-uv@*"] = {"next_page_token": "page-2"}
    _setup_uv_advisory(action_pr, [{"introduced": "0"}, {"fixed": "11.0.0"}], key="*#page-2")

    assert _checks(_vet(action_pr))[("advisory", "astral-sh/setup-uv")].verdict == "BLOCK"


def test_endless_osv_pagination_exits_two(monkeypatch: pytest.MonkeyPatch, action_pr: dict[str, Any]) -> None:
    fetch_json, fetch_text, _ = _fetchers(action_pr)
    monkeypatch.setattr(
        vet, "make_fetchers", lambda token: (fetch_json, fetch_text, lambda url, payload: {"next_page_token": "again"})
    )

    assert vet.main(["--pr", "748", "--repo", REPO]) == 2


def test_floating_major_tag_is_not_evaluated_as_dot_zero() -> None:
    # `# v10` names a moving tag; reading it as 10.0.0 would place it outside [10.1.0, 10.3.0).
    responses: dict[str, Any] = {}
    files = {
        ".github/workflows/ci.yml": (
            _workflow("astral-sh/setup-uv", SETUP_UV_OLD, "v9"),
            _workflow("astral-sh/setup-uv", SETUP_UV_NEW, "v10"),
        )
    }
    _pr(responses, head_ref="dependabot/github_actions/astral-sh/setup-uv-10", files=files)
    _tag(responses, "astral-sh/setup-uv", "v10", SETUP_UV_NEW)
    responses[f"{vet.GITHUB_API}/repos/astral-sh/setup-uv/git/commits/{SETUP_UV_NEW}"] = {
        "committer": {"date": "2026-09-01T00:00:00Z"}
    }
    responses[f"{vet.GITHUB_API}/repos/astral-sh/setup-uv/releases?per_page=100&page=1"] = []
    _setup_uv_advisory(responses, [{"introduced": "10.1.0"}, {"fixed": "10.3.0"}])

    check = _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")]

    assert check.verdict == "WARN"
    assert "GHSA-ranged" in check.detail


@pytest.mark.parametrize(
    ("comment", "advisory", "expected"),
    [("v10", False, "OK"), ("v10.2.1", True, "BLOCK")],
)
def test_floating_tag_without_advisory_and_exact_tag_with_one(comment: str, advisory: bool, expected: str) -> None:
    responses: dict[str, Any] = {}
    files = {".github/workflows/ci.yml": (None, _workflow("astral-sh/setup-uv", SETUP_UV_NEW, comment))}
    _pr(responses, head_ref="dependabot/github_actions/astral-sh/setup-uv", files=files)
    _tag(responses, "astral-sh/setup-uv", comment, SETUP_UV_NEW)
    responses[f"{vet.GITHUB_API}/repos/astral-sh/setup-uv/git/commits/{SETUP_UV_NEW}"] = {
        "committer": {"date": "2026-09-01T00:00:00Z"}
    }
    responses[f"{vet.GITHUB_API}/repos/astral-sh/setup-uv/releases?per_page=100&page=1"] = []
    if advisory:
        _setup_uv_advisory(responses, [{"introduced": "10.1.0"}, {"fixed": "10.3.0"}])

    assert _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")].verdict == expected


def test_action_names_are_canonicalized_before_querying_osv(action_pr: dict[str, Any]) -> None:
    # OSV package names are case-sensitive; GitHub accepts any casing in `uses:`.
    for name in ("test", "watchdog"):
        path = f".github/workflows/{name}.yml"
        action_pr[f"{API}/contents/{path}?ref={HEAD_SHA}"] = _workflow("Astral-SH/Setup-UV", SETUP_UV_NEW, "v10.2.0")
    _tag(action_pr, "Astral-SH/Setup-UV", "v10.2.0", SETUP_UV_NEW)
    _releases(action_pr, "Astral-SH/Setup-UV", {"v10.2.0": datetime(2026, 9, 21, 13, 15, tzinfo=UTC)})
    action_pr[f"{vet.GITHUB_API}/repos/Astral-SH/Setup-UV"] = {"full_name": "astral-sh/setup-uv"}
    _setup_uv_advisory(action_pr, [{"introduced": "0"}, {"fixed": "11.0.0"}])

    assert _checks(_vet(action_pr))[("advisory", "Astral-SH/Setup-UV")].verdict == "BLOCK"


def test_releases_beyond_the_first_page_are_considered(action_pr: dict[str, Any]) -> None:
    repo = f"{vet.GITHUB_API}/repos/astral-sh/setup-uv"
    action_pr[f"{repo}/releases?per_page=100&page=1"] = [
        {"tag_name": f"v1.0.{index}", "published_at": "2026-01-01T00:00:00Z", "draft": False, "prerelease": False}
        for index in range(100)
    ]
    action_pr[f"{repo}/releases?per_page=100&page=2"] = [
        {"tag_name": "v10.2.1", "published_at": "2026-09-23T00:00:00Z", "draft": False, "prerelease": False}
    ]

    check = _checks(_vet(action_pr))[("superseded", "astral-sh/setup-uv")]

    assert check.verdict == "WARN"
    assert "10.2.1" in check.detail


@pytest.mark.parametrize("range_type", ["GIT", "SEMVER"])
def test_github_action_advisory_with_unevaluable_range_is_a_warn(action_pr: dict[str, Any], range_type: str) -> None:
    # "Could not evaluate" must not read as "not affected": the rule leaves no fail-open path.
    action_pr["osv:GitHub Actions/astral-sh/setup-uv@*"] = {
        "vulns": [
            {
                "id": "GHSA-xxxx-git",
                "affected": [
                    {
                        "package": {"ecosystem": "GitHub Actions", "name": "astral-sh/setup-uv"},
                        "ranges": [{"type": range_type, "events": [{"introduced": "abc"}]}],
                    }
                ],
            }
        ]
    }

    check = _checks(_vet(action_pr))[("advisory", "astral-sh/setup-uv")]

    assert check.verdict == "WARN"
    assert "GHSA-xxxx-git" in check.detail


def _single_action_pr(action: str, sha: str, comment: str) -> dict[str, Any]:
    """A PR adding one `uses:` line; the tag resolves and the repository publishes no releases."""
    responses: dict[str, Any] = {}
    files = {".github/workflows/ci.yml": (None, _workflow(action, sha, comment))}
    _pr(responses, head_ref=f"dependabot/github_actions/{action}", files=files)
    if comment:
        _tag(responses, action, comment, sha)
    responses[f"{vet.GITHUB_API}/repos/{action}/git/commits/{sha}"] = {"committer": {"date": "2026-09-01T00:00:00Z"}}
    responses[f"{vet.GITHUB_API}/repos/{action}/releases?per_page=100&page=1"] = []
    return responses


def _action_advisory(
    responses: dict[str, Any], name: str, affected: dict[str, Any], *, vuln_id: str = "GHSA-test", **extra: Any
) -> None:
    responses[f"osv:GitHub Actions/{name}@*"] = {
        "vulns": [
            {
                "id": vuln_id,
                "affected": [{"package": {"ecosystem": "GitHub Actions", "name": name}, **affected}],
                **extra,
            }
        ]
    }


def test_floating_tag_with_advisory_listing_a_version_under_its_prefix_is_a_warn() -> None:
    # `# v7` stands for some 7.x release; an advisory listing 7.0.1 may cover the pinned SHA.
    responses = _single_action_pr("actions/github-script", SETUP_UV_NEW, "v7")
    _action_advisory(responses, "actions/github-script", {"versions": ["7.0.1"]})

    check = _checks(_vet(responses))[("advisory", "actions/github-script")]

    assert check.verdict == "WARN"
    assert "GHSA-test" in check.detail
    assert "pin の SHA に対応する release" in check.detail


@pytest.mark.parametrize(
    ("comment", "affected", "expected"),
    [
        ("v7", {"versions": ["6.4.1", "8.0.0"]}, "OK"),
        ("v7.1", {"versions": ["7.1.3"]}, "WARN"),
        ("v7.1", {"versions": ["7.2.0"]}, "OK"),
        ("v7", {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "8.0.0"}, {"fixed": "8.1.0"}]}]}, "OK"),
        ("v7", {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "7.0.0"}]}]}, "OK"),
        # `last_affected` closes the interval: 7.0.0 itself is affected.
        (
            "v7",
            {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"last_affected": "7.0.0"}]}]},
            "WARN",
        ),
        ("v7.1", {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "7.1.5"}]}]}, "WARN"),
        ("v7.1", {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "7.0.0"}, {"limit": "7.1.0"}]}]}, "OK"),
    ],
)
def test_floating_tag_warns_only_when_the_advisory_can_reach_its_prefix(
    comment: str, affected: dict[str, Any], expected: str
) -> None:
    responses = _single_action_pr("actions/github-script", SETUP_UV_NEW, comment)
    _action_advisory(responses, "actions/github-script", affected)

    assert _checks(_vet(responses))[("advisory", "actions/github-script")].verdict == expected


def test_transferred_repository_advisory_is_matched_under_its_canonical_name() -> None:
    # Workflows keep the old name working through GitHub's redirect; OSV files it under the new one.
    responses = _single_action_pr("old-owner/old-action", SETUP_UV_NEW, "v2.3.4")
    responses[f"{vet.GITHUB_API}/repos/old-owner/old-action"] = {"full_name": "new-owner/new-action"}
    _action_advisory(responses, "new-owner/new-action", {"versions": ["2.3.4"]}, vuln_id="GHSA-moved")

    check = _checks(_vet(responses))[("advisory", "old-owner/old-action")]

    assert check.verdict == "BLOCK"
    assert "GHSA-moved" in check.detail


def test_transferred_repository_is_queried_under_both_names() -> None:
    responses = _single_action_pr("old-owner/old-action", SETUP_UV_NEW, "v2.3.4")
    responses[f"{vet.GITHUB_API}/repos/old-owner/old-action"] = {"full_name": "new-owner/new-action"}
    _action_advisory(responses, "old-owner/old-action", {"versions": ["2.3.4"]}, vuln_id="GHSA-old-name")

    assert _checks(_vet(responses))[("advisory", "old-owner/old-action")].verdict == "BLOCK"


def test_wildcard_package_advisory_applies_to_every_action() -> None:
    # OSV's `*` package name means every package in the ecosystem.
    responses = _single_action_pr("astral-sh/setup-uv", SETUP_UV_NEW, "v10.2.0")
    responses["osv:GitHub Actions/astral-sh/setup-uv@*"] = {
        "vulns": [
            {
                "id": "GHSA-everyone",
                "affected": [
                    {
                        "package": {"ecosystem": "GitHub Actions", "name": "*"},
                        "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}],
                    }
                ],
            }
        ]
    }

    assert _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")].verdict == "BLOCK"


@pytest.mark.parametrize("comment", ["v0.0.0-alpha", "v1.0.0-1"])
def test_semver_prerelease_or_build_pin_is_not_ordered_with_pep_440(comment: str) -> None:
    # PEP 440 reads `1.0.0-1` as the post-release 1.0.0.post1, after 1.0.0; SemVer puts it before.
    # Neither order is trusted for an Action, so a range that may cover the pin is a WARN, never OK.
    responses = _single_action_pr("astral-sh/setup-uv", SETUP_UV_NEW, comment)
    _action_advisory(
        responses,
        "astral-sh/setup-uv",
        {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0.0"}]}]},
    )

    check = _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")]

    assert check.verdict == "WARN"
    assert "GHSA-test" in check.detail


@pytest.mark.parametrize(
    ("closing", "comment", "expected"),
    [
        ({"last_affected": "0"}, "v0.0.0", "BLOCK"),  # 0 itself is the last affected version
        ({"last_affected": "0"}, "v0.0.1", "OK"),
        ({"fixed": "0"}, "v0.0.0", "OK"),  # fixed at 0 leaves nothing affected
        ({"fixed": "1.0.0"}, "v0.9.0", "BLOCK"),
    ],
)
def test_zero_is_a_sentinel_only_for_introduced(closing: dict[str, str], comment: str, expected: str) -> None:
    # OSV gives `"0"` its "before every version" meaning only in `introduced`; elsewhere it is literal 0.
    responses = _single_action_pr("astral-sh/setup-uv", SETUP_UV_NEW, comment)
    _action_advisory(
        responses, "astral-sh/setup-uv", {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, closing]}]}
    )

    assert _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")].verdict == expected


def test_semver_prerelease_range_bound_is_unevaluable() -> None:
    # `fixed: 2.0.0-1` precedes 2.0.0 in SemVer but follows it in PEP 440 (a false BLOCK there).
    responses = _single_action_pr("astral-sh/setup-uv", SETUP_UV_NEW, "v2.0.0")
    _action_advisory(
        responses,
        "astral-sh/setup-uv",
        {"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.0.0-1"}]}]},
    )

    assert _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")].verdict == "WARN"


def test_sha_pin_without_version_comment_and_any_advisory_is_a_warn() -> None:
    responses = _single_action_pr("astral-sh/setup-uv", SETUP_UV_NEW, "")
    _action_advisory(responses, "astral-sh/setup-uv", {"versions": ["1.0.0"]})

    check = _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")]

    assert check.verdict == "WARN"
    assert "GHSA-test" in check.detail


def test_sha_pin_without_version_comment_and_no_advisory_is_ok() -> None:
    responses = _single_action_pr("astral-sh/setup-uv", SETUP_UV_NEW, "")

    assert _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")].verdict == "OK"


def test_withdrawn_advisories_are_ignored(uv_pr: dict[str, Any]) -> None:
    uv_pr["osv:PyPI/ruff@0.16.8"] = {"vulns": [{"id": "GHSA-gone", "withdrawn": "2026-09-20T00:00:00Z"}]}
    responses = _single_action_pr("astral-sh/setup-uv", SETUP_UV_NEW, "v10.2.0")
    _action_advisory(responses, "astral-sh/setup-uv", {"versions": ["10.2.0"]}, withdrawn="2026-09-20T00:00:00Z")

    assert _checks(_vet(uv_pr))[("advisory", "ruff")].verdict == "OK"
    assert _checks(_vet(responses))[("advisory", "astral-sh/setup-uv")].verdict == "OK"


@pytest.mark.parametrize("comment", ["v7", ""])
def test_superseded_is_not_evaluated_without_an_exact_version(comment: str) -> None:
    # The pinned SHA may itself be 7.1.0; every 7.x release would otherwise count as its successor.
    responses = _single_action_pr("actions/github-script", SETUP_UV_NEW, comment)
    _releases(
        responses,
        "actions/github-script",
        {"v7.0.0": datetime(2026, 9, 2, tzinfo=UTC), "v7.1.0": datetime(2026, 9, 3, tzinfo=UTC)},
    )

    check = _checks(_vet(responses))[("superseded", "actions/github-script")]

    assert check.verdict == "OK"
    assert "評価不能" in check.detail


def test_release_dropping_the_python_floor_is_a_block(uv_pr: dict[str, Any]) -> None:
    _pypi(uv_pr, "ruff", {"0.16.8": datetime(2026, 9, 16, tzinfo=UTC)}, requires_python=">=3.12")

    check = _checks(_vet(uv_pr))[("python_range", "ruff")]

    assert check.verdict == "BLOCK"
    assert "3.11" in check.detail


def test_python_floor_is_the_tightest_lower_bound() -> None:
    # `>=3.10,>=3.11` admits nothing below 3.11, so a release needing 3.11 must not BLOCK.
    pyproject = '[project]\nrequires-python = ">=3.10,>=3.11"\n'

    assert str(vet.requires_python_floor(pyproject)) == "3.11"


def test_release_younger_than_cooldown_is_a_warn(uv_pr: dict[str, Any]) -> None:
    _pypi(uv_pr, "ruff", {"0.16.8": NOW - timedelta(days=2)})

    assert _checks(_vet(uv_pr))[("cooldown", "ruff")].verdict == "WARN"


def test_cooldown_counts_calendar_days_like_dependabot(action_pr: dict[str, Any]) -> None:
    # #745: published 2026-09-21T13:15Z, proposed 2026-09-28T00:07Z -- 6.45 days, 7 calendar days.
    assert _checks(_vet(action_pr))[("cooldown", "astral-sh/setup-uv")].verdict == "OK"

    action_pr[f"{vet.GITHUB_API}/repos/astral-sh/setup-uv/releases/tags/v10.2.0"] = {
        "published_at": "2026-09-22T00:00:00Z"
    }
    check = _checks(_vet(action_pr))[("cooldown", "astral-sh/setup-uv")]

    assert check.verdict == "WARN"
    assert "6 日" in check.detail


def test_release_superseded_within_seven_days_is_a_warn(uv_pr: dict[str, Any]) -> None:
    _pypi(
        uv_pr,
        "ruff",
        {"0.16.8": datetime(2026, 9, 16, tzinfo=UTC), "0.16.9": datetime(2026, 9, 19, tzinfo=UTC)},
    )

    check = _checks(_vet(uv_pr))[("superseded", "ruff")]

    assert check.verdict == "WARN"
    assert "0.16.9" in check.detail


def test_newer_line_published_before_the_candidate_does_not_supersede_it(uv_pr: dict[str, Any]) -> None:
    # A 0.16.x backport released after 0.17.0 is not superseded by the older-but-higher 0.17.0.
    _pypi(
        uv_pr,
        "ruff",
        {"0.17.0": datetime(2026, 9, 1, tzinfo=UTC), "0.16.8": datetime(2026, 9, 16, tzinfo=UTC)},
    )

    check = _checks(_vet(uv_pr))[("superseded", "ruff")]

    assert check.verdict == "OK"
    assert check.detail == "後続 release 無し"


def test_later_release_with_unknown_publication_time_is_a_warn(action_pr: dict[str, Any]) -> None:
    # Without the candidate's own publication time a later release cannot be ruled out.
    repo = f"{vet.GITHUB_API}/repos/astral-sh/setup-uv"
    del action_pr[f"{repo}/git/ref/tags/v10.2.0"]
    action_pr[f"{repo}/releases?per_page=100&page=1"] = [
        {"tag_name": "v10.2.1", "published_at": "2026-09-23T00:00:00Z", "draft": False, "prerelease": False}
    ]
    del action_pr[f"{repo}/releases/tags/v10.2.0"]

    check = _checks(_vet(action_pr))[("superseded", "astral-sh/setup-uv")]

    assert check.verdict == "WARN"
    assert "評価不能" in check.detail


def test_release_superseded_after_seven_days_is_ok(uv_pr: dict[str, Any]) -> None:
    check = _checks(_vet(uv_pr))[("superseded", "ruff")]

    # 0.16.9 followed 0.16.8 by 8.2 days, outside the window.
    assert check.verdict == "OK"
    assert "8.2 日後" in check.detail


def test_superseded_is_not_evaluated_for_repositories_without_releases() -> None:
    responses: dict[str, Any] = {}
    repo_url = "https://github.com/rbubley/mirrors-prettier"
    files = {".pre-commit-config.yaml": (_pre_commit(repo_url, "v3.8.5"), _pre_commit(repo_url, "v3.9.8"))}
    _pr(responses, head_ref="dependabot/pre_commit/https-/github.com/rbubley/mirrors-prettier-3.9.8", files=files)
    _tag(responses, "rbubley/mirrors-prettier", "v3.9.8", "a" * 40)
    responses[f"{vet.GITHUB_API}/repos/rbubley/mirrors-prettier/releases?per_page=100&page=1"] = []
    responses[f"{vet.GITHUB_API}/repos/rbubley/mirrors-prettier/git/commits/{'a' * 40}"] = {
        "committer": {"date": "2026-09-18T08:31:09Z"}
    }

    checks = _checks(_vet(responses))

    assert checks[("superseded", "rbubley/mirrors-prettier")].verdict == "OK"
    assert "評価不能" in checks[("superseded", "rbubley/mirrors-prettier")].detail
    # With neither a release nor an annotated tag, the commit date stands in for publication.
    assert "2026-09-18 → 2026-09-28 = 10 日" in checks[("cooldown", "rbubley/mirrors-prettier")].detail


def test_action_sha_matching_lightweight_tag_is_ok(action_pr: dict[str, Any]) -> None:
    check = _checks(_vet(action_pr))[("tag_sha", "astral-sh/setup-uv")]

    assert check.verdict == "OK"
    assert SETUP_UV_NEW in check.detail


def test_action_sha_matching_dereferenced_annotated_tag_is_ok() -> None:
    # #747: reviewdog/action-actionlint v1.76.0 is annotated and has no release object.
    responses: dict[str, Any] = {}
    files = {
        ".github/workflows/actionlint.yml": (
            _workflow("reviewdog/action-actionlint", "a" * 40, "v1.74.0"),
            _workflow("reviewdog/action-actionlint", ACTIONLINT_SHA, "v1.76.0"),
        )
    }
    _pr(responses, head_ref="dependabot/github_actions/reviewdog/action-actionlint-1.76.0", files=files)
    _tag(
        responses,
        "reviewdog/action-actionlint",
        "v1.76.0",
        ACTIONLINT_SHA,
        annotated_at=datetime(2026, 9, 18, tzinfo=UTC),
    )
    responses[f"{vet.GITHUB_API}/repos/reviewdog/action-actionlint/releases?per_page=100&page=1"] = [
        {"tag_name": "v1.76.1", "published_at": "2026-09-22T01:48:00Z", "draft": False, "prerelease": False},
        {"tag_name": "v1.77.0-rc1", "published_at": "2026-09-23T00:00:00Z", "draft": False, "prerelease": True},
    ]

    verdict = _vet(responses)
    checks = _checks(verdict)

    assert checks[("tag_sha", "reviewdog/action-actionlint")].verdict == "OK"
    assert checks[("cooldown", "reviewdog/action-actionlint")].detail.startswith("2026-09-18 → 2026-09-28 = 10 日")
    assert checks[("superseded", "reviewdog/action-actionlint")].verdict == "WARN"
    assert vet.verdict_line(verdict) == "判定: WARN (1 件)"


def test_sha_pin_without_version_comment_is_still_vetted(action_pr: dict[str, Any]) -> None:
    path = ".github/workflows/test.yml"
    action_pr[f"{API}/contents/{path}?ref={HEAD_SHA}"] = _workflow("astral-sh/setup-uv", SETUP_UV_NEW, "")

    verdict = _vet(action_pr)
    bare = [bump for bump in verdict.bumps if bump.new == SETUP_UV_NEW[:12]]

    assert bare == [vet.Bump("astral-sh/setup-uv", "v10.1.0", SETUP_UV_NEW[:12], "action", sha=SETUP_UV_NEW)]
    tag_sha = [check for check in verdict.checks if check.check_id == "tag_sha"]
    assert sorted(check.verdict for check in tag_sha) == ["OK", "WARN"]


def test_pr_without_detected_bumps_is_a_warn(action_pr: dict[str, Any]) -> None:
    # A tag-only `uses:` (no SHA pin) is invisible to the parser: say so instead of reporting OK.
    for name in ("test", "watchdog"):
        path = f".github/workflows/{name}.yml"
        action_pr[f"{API}/contents/{path}?ref={HEAD_SHA}"] = (
            "jobs:\n  t:\n    steps:\n      - uses: astral-sh/setup-uv@v10\n"
        )

    verdict = _vet(action_pr)

    assert verdict.bumps == []
    assert verdict.verdict == "WARN"
    assert "bump を検出できなかった" in _checks(verdict)[("pr_hygiene", "-")].detail


def test_distinct_sha_pins_for_the_same_version_are_each_checked(action_pr: dict[str, Any]) -> None:
    wrong = "9" * 40
    path = ".github/workflows/watchdog.yml"
    action_pr[f"{API}/contents/{path}?ref={HEAD_SHA}"] = _workflow("astral-sh/setup-uv", wrong, "v10.2.0")

    verdict = _vet(action_pr)
    tag_sha = sorted(check.verdict for check in verdict.checks if check.check_id == "tag_sha")

    assert tag_sha == ["BLOCK", "OK"]
    assert verdict.verdict == "BLOCK"


def test_yaml_extension_workflows_are_parsed() -> None:
    responses: dict[str, Any] = {}
    files = {
        ".github/workflows/ci.yaml": (
            _workflow("astral-sh/setup-uv", SETUP_UV_OLD, "v10.1.0"),
            _workflow("astral-sh/setup-uv", SETUP_UV_NEW, "v10.2.0"),
        )
    }
    _pr(responses, head_ref="dependabot/github_actions/astral-sh/setup-uv-10.2.0", files=files)
    _tag(responses, "astral-sh/setup-uv", "v10.2.0", SETUP_UV_NEW)
    _releases(responses, "astral-sh/setup-uv", {"v10.2.0": datetime(2026, 9, 21, tzinfo=UTC)})

    verdict = _vet(responses)

    assert [bump.new for bump in verdict.bumps] == ["v10.2.0"]
    assert _checks(verdict)[("pr_hygiene", "-")].verdict == "OK"


def _metadata_check(verdict: vet.PullRequestVerdict, action: str) -> vet.CheckResult:
    return _checks(verdict)[("action_metadata", action)]


def test_unchanged_action_metadata_is_ok_and_read_at_both_pinned_shas(action_pr: dict[str, Any]) -> None:
    fetched: list[str] = []
    fetch_json, fetch_text, post_json = _fetchers(action_pr)

    def recording_text(url: str) -> str:
        fetched.append(url)
        return fetch_text(url)

    verdict = vet.vet_pull_request(fetch_json, recording_text, post_json, REPO, 748)
    check = _metadata_check(verdict, "astral-sh/setup-uv")

    assert check.verdict == "OK"
    assert check.change == "v10.1.0 → v10.2.0"
    assert "node24" in check.detail
    # The commits the workflows pin, never the tags their comments claim (tags can move).
    assert _metadata_url("astral-sh/setup-uv", SETUP_UV_OLD) in fetched
    assert _metadata_url("astral-sh/setup-uv", SETUP_UV_NEW) in fetched
    assert not [url for url in fetched if "ref=v" in url]
    assert verdict.verdict == "OK"


@pytest.mark.parametrize(
    ("old", "new", "verdict", "expected"),
    [
        (_action_yml("node20"), _action_yml("node24"), "WARN", "runs.using node20 → node24"),
        (
            _action_yml(),
            _action_yml(inputs="  token:\n    description: t\n    required: true\n"),
            "WARN",
            "必須 input token を追加 (default なし",
        ),
        (
            _action_yml(),
            _action_yml(inputs="  token:\n    description: t\n    required: true\n    default: abc\n"),
            "WARN",
            "必須 input token を追加 (default あり",
        ),
        (
            _action_yml(inputs="  token:\n    description: t\n"),
            _action_yml(inputs="  token:\n    description: t\n    required: true\n"),
            "WARN",
            "input token が任意 → 必須 (default なし",
        ),
        (
            _action_yml(outputs="  cache-hit:\n    description: c\n  path:\n    description: p\n"),
            _action_yml(outputs="  path:\n    description: p\n"),
            "WARN",
            "output を削除: cache-hit",
        ),
        (
            _action_yml(),
            _action_yml(inputs="  verbose:\n    description: v\n    required: false\n"),
            "OK",
            "必須 input の追加",
        ),
    ],
    ids=[
        "runtime",
        "required-added",
        "required-added-with-default",
        "optional-to-required",
        "output-removed",
        "optional-added",
    ],
)
def test_action_metadata_contract_changes(
    action_pr: dict[str, Any], old: str, new: str, verdict: str, expected: str
) -> None:
    action_pr[_metadata_url("astral-sh/setup-uv", SETUP_UV_OLD)] = old
    action_pr[_metadata_url("astral-sh/setup-uv", SETUP_UV_NEW)] = new

    check = _metadata_check(_vet(action_pr), "astral-sh/setup-uv")

    assert check.verdict == verdict
    assert expected in check.detail


def test_action_yaml_extension_is_read_when_action_yml_is_absent(action_pr: dict[str, Any]) -> None:
    for sha in (SETUP_UV_OLD, SETUP_UV_NEW):
        action_pr[_metadata_url("astral-sh/setup-uv", sha, "action.yaml")] = action_pr.pop(
            _metadata_url("astral-sh/setup-uv", sha)
        )
    action_pr[_metadata_url("astral-sh/setup-uv", SETUP_UV_NEW, "action.yaml")] = _action_yml("node26")

    check = _metadata_check(_vet(action_pr), "astral-sh/setup-uv")

    assert check.verdict == "WARN"
    assert "runs.using node24 → node26" in check.detail
    assert check.links[-1].endswith(f"/blob/{SETUP_UV_NEW}/action.yaml")


def test_actions_under_subpaths_of_one_repository_are_compared_separately() -> None:
    responses: dict[str, Any] = {}
    old_sha, new_sha = "1" * 40, "2" * 40
    steps = "".join(f"      - uses: github/codeql-action/{name}@{{sha}} # {{tag}}\n" for name in ("init", "analyze"))
    workflow = "jobs:\n  scan:\n    steps:\n" + steps
    files = {
        ".github/workflows/codeql.yml": (
            workflow.format(sha=old_sha, tag="v4.1.0"),
            workflow.format(sha=new_sha, tag="v4.2.0"),
        )
    }
    responses[_metadata_url("github/codeql-action/init", old_sha)] = _action_yml("node20")
    responses[_metadata_url("github/codeql-action/init", new_sha)] = _action_yml("node24")
    _pr(responses, head_ref="dependabot/github_actions/github/codeql-action-4.2.0", files=files)
    _releases(responses, "github/codeql-action", {})

    verdict = _vet(responses)

    assert verdict.bumps == [vet.Bump("github/codeql-action", "v4.1.0", "v4.2.0", "action", sha=new_sha)]
    assert _metadata_check(verdict, "github/codeql-action/init").verdict == "WARN"
    assert _metadata_check(verdict, "github/codeql-action/analyze").verdict == "OK"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda r: r.pop(_metadata_url("astral-sh/setup-uv", SETUP_UV_NEW)), "action.yml / action.yaml が無い"),
        (lambda r: r.pop(_metadata_url("astral-sh/setup-uv", SETUP_UV_OLD)), "action.yml / action.yaml が無い"),
        (
            lambda r: r.__setitem__(_metadata_url("astral-sh/setup-uv", SETUP_UV_NEW), "runs: [unclosed"),
            "を解釈できない",
        ),
        (
            lambda r: r.__setitem__(_metadata_url("astral-sh/setup-uv", SETUP_UV_OLD), "name: no runs\n"),
            "を解釈できない",
        ),
        (
            lambda r: r.__setitem__(_metadata_url("astral-sh/setup-uv", SETUP_UV_NEW), _action_yml("'node24 | x'")),
            "を解釈できない",
        ),
    ],
    ids=["new-missing", "old-missing", "new-not-yaml", "old-without-runs", "untrusted-runtime"],
)
def test_unreadable_action_metadata_is_an_explicit_warn(
    action_pr: dict[str, Any], mutate: Callable[[dict[str, Any]], Any], expected: str
) -> None:
    mutate(action_pr)

    check = _metadata_check(_vet(action_pr), "astral-sh/setup-uv")

    assert check.verdict == "WARN"
    assert expected in check.detail


def test_newly_added_action_without_an_old_pin_is_not_reported_as_compatible() -> None:
    responses: dict[str, Any] = {}
    files = {".github/workflows/test.yml": (None, _workflow("astral-sh/setup-uv", SETUP_UV_NEW, "v10.2.0"))}
    _pr(responses, head_ref="dependabot/github_actions/astral-sh/setup-uv-10.2.0", files=files)
    _releases(responses, "astral-sh/setup-uv", {})

    check = _metadata_check(_vet(responses), "astral-sh/setup-uv")

    assert check.verdict == "WARN"
    assert check.change == "(新規) → v10.2.0"
    assert "旧 pin が無いため比較不能" in check.detail


def test_reusable_workflow_and_unsafe_subpaths_are_not_compared() -> None:
    responses: dict[str, Any] = {}
    reusable = "org/shared/.github/workflows/ci.yml"
    traversal = "org/shared/../evil"
    before = "".join(_workflow(name, "1" * 40, "v1.0.0") for name in (reusable, traversal))
    after = "".join(_workflow(name, "2" * 40, "v1.1.0") for name in (reusable, traversal))
    _pr(
        responses,
        head_ref="dependabot/github_actions/org/shared-1.1.0",
        files={".github/workflows/x.yml": (before, after)},
    )
    _releases(responses, "org/shared", {})

    checks = _checks(_vet(responses))

    assert checks[("action_metadata", reusable)].verdict == "WARN"
    assert "再利用ワークフロー" in checks[("action_metadata", reusable)].detail
    assert checks[("action_metadata", traversal)].verdict == "WARN"
    assert "サブパスを解釈できない" in checks[("action_metadata", traversal)].detail


def test_untrusted_input_names_are_not_echoed_into_the_report(action_pr: dict[str, Any]) -> None:
    hostile = "  '[click](https://evil.example)':\n    description: x\n    required: true\n"
    action_pr[_metadata_url("astral-sh/setup-uv", SETUP_UV_NEW)] = _action_yml(inputs=hostile)

    check = _metadata_check(_vet(action_pr), "astral-sh/setup-uv")

    assert check.verdict == "WARN"
    assert "evil.example" not in check.detail
    assert "表示できない名前" in check.detail


def test_action_metadata_server_error_exits_two(monkeypatch: pytest.MonkeyPatch, action_pr: dict[str, Any]) -> None:
    fetch_json, fetch_text, post_json = _fetchers(action_pr)

    def failing_text(url: str) -> str:
        if "/contents/action.yml" in url:
            response = requests.Response()
            response.status_code = 502
            raise requests.HTTPError(response=response)
        return fetch_text(url)

    monkeypatch.setattr(vet, "make_fetchers", lambda token: (fetch_json, failing_text, post_json))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    assert vet.main(["--repo", REPO, "--pr", "748"]) == 2


def test_action_sha_not_matching_tag_is_a_block(action_pr: dict[str, Any]) -> None:
    _tag(action_pr, "astral-sh/setup-uv", "v10.2.0", "9" * 40)

    check = _checks(_vet(action_pr))[("tag_sha", "astral-sh/setup-uv")]

    assert check.verdict == "BLOCK"
    assert "不一致" in check.detail


def test_action_tag_missing_is_a_block(action_pr: dict[str, Any]) -> None:
    del action_pr[f"{vet.GITHUB_API}/repos/astral-sh/setup-uv/git/ref/tags/v10.2.0"]

    check = _checks(_vet(action_pr))[("tag_sha", "astral-sh/setup-uv")]

    assert check.verdict == "BLOCK"
    assert "404" in check.detail


@pytest.mark.parametrize(("comment", "expected"), [("frozen: v1.7.12", "OK"), ("", "WARN")])
def test_sha_pinned_pre_commit_rev_is_checked_against_its_version_comment(comment: str, expected: str) -> None:
    responses: dict[str, Any] = {}
    repo_url = "https://github.com/rhysd/actionlint"
    files = {
        ".pre-commit-config.yaml": (
            _pre_commit(repo_url, "a" * 40, "frozen: v1.7.11"),
            _pre_commit(repo_url, ACTIONLINT_SHA, comment),
        )
    }
    _pr(responses, head_ref="dependabot/pre_commit/https-/github.com/rhysd/actionlint-1.7.12", files=files)
    _tag(responses, "rhysd/actionlint", "v1.7.12", ACTIONLINT_SHA)
    _releases(responses, "rhysd/actionlint", {"v1.7.12": datetime(2026, 9, 1, tzinfo=UTC)})

    checks = _checks(_vet(responses))

    # Without a version comment there is no tag to compare the SHA against: ask a human.
    assert checks[("tag_sha", "rhysd/actionlint")].verdict == expected
    assert ("tag_exists", "rhysd/actionlint") not in checks


def test_pre_commit_repo_outside_github_is_a_warn() -> None:
    responses: dict[str, Any] = {}
    repo_url = "https://gitlab.com/pycqa/flake8"
    files = {".pre-commit-config.yaml": (_pre_commit(repo_url, "7.0.0"), _pre_commit(repo_url, "7.1.0"))}
    _pr(responses, head_ref="dependabot/pre_commit/https-/gitlab.com/pycqa/flake8-7.1.0", files=files)

    checks = _checks(_vet(responses))

    assert checks[("tag_exists", repo_url)].verdict == "WARN"
    assert checks[("cooldown", repo_url)].verdict == "WARN"
    assert checks[("superseded", repo_url)].verdict == "OK"


def test_release_without_requires_python_is_ok(uv_pr: dict[str, Any]) -> None:
    _pypi(uv_pr, "ruff", {"0.16.8": datetime(2026, 9, 16, tzinfo=UTC)}, requires_python="")

    check = _checks(_vet(uv_pr))[("python_range", "ruff")]

    assert check.verdict == "OK"
    assert "宣言なし" in check.detail


def test_missing_pre_commit_tag_is_a_block() -> None:
    responses: dict[str, Any] = {}
    repo_url = "https://github.com/pre-commit/pre-commit-hooks"
    files = {".pre-commit-config.yaml": (_pre_commit(repo_url, "v6.0.0"), _pre_commit(repo_url, "v6.0.1"))}
    _pr(responses, head_ref="dependabot/pre_commit/https-/github.com/pre-commit/pre-commit-hooks-6.0.1", files=files)
    responses[f"{vet.GITHUB_API}/repos/pre-commit/pre-commit-hooks/releases?per_page=100&page=1"] = []

    verdict = _vet(responses)
    checks = _checks(verdict)

    assert checks[("tag_exists", "pre-commit/pre-commit-hooks")].verdict == "BLOCK"
    # The missing tag also hides the publication time; that is a question, not a second BLOCK.
    assert checks[("cooldown", "pre-commit/pre-commit-hooks")].verdict == "WARN"
    assert verdict.verdict == "BLOCK"


def test_major_bump_is_a_warn(uv_pr: dict[str, Any]) -> None:
    uv_pr[f"{API}/contents/uv.lock?ref={HEAD_SHA}"] = _uv_lock(ruff="1.0.0", requests="2.34.2")
    _pypi(uv_pr, "ruff", {"1.0.0": datetime(2026, 9, 1, tzinfo=UTC)})

    check = _checks(_vet(uv_pr))[("major_bump", "ruff")]

    assert check.verdict == "WARN"
    assert check.detail == "major 0 → 1"


# --- per-PR checks -------------------------------------------------------------------------


def test_pr_controlled_strings_cannot_break_the_report_table() -> None:
    responses: dict[str, Any] = {}
    repo_url = "https://gitlab.com/evil/hooks"
    rev = "v2|<img src=x>[x](https://evil.example)`"
    files = {".pre-commit-config.yaml": (_pre_commit(repo_url, "v1"), _pre_commit(repo_url, f"'{rev}'"))}
    _pr(responses, head_ref="dependabot/pre_commit/https-/gitlab.com/evil/hooks-2", files=files)

    report = vet.render_pull_request(_vet(responses))
    rows = [line for line in report.splitlines() if line.startswith("| ") and "evil" in line]

    assert rows
    for row in rows:
        # Five cells means six unescaped separators, whatever the PR wrote.
        assert len(re.findall(r"(?<!\\)\|", row)) == 6
        assert "[x](" not in row
        assert not re.search(r"(?<!\\)<img", row)


def test_each_row_shows_the_bump_it_was_computed_for(action_pr: dict[str, Any]) -> None:
    # Two bumps of one action: rows must not borrow the other bump's versions.
    path = ".github/workflows/watchdog.yml"
    action_pr[f"{API}/contents/{path}?ref={MERGE_BASE}"] = _workflow("astral-sh/setup-uv", "8" * 40, "v9.0.0")
    action_pr[f"{API}/contents/{path}?ref={HEAD_SHA}"] = _workflow("astral-sh/setup-uv", "9" * 40, "v9.1.0")
    _tag(action_pr, "astral-sh/setup-uv", "v9.1.0", "9" * 40)
    action_pr[f"{vet.GITHUB_API}/repos/astral-sh/setup-uv/releases/tags/v9.1.0"] = {
        "published_at": "2026-09-01T00:00:00Z"
    }

    verdict = _vet(action_pr)
    rows = {
        (cells[2], cells[1])
        for line in vet.render_pull_request(verdict).splitlines()
        if line.startswith("| astral-sh") and (cells := line.split(" | "))
    }

    assert ("tag_sha", "v10.1.0 → v10.2.0") in rows
    assert ("tag_sha", "v10.1.0 → v9.1.0") in rows or ("tag_sha", "v9.0.0 → v9.1.0") in rows
    assert {check.change for check in verdict.checks if check.check_id == "tag_sha"} == {
        f"{bump.old} → {bump.new}" for bump in verdict.bumps
    }


def test_extra_files_and_human_commits_are_a_warn(action_pr: dict[str, Any]) -> None:
    # #745: the uv pin follows setup-uv, so a human stacks .tool-versions and CLAUDE.md.
    files = action_pr[f"{API}/pulls/748/files?per_page=100&page=1"]
    files += [{"filename": ".tool-versions"}, {"filename": "CLAUDE.md"}]
    action_pr[f"{API}/pulls/748/commits?per_page=100&page=1"] += [{"author": {"login": "human"}}, {"author": None}]

    verdict = _vet(action_pr)
    check = _checks(verdict)[("pr_hygiene", "-")]

    assert check.verdict == "WARN"
    assert ".tool-versions, CLAUDE.md" in check.detail
    assert "Dependabot 以外の commit 2 件" in check.detail
    assert vet.verdict_line(verdict) == "判定: WARN (1 件)"


def test_unknown_branch_prefix_is_a_warn_and_runs_every_parser(uv_pr: dict[str, Any]) -> None:
    uv_pr[f"{API}/pulls/748"]["head"]["ref"] = "dependabot/npm_and_yarn/ruff"

    verdict = _vet(uv_pr)

    assert verdict.bumps == [vet.Bump("ruff", "0.16.7", "0.16.8", "pypi")]
    assert "接頭辞" in _checks(verdict)[("pr_hygiene", "-")].detail


@pytest.mark.parametrize("state", ["failure", "in_progress"])
def test_non_green_check_runs_are_a_warn(uv_pr: dict[str, Any], state: str) -> None:
    _pr_runs(uv_pr, [("lint", "success"), ("test", state)])

    check = _checks(_vet(uv_pr))[("ci_green", "-")]

    assert check.verdict == "WARN"
    assert f"test: {state}" in check.detail


def test_no_check_runs_is_a_warn(uv_pr: dict[str, Any]) -> None:
    _pr_runs(uv_pr, [])

    assert _checks(_vet(uv_pr))[("ci_green", "-")].verdict == "WARN"


def test_skipped_and_neutral_check_runs_are_green(uv_pr: dict[str, Any]) -> None:
    _pr_runs(uv_pr, [("lint", "success"), ("claude-review", "skipped"), ("codeql", "neutral")])

    check = _checks(_vet(uv_pr))[("ci_green", "-")]

    assert check.verdict == "OK"
    assert check.detail == "neutral ×1 + skipped ×1 + success ×1"


def test_the_vetting_job_ignores_its_own_check_run(uv_pr: dict[str, Any]) -> None:
    _pr_runs(uv_pr, [("lint", "success"), (vet.SELF_CHECK_NAME, "in_progress")])

    assert _checks(_vet(uv_pr))[("ci_green", "-")].verdict == "OK"


def _pr_runs(responses: dict[str, Any], runs: list[tuple[str, str]]) -> None:
    responses[f"{API}/commits/{HEAD_SHA}/check-runs?per_page=100&page=1"] = {
        "check_runs": [
            {"name": name, "status": "in_progress" if state == "in_progress" else "completed", "conclusion": state}
            for name, state in runs
        ]
    }


def test_healthy_uv_pr_is_ok_on_every_check(uv_pr: dict[str, Any]) -> None:
    verdict = _vet(uv_pr)

    assert {check.check_id: check.verdict for check in verdict.checks} == dict.fromkeys(
        ["yanked", "advisory", "python_range", "cooldown", "superseded", "major_bump", "pr_hygiene", "ci_green"], "OK"
    )
    assert vet.verdict_line(verdict) == "判定: OK"


# --- CLI -----------------------------------------------------------------------------------


def test_non_dependabot_pr_exits_two(
    monkeypatch: pytest.MonkeyPatch, uv_pr: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    uv_pr[f"{API}/pulls/748"]["user"]["login"] = "kambarakun"

    assert _main(monkeypatch, uv_pr, "--pr", "748") == 2
    assert "Dependabot の PR ではない" in capsys.readouterr().err


def test_unexpected_exception_exits_two_not_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A comment-only `.pre-commit-config.yaml` loads as None; exit 1 would claim a BLOCK.
    responses: dict[str, Any] = {}
    files = {".pre-commit-config.yaml": ("# nothing yet\n", "# still nothing\n")}
    _pr(responses, head_ref="dependabot/pre_commit/hooks", files=files)

    assert _main(monkeypatch, responses, "--pr", "748") == 2
    assert "AttributeError" in capsys.readouterr().err


def test_network_failure_exits_two_not_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def broken(url: str) -> Any:
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(vet, "make_fetchers", lambda token: (broken, broken, lambda url, payload: broken(url)))
    report, report_json = tmp_path / "report.md", tmp_path / "report.json"

    status = vet.main(["--pr", "748", "--repo", REPO, "--report", str(report), "--json", str(report_json)])

    assert status == 2
    # The workflow's `[ -f report.md ]` guard relies on no report being written.
    assert not report.exists()
    assert not report_json.exists()


def test_broken_fixture_index_exits_two(tmp_path: Path) -> None:
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")

    assert vet.main(["--pr", "748", "--repo", REPO, "--fixture", str(tmp_path)]) == 2


def test_unwritable_report_exits_two_not_block(
    monkeypatch: pytest.MonkeyPatch, uv_pr: dict[str, Any], tmp_path: Path
) -> None:
    # A failed write must not surface as exit 1, which means BLOCK.
    assert _main(monkeypatch, uv_pr, "--pr", "748", "--report", str(tmp_path / "missing" / "report.md")) == 2


def test_failed_comment_post_exits_two(monkeypatch: pytest.MonkeyPatch, uv_pr: dict[str, Any]) -> None:
    def post(url: str, payload: dict[str, Any]) -> Any:
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(vet, "make_poster", lambda token: post)

    assert _main(monkeypatch, uv_pr, "--pr", "748", "--comment") == 2


def test_pypi_server_error_exits_two(monkeypatch: pytest.MonkeyPatch, uv_pr: dict[str, Any]) -> None:
    def fetch_json(url: str) -> Any:
        if "pypi.org" in url:
            response = requests.Response()
            response.status_code = 503
            raise requests.HTTPError(response=response)
        return _fetchers(uv_pr)[0](url)

    monkeypatch.setattr(vet, "make_fetchers", lambda token: (fetch_json, *_fetchers(uv_pr)[1:]))

    assert vet.main(["--pr", "748", "--repo", REPO]) == 2


def test_all_open_filters_dependabot_authors_and_aggregates_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    uv_pr: dict[str, Any],
    action_pr: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = dict(uv_pr)
    yanked_head, action_head = "5" * 40, "6" * 40
    _pr(
        responses,
        number=750,
        head_sha=yanked_head,
        head_ref="dependabot/uv/requests-2.34.3",
        files={"uv.lock": (_uv_lock(requests="2.34.2"), _uv_lock(requests="2.34.3"))},
    )
    _pypi(responses, "requests", {"2.34.3": datetime(2026, 9, 1, tzinfo=UTC)}, yanked=("2.34.3",))
    for key, value in action_pr.items():
        responses.setdefault(key.replace("/pulls/748", "/pulls/751").replace(HEAD_SHA, action_head), value)
    responses[f"{API}/pulls/751"] = {
        **action_pr[f"{API}/pulls/748"],
        "head": {**action_pr[f"{API}/pulls/748"]["head"], "sha": action_head},
    }
    responses[f"{API}/pulls?state=open&per_page=100&page=1"] = [
        {"number": 748, "user": {"login": vet.DEPENDABOT_AUTHOR}},
        {"number": 750, "user": {"login": vet.DEPENDABOT_AUTHOR}},
        {"number": 751, "user": {"login": vet.DEPENDABOT_AUTHOR}},
        {"number": 752, "user": {"login": "kambarakun"}},
    ]

    status = _main(monkeypatch, responses, "--all-open")
    out = capsys.readouterr().out

    assert status == 1
    assert [line for line in out.splitlines() if line.startswith("## PR")] == [
        "## PR #748 (uv)",
        "## PR #750 (uv)",
        "## PR #751 (github-actions)",
    ]
    assert "判定: BLOCK (1 件)" in out


def test_all_open_without_prs_exits_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    responses: dict[str, Any] = {f"{API}/pulls?state=open&per_page=100&page=1": []}

    assert _main(monkeypatch, responses, "--all-open") == 0
    assert "open な Dependabot PR はない" in capsys.readouterr().out


def test_json_mirrors_the_markdown_verdicts(
    monkeypatch: pytest.MonkeyPatch, action_pr: dict[str, Any], tmp_path: Path
) -> None:
    _releases(
        action_pr,
        "astral-sh/setup-uv",
        {"v10.2.0": datetime(2026, 9, 21, 13, 15, tzinfo=UTC), "v10.2.1": datetime(2026, 9, 23, tzinfo=UTC)},
    )
    report, report_json = tmp_path / "report.md", tmp_path / "report.json"

    status = _main(monkeypatch, action_pr, "--pr", "748", "--report", str(report), "--json", str(report_json))
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    rows = [line.split(" | ") for line in report.read_text(encoding="utf-8").splitlines() if line.startswith("| ")][2:]

    assert status == 0
    assert payload[0]["verdict"] == "WARN"
    assert payload[0]["bumps"] == [{"name": "astral-sh/setup-uv", "old": "v10.1.0", "new": "v10.2.0", "kind": "action"}]
    assert [(row[2], row[3]) for row in rows] == [(check["id"], check["verdict"]) for check in payload[0]["checks"]]
    assert rows[0][1] == "v10.1.0 → v10.2.0"


def test_comment_is_refused_inside_github_actions(monkeypatch: pytest.MonkeyPatch, uv_pr: dict[str, Any]) -> None:
    monkeypatch.setattr(vet, "make_fetchers", lambda token: _fetchers(uv_pr))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert vet.main(["--pr", "748", "--repo", REPO, "--comment"]) == 2


def test_comment_posts_each_pr_report_locally(monkeypatch: pytest.MonkeyPatch, uv_pr: dict[str, Any]) -> None:
    posted: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(vet, "make_poster", lambda token: lambda url, payload: posted.append((url, payload)))

    assert _main(monkeypatch, uv_pr, "--pr", "748", "--comment") == 0
    assert posted[0][0] == f"{API}/issues/748/comments"
    assert posted[0][1]["body"].startswith("## PR #748 (uv)")


def test_fixture_mode_replays_recorded_responses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def offline(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fixture mode must not touch the network")

    monkeypatch.setattr(requests, "get", offline)
    monkeypatch.setattr(requests, "post", offline)

    status = vet.main(
        ["--pr", "748", "--repo", "kambarakun/fetch-tokyo-idsc-github-actions", "--fixture", str(FIXTURE_DIR)]
    )
    out = capsys.readouterr().out

    assert status == 0
    assert "| ruff | 0.16.7 → 0.16.8 | yanked | OK |" in out
    assert out.rstrip().endswith("判定: OK")


def test_recording_writes_a_fixture_that_replays_identically(
    monkeypatch: pytest.MonkeyPatch, uv_pr: dict[str, Any], tmp_path: Path
) -> None:
    uv_pr[f"{API}/pulls/748"]["body"] = "untrusted text"
    uv_pr[f"{API}/pulls/748"]["title"] = "untrusted title"
    uv_pr[f"{API}/pulls/748/files?per_page=100&page=1"][0]["patch"] = "@@ -1 +1 @@"
    uv_pr[f"{API}/commits/{HEAD_SHA}/check-runs?per_page=100&page=1"]["check_runs"][0]["output"] = {"text": "log"}
    monkeypatch.setattr(vet, "make_fetchers", lambda token: _fetchers(uv_pr))
    fetch_json, fetch_text, post_json = vet.make_recording_fetchers(None, tmp_path)

    recorded = vet.vet_pull_request(fetch_json, fetch_text, post_json, REPO, 748)
    replayed = vet.vet_pull_request(*vet.make_fixture_fetchers(tmp_path), REPO, 748)
    stored = "".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())

    assert recorded.checks == replayed.checks
    assert "untrusted text" not in stored
    assert "untrusted title" not in stored
    assert "@@ -1 +1 @@" not in stored
    assert '"output"' not in stored
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    project = json.loads((tmp_path / index[f"GET {vet.PYPI_PROJECT.format(name='ruff')}"]).read_text(encoding="utf-8"))
    # Only releases above the candidate (0.16.8) can supersede it; 0.16.7 is history.
    assert sorted(project["releases"]) == ["0.16.10", "0.16.9"]


def test_recorded_fixture_stays_under_the_size_budget() -> None:
    """issue #765: 300 KB was the budget set in #762; uv.lock twice already takes 225 KB of it."""
    assert sum(path.stat().st_size for path in FIXTURE_DIR.iterdir()) < 300_000


def test_the_token_never_leaves_the_github_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, dict[str, str]] = {}

    class Response:
        ok = True

        def json(self) -> Any:
            return {}

    def record(url: str, headers: dict[str, str], **kwargs: Any) -> Response:
        seen[url] = headers
        return Response()

    monkeypatch.setattr(requests, "get", record)
    monkeypatch.setattr(requests, "post", record)
    fetch_json, _, post_json = vet.make_fetchers("secret-token")

    fetch_json(f"{API}/pulls/748")
    fetch_json(vet.PYPI_PROJECT.format(name="ruff"))
    post_json(vet.OSV_QUERY, {"package": {"name": "ruff", "ecosystem": "PyPI"}})

    assert seen[f"{API}/pulls/748"]["Authorization"] == "Bearer secret-token"
    assert "Authorization" not in seen[vet.PYPI_PROJECT.format(name="ruff")]
    assert "Authorization" not in seen[vet.OSV_QUERY]


def test_repository_is_resolved_from_the_environment_then_git_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "env/repo")
    assert vet.resolve_repo(None) == "env/repo"

    monkeypatch.delenv("GITHUB_REPOSITORY")
    assert vet.resolve_repo(None) == "kambarakun/fetch-tokyo-idsc-github-actions"
    assert vet.resolve_repo("given/repo") == "given/repo"
