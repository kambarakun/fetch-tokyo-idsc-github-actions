#!/usr/bin/env python3
"""Liveness check for the dependency update pipeline (issue #683).

The uv-ecosystem outage that started on 2026-07-27 (issue #681) was invisible for six
weekly cycles: Dependabot aborted before ``uv lock`` and opened no PR, and "no PR" is
indistinguishable from "nothing to update" when you only look at the repository. The
failure lived in job logs only the repository owner can read, and Dependabot Security
Updates run through the same updater, so CVE fixes were stalled too.

These checks turn that silence into a signal. Every input is a structured field --
timestamps, labels, version strings -- so PR and issue text never reaches the report
(AGENTS.md treats that text as untrusted input).

Exit codes: 0 healthy, 1 at least one alert, 2 the check itself could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import requests
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

PROJECT_ROOT = Path(__file__).resolve().parent.parent

GITHUB_API_HOST = "api.github.com"
GITHUB_API = f"https://{GITHUB_API_HOST}"
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
SETUP_UV_CHECKSUMS = "https://raw.githubusercontent.com/astral-sh/setup-uv/{sha}/src/download/checksum/{filename}"
DEPENDABOT_CORE_LATEST_RELEASE = f"{GITHUB_API}/repos/dependabot/dependabot-core/releases/latest"
DEPENDABOT_LOGIN = "dependabot[bot]"
# Read at a release tag rather than `main`: the hosted updater runs a shipped release, so an
# unreleased (or since reverted) commit on `main` is not what rewrites this repository's
# uv.lock, and comparing against it turns ordinary upstream churn into a 3c alert.
DEPENDABOT_UV_DOCKERFILE = "https://raw.githubusercontent.com/dependabot/dependabot-core/{ref}/uv/Dockerfile"

# setup-uv moved the table from a generated .ts module to plain JSON on 2026-08-19
# (astral-sh/setup-uv#1025). The pinned commit decides which one exists, so try both.
CHECKSUM_FILENAMES = ("known-checksums.json", "known-checksums.ts")
# ubuntu-latest runners, i.e. the platform whose checksum actually gates CI.
CHECKSUM_PLATFORM = "x86_64-unknown-linux-gnu"
CHECKSUM_KEY = re.compile(rf'"{CHECKSUM_PLATFORM}-(?P<version>\d+\.\d+\.\d+)"')
# Same shape astral-sh/setup-uv accepts for `version-file: .tool-versions`: a bare
# `uv <version>` line, no trailing comment (see tests/test_dependabot_config.py).
TOOL_VERSIONS_UV_LINE = re.compile(r"^\s*uv\s*v?\s*(?P<version>\S+)\s*$")
SETUP_UV_REF = re.compile(r"astral-sh/setup-uv@(?P<sha>[0-9a-f]{40})")
DEPENDABOT_UV_IMAGE = re.compile(r"ghcr\.io/astral-sh/uv:(?P<version>\d+\.\d+\.\d+)")

# Check 4 (issue #656). A pinned Action ships its own lockfile, and the github-actions
# ecosystem tracks only the Action's own version -- never the dependencies frozen inside it.
# A CVE in one of those is invisible to Dependabot and to every other check here.
ACTION_LOCKFILE = "https://raw.githubusercontent.com/{action}/{ref}/{lockfile}"
ACTION_RELEASES = GITHUB_API + "/repos/{action}/releases?per_page=100"
# The lookbehind keeps a different Action whose name merely ends in the watched one
# (`not-anthropics/claude-code-action`) from being counted as a second pin of it.
ACTION_PINNED_REF = "(?<![A-Za-z0-9._/-]){action}(?:/[A-Za-z0-9._/-]+)?@(?P<sha>[0-9a-f]{{40}})"
# `/releases/latest` cannot answer "what would we move to": anthropics/claude-code-action
# republishes a floating `v1` release, and that is the tag the endpoint returns. Take the
# highest tag of this shape instead, which is also the one Dependabot proposes.
ACTION_RELEASE_TAG = re.compile(r"v\d+\.\d+\.\d+")

# The ecosystem that proposes the Python dependencies check 2 looks at.
PYTHON_ECOSYSTEM = "uv"

# Interpreters `requires-python` is enumerated over, see declared_python_versions. CPython
# has never shipped a minor or patch anywhere near 40, so this brackets every version a
# Requires-Python can meaningfully bound.
INTERPRETER_GRID = [
    Version(f"{major}.{minor}.{patch}") for major in (2, 3, 4) for minor in range(40) for patch in range(40)
]

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Three missed weekly cycles. The 2026-07-27 outage would have tripped this on 2026-08-17,
# five weeks before a human noticed it.
DEFAULT_MAX_PR_AGE_DAYS = 21
# Steady state is 0-2: a release inside the cooldown window is not yet actionable and is
# excluded below, so anything above this means proposals are not landing.
DEFAULT_MAX_STALE_DIRECT = 3

Severity = str
FetchJson = Callable[[str], Any]
FetchText = Callable[[str], str]


@dataclass(frozen=True)
class CheckResult:
    """One verdict plus the structured facts that produced it."""

    check_id: str
    title: str
    severity: Severity
    ok: bool
    detail: str
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StaleDependency:
    """A direct dependency whose newer release is past the Dependabot cooldown."""

    name: str
    locked: str
    latest: str
    released_at: datetime


@dataclass(frozen=True)
class BundledDependency:
    """A dependency frozen inside an Action this repository pins, watched for one advisory."""

    action: str
    lockfile: str
    package: str
    fixed_in: Version
    advisory: str


# Every claude-code-action release so far locks shell-quote 1.8.4; GHSA-395f-4hp3-45gv
# (quadratic-time `parse()`) is fixed in 1.9.0. Issue #656 accepts that exposure -- no
# workflow here passes external input to `claude_args` -- so the check stays green while no
# fixed release exists, and turns red the week upstream ships one and the bump is possible.
WATCHED_ACTION_DEPENDENCIES = (
    BundledDependency(
        action="anthropics/claude-code-action",
        lockfile="bun.lock",
        package="shell-quote",
        fixed_in=Version("1.9.0"),
        advisory="GHSA-395f-4hp3-45gv",
    ),
)


def _http_get(url: str, token: str | None, accept: str) -> requests.Response:
    headers = {"Accept": accept, "User-Agent": "fetch-tokyo-idsc-dependency-watchdog"}
    # The workflow token carries `issues: write` and `pull-requests: read`. The same fetchers
    # also call pypi.org and raw.githubusercontent.com, so gate the credential on the host
    # rather than on the caller: a future check cannot leak it by picking the wrong fetcher.
    if token and urlsplit(url).hostname == GITHUB_API_HOST:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=30)
    if not response.ok:
        # issue #697: a bare status code sent two runs chasing the wrong cause. GitHub explains
        # itself in the body ("Resource not accessible by integration", rate limits, ...), so
        # carry the first part of it into the exception the workflow prints.
        detail = " ".join(response.text.split())[:200]
        raise requests.HTTPError(f"{response.status_code} {response.reason} for {url}: {detail}", response=response)
    return response


def make_fetchers(token: str | None) -> tuple[FetchJson, FetchText]:
    """Build the two network accessors; tests substitute them wholesale."""

    def fetch_json(url: str) -> Any:
        return _http_get(url, token, "application/vnd.github+json").json()

    def fetch_text(url: str) -> str:
        return _http_get(url, token, "text/plain").text

    return fetch_json, fetch_text


def dependabot_config(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / ".github" / "dependabot.yml").read_text(encoding="utf-8"))


def dependabot_ecosystems(config: dict[str, Any]) -> dict[str, str]:
    """Map each configured ecosystem to the label that identifies its PRs.

    `uv` PRs are labelled `python`, so the label is the only reliable join key between
    dependabot.yml and the PRs it produces.
    """
    ecosystems: dict[str, str] = {}
    for update in config["updates"]:
        labels = [label for label in update.get("labels", []) if label != "dependencies"]
        if labels:
            ecosystems[update["package-ecosystem"]] = labels[0]
    return ecosystems


def cooldown_days(config: dict[str, Any]) -> int:
    """Read the shared cooldown so the stale-dependency window never drifts from config."""
    return max(update.get("cooldown", {}).get("default-days", 0) for update in config["updates"])


def last_scheduled_update(config: dict[str, Any], ecosystem: str, now: datetime) -> datetime:
    """When the updater last had a chance to run, per dependabot.yml.

    The cooldown has to be judged as of that moment rather than now. This workflow runs on
    Wednesday while the updater is scheduled for Monday, so a release that was still inside
    the cooldown on Monday is older than the cooldown by Wednesday even though Dependabot has
    had no opportunity to propose it -- counting those would open a false outage issue every
    time a few releases land in that gap.
    """
    empty: dict[str, Any] = {}
    update = next((entry for entry in config["updates"] if entry["package-ecosystem"] == ecosystem), empty)
    schedule = update.get("schedule", {})
    # Only the weekly shape is modelled; any other interval keeps the conservative `now`.
    if schedule.get("interval") != "weekly":
        return now
    local = now.astimezone(ZoneInfo(schedule.get("timezone", "UTC")))
    hour, _, minute = str(schedule.get("time", "00:00")).partition(":")
    scheduled = local.replace(hour=int(hour), minute=int(minute or 0), second=0, microsecond=0)
    target_weekday = WEEKDAYS[str(schedule.get("day", "monday")).lower()]
    scheduled -= timedelta(days=(scheduled.weekday() - target_weekday) % 7)
    if scheduled > local:
        scheduled -= timedelta(days=7)
    return scheduled


def last_dependabot_pr(fetch_json: FetchJson, repo: str, label: str) -> datetime | None:
    """Creation time of the newest Dependabot PR carrying `label`, or None if there is none.

    The repository issues endpoint is used rather than the search API (issue #697): the workflow
    token is refused there, and refused in a way that hid the problem. Without
    `pull-requests: read` the search answered HTTP 200 with an empty list, so every ecosystem
    looked dead; with the permission it answers HTTP 403. This endpoint also costs the core rate
    limit (5,000/h) instead of the search limit (30/min), and does not depend on the migration
    of the legacy issue-search syntax.

    `GET /issues` lists pull requests alongside issues, so `pull_request` is what separates them;
    `pull-requests: read` is still required for the token to see the pull requests at all.

    One page is enough and there is no pagination: `creator` and `labels` already narrow the list
    to what Dependabot filed under this ecosystem, and Dependabot files pull requests rather than
    issues, so the newest entry is the answer. A full page of Dependabot-authored non-PR issues
    would be needed to hide a real pull request, and the page is the API maximum.
    """
    url = (
        f"{GITHUB_API}/repos/{repo}/issues"
        f"?labels={quote(label)}&state=all&creator={quote(DEPENDABOT_LOGIN)}"
        "&sort=created&direction=desc&per_page=100"
    )
    for item in fetch_json(url):
        if "pull_request" in item:
            return datetime.fromisoformat(item["created_at"])
    return None


def direct_requirements(root: Path) -> list[Requirement]:
    """Every dependency Dependabot can propose, i.e. the ones written in pyproject.toml.

    Deduplicated by canonical name: a package listed both in `dependencies` and in an extra
    (or shared by two extras) is still one Dependabot proposal, and counting it twice would
    let a single overdue package eat several slots of the backlog threshold.
    """
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    specs: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    unique: dict[str, Requirement] = {}
    for spec in specs:
        requirement = Requirement(spec)
        unique.setdefault(_canonical(requirement.name), requirement)
    return list(unique.values())


def locked_versions(root: Path) -> dict[str, str]:
    lock_text = (root / "uv.lock").read_text(encoding="utf-8")
    return dict(re.findall(r'name = "([^"]+)"\nversion = "([^"]+)"', lock_text))


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_python_versions(root: Path) -> list[Version]:
    """The interpreters pyproject.toml declares support for, as concrete versions.

    Enumerating the range instead of keeping the SpecifierSet leaves every later comparison
    to `packaging`: a Requires-Python may use wildcards and exclusions (`!=3.9.*`) whose
    semantics no hand-rolled bound arithmetic gets right, and `SpecifierSet.contains` rejects
    a wildcard outright when handed one as its argument. An empty list means the project
    declares no range, in which case there is nothing to compare against.
    """
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"].get("requires-python")
    if not declared:
        return []
    try:
        specifiers = SpecifierSet(declared)
    except InvalidSpecifier:  # pragma: no cover - defensive; uv validates this field
        return []
    return [version for version in INTERPRETER_GRID if specifiers.contains(version)]


def _runs_on(requires_python: str | None, declared: Sequence[Version]) -> bool:
    """Whether uv could lock a release on every interpreter the project declares.

    uv resolves for the whole `requires-python` range (`>=3.11,<3.12` here), not for the one
    the watchdog happens to run on, so a release that narrows that range anywhere is
    unresolvable for this project: raising the floor to a newer 3.11 patch, capping below the
    project's ceiling, or excluding versions outright. Neither Dependabot nor uv can propose
    such a release, and counting it would keep an unfixable entry in the backlog until it
    opens a false outage issue.
    """
    if not requires_python or not declared:
        return True
    try:
        candidate = SpecifierSet(requires_python)
    except InvalidSpecifier:  # pragma: no cover - defensive; PyPI validates this field
        return True
    return all(candidate.contains(version) for version in declared)


def newest_eligible_release(
    payload: dict[str, Any], current: Version, as_of: datetime, cooldown: int, python_versions: Sequence[Version]
) -> tuple[Version, datetime] | None:
    """The newest release Dependabot is overdue to propose, or None if there is none.

    Scanning the whole release history rather than `info.version` matters for frequently
    released packages: with 1.0 locked, an overdue 1.1 would be invisible as soon as 2.0
    (a major bump dependabot.yml ignores) or a same-day 1.2 (still inside the cooldown)
    took over `info.version`, and a stalled updater would go unnoticed exactly where the
    backlog is largest.
    """
    # PyPI has signalled it may drop `releases` from this endpoint. Treating a missing key as
    # "no eligible release" would report an empty backlog forever -- the silent failure this
    # watchdog exists to catch -- so fail loudly instead (exit 2 turns the job red).
    if "releases" not in payload:
        raise ValueError("PyPI response has no `releases` history; the backlog check cannot run")

    best: tuple[Version, datetime] | None = None
    for raw, files in payload["releases"].items():
        try:
            version = Version(raw)
        except InvalidVersion:  # pragma: no cover - defensive; PyPI versions are PEP 440
            continue
        # Major bumps are ignored by dependabot.yml, and pre-releases are never proposed.
        if version <= current or version.major != current.major or version.is_prerelease:
            continue
        uploads = [
            file["upload_time_iso_8601"]
            for file in files
            if not file.get("yanked")
            and file.get("upload_time_iso_8601")
            and _runs_on(file.get("requires_python"), python_versions)
        ]
        if not uploads:
            continue
        released_at = min(datetime.fromisoformat(upload) for upload in uploads)
        # Inside the cooldown the PR was not due yet, so this release is not evidence of a stall.
        if (as_of - released_at).days < cooldown:
            continue
        if best is None or version > best[0]:
            best = (version, released_at)
    return best


def stale_direct_dependencies(
    fetch_json: FetchJson,
    requirements: Iterable[Requirement],
    locked: dict[str, str],
    as_of: datetime,
    cooldown: int,
    python_versions: Sequence[Version],
) -> list[StaleDependency]:
    """Direct dependencies Dependabot should already have proposed but has not."""
    stale: list[StaleDependency] = []
    for requirement in requirements:
        current = locked.get(_canonical(requirement.name))
        if current is None:
            continue
        try:
            current_version = Version(current)
        except InvalidVersion:  # pragma: no cover - defensive; uv.lock holds PEP 440 versions
            continue
        payload = fetch_json(PYPI_JSON.format(name=requirement.name))
        eligible = newest_eligible_release(payload, current_version, as_of, cooldown, python_versions)
        if eligible is None:
            continue
        version, released_at = eligible
        stale.append(StaleDependency(requirement.name, current, str(version), released_at))
    return stale


def tool_versions_uv_pin(root: Path) -> Version:
    lines = (root / ".tool-versions").read_text(encoding="utf-8").splitlines()
    pins = [
        match["version"]
        for line in lines
        if not line.lstrip().startswith("#") and (match := TOOL_VERSIONS_UV_LINE.match(line))
    ]
    if len(pins) != 1:
        raise ValueError(f"expected exactly one uv pin in .tool-versions, found {len(pins)}")
    return Version(pins[0])


def setup_uv_pinned_sha(root: Path) -> str:
    """The single setup-uv commit every workflow pins (guarded by test_dependabot_config)."""
    refs = {
        match["sha"]
        for workflow in sorted((root / ".github" / "workflows").glob("*.yml"))
        for match in SETUP_UV_REF.finditer(workflow.read_text(encoding="utf-8"))
    }
    if len(refs) != 1:
        raise ValueError(f"expected exactly one pinned setup-uv commit, found {len(refs)}")
    return next(iter(refs))


def known_uv_checksums(fetch_text: FetchText, sha: str) -> set[Version]:
    """uv versions the pinned setup-uv can verify; anything else installs unverified."""
    for filename in CHECKSUM_FILENAMES:
        try:
            body = fetch_text(SETUP_UV_CHECKSUMS.format(sha=sha, filename=filename))
        except requests.HTTPError:
            continue
        versions = {Version(match["version"]) for match in CHECKSUM_KEY.finditer(body)}
        # A 200 that parses to nothing means the layout changed. Returning the empty set
        # would make 3a report an unverified binary that is actually fine, so keep looking
        # and let the caller fail loudly if no candidate yields anything.
        if versions:
            return versions
    raise ValueError(f"no known-checksums file found for setup-uv commit {sha}")


def dependabot_bundled_uv(fetch_json: FetchJson, fetch_text: FetchText) -> tuple[Version, str]:
    """The uv image dependabot-core ships, i.e. the binary that rewrites uv.lock.

    Returns the version and the release tag it was read from. No public source names the
    revision GitHub actually has deployed, so the newest release is the closest identifiable
    stand-in; naming it in the report is what lets a human judge the remaining lag.
    """
    ref = fetch_json(DEPENDABOT_CORE_LATEST_RELEASE)["tag_name"]
    match = DEPENDABOT_UV_IMAGE.search(fetch_text(DEPENDABOT_UV_DOCKERFILE.format(ref=ref)))
    if match is None:
        raise ValueError(f"could not read the uv version from dependabot-core {ref}'s uv/Dockerfile")
    return Version(match["version"]), ref


def action_pinned_sha(root: Path, action: str) -> str:
    """The single commit every workflow pins `action` to.

    Zero matches is an error rather than a pass: an Action that is no longer used has to be
    dropped from WATCHED_ACTION_DEPENDENCIES deliberately, not disappear from the report.
    """
    pattern = re.compile(ACTION_PINNED_REF.format(action=re.escape(action)))
    refs = {
        match["sha"]
        for workflow in sorted((root / ".github" / "workflows").glob("*.yml"))
        for match in pattern.finditer(workflow.read_text(encoding="utf-8"))
    }
    if len(refs) != 1:
        raise ValueError(f"expected exactly one pinned {action} commit, found {len(refs)}")
    return next(iter(refs))


def newest_action_release(fetch_json: FetchJson, action: str) -> str:
    """The highest semver release tag, i.e. the version Dependabot would propose next."""
    releases = fetch_json(ACTION_RELEASES.format(action=action))
    tags = [
        release["tag_name"]
        for release in releases
        if not release.get("draft")
        and not release.get("prerelease")
        and ACTION_RELEASE_TAG.fullmatch(release["tag_name"])
    ]
    if not tags:
        raise ValueError(f"no semver release tag found for {action}")
    return max(tags, key=Version)


def bundled_package_version(fetch_text: FetchText, watched: BundledDependency, ref: str) -> Version | None:
    """The lowest `package` version locked by `action` at `ref`, or None if it locks none.

    The `"name@version"` key is anchored on its opening quote so that a scoped sibling
    (`"@types/shell-quote@1.7.5"`) cannot be mistaken for the package itself, and the lowest
    of several copies is the one that decides exposure. A missing lockfile propagates as an
    error: a moved lockfile is the one way this check could report an unverified all-clear.
    """
    body = fetch_text(ACTION_LOCKFILE.format(action=watched.action, ref=ref, lockfile=watched.lockfile))
    pattern = re.compile(rf'"{re.escape(watched.package)}@(?P<version>[^"]+)"')
    return min((Version(match["version"]) for match in pattern.finditer(body)), default=None)


def check_pr_age(
    fetch_json: FetchJson, repo: str, ecosystems: dict[str, str], now: datetime, max_age_days: int
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for ecosystem, label in sorted(ecosystems.items()):
        created_at = last_dependabot_pr(fetch_json, repo, label)
        if created_at is None:
            results.append(
                CheckResult(
                    f"1:{ecosystem}",
                    f"{ecosystem} エコシステムの最終 Dependabot PR",
                    "high",
                    False,
                    f"`{label}` ラベルの Dependabot PR が 1 件も存在しない",
                    {"ecosystem": ecosystem, "label": label, "last_pr_at": None},
                )
            )
            continue
        age_days = (now - created_at).days
        results.append(
            CheckResult(
                f"1:{ecosystem}",
                f"{ecosystem} エコシステムの最終 Dependabot PR",
                "high",
                age_days <= max_age_days,
                f"最終 PR から {age_days} 日経過 (閾値 {max_age_days} 日 / {created_at.date().isoformat()})",
                {
                    "ecosystem": ecosystem,
                    "label": label,
                    "last_pr_at": created_at.isoformat(),
                    "age_days": age_days,
                    "threshold_days": max_age_days,
                },
            )
        )
    return results


def check_stale_dependencies(
    stale: Sequence[StaleDependency], cooldown: int, threshold: int, as_of: datetime
) -> CheckResult:
    listing = ", ".join(f"{item.name} {item.locked} -> {item.latest}" for item in stale) or "なし"
    return CheckResult(
        "2",
        "cooldown を過ぎた直接依存の滞留",
        "high",
        len(stale) <= threshold,
        f"{len(stale)} 件 (閾値 {threshold} 件 / cooldown {cooldown} 日 / "
        f"判定基準時刻 {as_of.isoformat(timespec='minutes')}): {listing}",
        {
            "count": len(stale),
            "threshold": threshold,
            "cooldown_days": cooldown,
            "as_of": as_of.isoformat(),
            "dependencies": [
                {
                    "name": item.name,
                    "locked": item.locked,
                    "latest": item.latest,
                    "released_at": item.released_at.isoformat(),
                }
                for item in stale
            ],
        },
    )


def check_uv_pin(
    pinned: Version, known: set[Version], bundled: Version, bundled_ref: str, setup_uv_sha: str
) -> list[CheckResult]:
    """3a/3b/3c from issue #683.

    The pin is deliberately behind upstream uv: setup-uv installs an unknown version
    *without verifying its checksum*, so the ceiling is what the pinned setup-uv knows,
    not what astral released (CLAUDE.md "uv 本体の更新経路").
    """
    highest = max(known, default=None)
    return [
        CheckResult(
            "3a",
            "uv pin が setup-uv の既知 checksum に含まれる",
            "high",
            pinned in known,
            f".tool-versions の uv {pinned} は setup-uv {setup_uv_sha[:7]} の既知 checksum に"
            + ("含まれる" if pinned in known else "**含まれない** (CI が未検証バイナリを導入している)"),
            {"pinned": str(pinned), "setup_uv_sha": setup_uv_sha, "known_count": len(known)},
        ),
        CheckResult(
            "3b",
            "検証つきで追随できる uv がある",
            "low",
            highest is None or pinned >= highest,
            f"既知 checksum の上限は {highest} / pin は {pinned}",
            {"pinned": str(pinned), "highest_known": str(highest) if highest else None},
        ),
        CheckResult(
            "3c",
            "Dependabot 同梱 uv との系列一致",
            "medium",
            (pinned.major, pinned.minor) == (bundled.major, bundled.minor),
            f"pin {pinned} / dependabot-core {bundled_ref} 同梱 {bundled} (major.minor の一致を要求)",
            {"pinned": str(pinned), "bundled": str(bundled), "bundled_ref": bundled_ref},
        ),
    ]


def check_action_bundled_dependencies(
    fetch_json: FetchJson, fetch_text: FetchText, root: Path, watched: Sequence[BundledDependency]
) -> list[CheckResult]:
    """4 from issue #656.

    Only the actionable state alerts. Sitting on a vulnerable copy while upstream ships no
    fixed release is the accepted risk, and a check that stayed red for months would keep the
    tracking issue permanently open and drown checks 1-3 in it.
    """
    results: list[CheckResult] = []
    for entry in watched:
        sha = action_pinned_sha(root, entry.action)
        tag = newest_action_release(fetch_json, entry.action)
        pinned = bundled_package_version(fetch_text, entry, sha)
        available = bundled_package_version(fetch_text, entry, tag)
        # None means the Action stopped locking the package at all, i.e. the exposure is gone.
        pin_fixed = pinned is None or pinned >= entry.fixed_in
        release_fixed = available is None or available >= entry.fixed_in
        pinned_label = "同梱なし" if pinned is None else str(pinned)
        latest_label = "同梱なし" if available is None else str(available)
        if pin_fixed:
            detail = f"pin ({sha[:7]}) の {entry.package} は {pinned_label} で、修正版 {entry.fixed_in} 以上"
        elif release_fixed:
            detail = (
                f"pin ({sha[:7]}) は {entry.package} {pinned_label} のまま / "
                f"最新リリース {tag} は {latest_label} -> **SHA 更新で解消できる**"
            )
        else:
            detail = (
                f"pin ({sha[:7]}) / 最新リリース {tag} とも {entry.package} {pinned_label} で、"
                f"修正版 {entry.fixed_in} を lock した release は未公開"
            )
        advisory_link = f"[{entry.advisory}](https://github.com/advisories/{entry.advisory})"
        results.append(
            CheckResult(
                f"4:{entry.action.rsplit('/', maxsplit=1)[-1]}",
                f"{entry.action} 同梱 {entry.package} の既知脆弱性",
                "high",
                pin_fixed or not release_fixed,
                f"{detail} ({advisory_link})",
                {
                    "action": entry.action,
                    "package": entry.package,
                    "fixed_in": str(entry.fixed_in),
                    "advisory": entry.advisory,
                    "pinned_sha": sha,
                    "pinned_version": None if pinned is None else str(pinned),
                    "latest_release": tag,
                    "latest_version": None if available is None else str(available),
                },
            )
        )
    return results


def run_checks(
    fetch_json: FetchJson,
    fetch_text: FetchText,
    root: Path,
    repo: str,
    now: datetime,
    max_pr_age_days: int,
    max_stale_direct: int,
) -> list[CheckResult]:
    config = dependabot_config(root)
    cooldown = cooldown_days(config)
    results = check_pr_age(fetch_json, repo, dependabot_ecosystems(config), now, max_pr_age_days)
    as_of = last_scheduled_update(config, PYTHON_ECOSYSTEM, now)
    stale = stale_direct_dependencies(
        fetch_json, direct_requirements(root), locked_versions(root), as_of, cooldown, declared_python_versions(root)
    )
    results.append(check_stale_dependencies(stale, cooldown, max_stale_direct, as_of))
    setup_uv_sha = setup_uv_pinned_sha(root)
    bundled, bundled_ref = dependabot_bundled_uv(fetch_json, fetch_text)
    results.extend(
        check_uv_pin(
            tool_versions_uv_pin(root),
            known_uv_checksums(fetch_text, setup_uv_sha),
            bundled,
            bundled_ref,
            setup_uv_sha,
        )
    )
    results.extend(check_action_bundled_dependencies(fetch_json, fetch_text, root, WATCHED_ACTION_DEPENDENCIES))
    return results


SEVERITY_MARK = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def render_report(results: Sequence[CheckResult], repo: str, now: datetime) -> str:
    alerts = [result for result in results if not result.ok]
    lines = [
        f"依存更新パイプラインの生存確認 ({repo} / {now.isoformat(timespec='seconds')})",
        "",
        ("✅ 全ての検査を通過した。" if not alerts else f"🚨 {len(alerts)} 件の検査が閾値を超えた。"),
        "",
        "| | 検査 | 重要度 | 結果 |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        mark = "✅" if result.ok else "🚨"
        lines.append(
            f"| {mark} | {result.title} | {SEVERITY_MARK[result.severity]} {result.severity} | {result.detail} |"
        )
    if alerts:
        lines += ["", "## 対応", ""]
        lines.append("`docs/dependency-pipeline.md` の「アラート別の対応」を参照する。")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--max-pr-age-days", type=int, default=DEFAULT_MAX_PR_AGE_DAYS)
    parser.add_argument("--max-stale-direct", type=int, default=DEFAULT_MAX_STALE_DIRECT)
    parser.add_argument("--report", type=Path, help="Markdown レポートの出力先")
    parser.add_argument("--json", type=Path, help="構造化結果の出力先")
    args = parser.parse_args(argv)

    if not args.repo:
        print("--repo または GITHUB_REPOSITORY が必要です", file=sys.stderr)
        return 2

    fetch_json, fetch_text = make_fetchers(os.environ.get("GITHUB_TOKEN"))
    now = datetime.now(UTC)
    # Rendering and writing the report stay inside the boundary: an uncaught failure there
    # exits with 1, which the workflow reads as "threshold exceeded" and turns into a false
    # alert issue. Everything that is not a verdict must exit 2 instead.
    try:
        results = run_checks(
            fetch_json,
            fetch_text,
            PROJECT_ROOT,
            args.repo,
            now,
            args.max_pr_age_days,
            args.max_stale_direct,
        )
        report = render_report(results, args.repo, now)
        print(report, end="")
        if args.report:
            args.report.write_text(report, encoding="utf-8")
        if args.json:
            payload = [
                {
                    "id": result.check_id,
                    "title": result.title,
                    "severity": result.severity,
                    "ok": result.ok,
                    "detail": result.detail,
                    "facts": result.facts,
                }
                for result in results
            ]
            args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (requests.RequestException, yaml.YAMLError, OSError, ValueError, KeyError) as error:
        # A watchdog that fails quietly reproduces the very bug it exists to catch,
        # so surface this as a red run rather than as "healthy".
        print(f"生存確認を実行できませんでした: {error}", file=sys.stderr)
        return 2

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
