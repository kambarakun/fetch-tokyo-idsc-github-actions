#!/usr/bin/env python3
"""Pre-merge vetting of Dependabot pull requests (issue #762).

Dependabot opens up to fifteen version-update PRs every Monday across three ecosystems
(github-actions / uv / pre-commit). CI proves the bump builds; nothing checked whether the
proposed version itself is a landmine -- yanked from PyPI, covered by an advisory, dropping
the Python floor, or pinned to a SHA its tag does not resolve to. This script asks those
questions of public APIs and prints one table per PR with a verdict of OK / WARN / BLOCK.

Versions are read by comparing whole files at the merge base and the head (uv.lock,
workflows, local composite actions, .pre-commit-config.yaml), never from the PR title or
body: #749 was titled "from v3.8.5 to 3.9.8" while its `rev:` said v3.9.8, and PR text is
untrusted input that must not reach a report agents read (AGENTS.md).

Beside the checks, each uv bump compares who PyPI says published the old and the new sdist
(issue #765). That comparison is an observation with no verdict of its own, listed apart from
the table, except when a publisher the old sdist had is gone or replaced (a WARN row). It reads
PyPI's metadata only: no signature, artifact or wheel is verified.

Exit codes: 0 no BLOCK (WARN allowed), 1 at least one BLOCK, 2 the vetting itself failed.
A network or parse failure is always 2 -- "could not check" is never reported as OK or BLOCK.
When several PRs are vetted, one that fails is listed as such beside the others' tables, and the
run still exits 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import operator
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import http_fetch
import requests
import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import InvalidName, canonicalize_name
from packaging.version import InvalidVersion, Version

GITHUB_API = "https://api.github.com"
PYPI_RELEASE = "https://pypi.org/pypi/{name}/{version}/json"
PYPI_PROJECT = "https://pypi.org/pypi/{name}/json"
# The Integrity API answers per distribution file (https://docs.pypi.org/api/integrity/): 404 means the file
# has no provenance, and GitHub's media type gets 406, so it is asked for its own.
PYPI_INTEGRITY_ROOT = "https://pypi.org/integrity/"
PYPI_INTEGRITY = PYPI_INTEGRITY_ROOT + "{name}/{version}/{filename}/provenance"
PYPI_INTEGRITY_ACCEPT = "application/vnd.pypi.integrity.v1+json"
# The fields that name a Trusted Publisher, per kind, as pypi/pypi-attestations (945dd53a) models them:
# (required, optional). Claims and refs (`claims`, CircleCI `vcs_ref`) move with every release and are
# not the publisher. An optional field absent, null or "" is "not set" alike.
PUBLISHER_IDENTITY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "GitHub": (("repository", "workflow"), ("environment",)),
    "GitLab": (("repository", "workflow_filepath"), ("environment",)),
    "Google": (("email",), ()),
    "CircleCI": (("project_id", "pipeline_definition_id"), ("vcs_origin",)),
}
# Publisher values are third-party text: compared whole, but echoed only when shaped like a repository,
# a file path, an e-mail address or an ID.
PUBLISHER_VALUE = re.compile(r"[\w.@/:+-]{1,128}", re.ASCII)
PROVENANCE_SCOPE = "sdist の publisher metadata の比較のみで、署名・配布物・wheel は検証していない"
OSV_QUERY = "https://api.osv.dev/v1/query"
# Repository security advisories (REST "List repository security advisories"): what a maintainer
# published on GitHub, often days before OSV or GitHub's own database carries it (virtualenv
# GHSA-8rjx-v5ww-45pp, issue #765). Public ones need no permission; the listing pages by cursor.
REPOSITORY_ADVISORIES = GITHUB_API + "/repos/{repo}/security-advisories?state=published&per_page=100"
LIST_MAX_PAGES = 20
OSV_MAX_PAGES = 20
# OSV's `introduced: "0"` sorts before every version; Version("0") would sit above 0.0.0-alpha.
# Only `introduced` carries that meaning: `fixed` / `last_affected` / `limit` of "0" stay literal 0.
OSV_ZERO = Version("0.dev0")
# Action versions follow SemVer, whose prerelease / build order PEP 440 does not share (`1.0.0-1` is a
# post-release there), so only purely numeric versions are ordered; anything else is unevaluable.
# ASCII only: Version() rejects other Unicode digits, which would fail the whole PR, not read as unknown.
NUMERIC_VERSION = re.compile(r"v?\d+(?:\.\d+)*", re.ASCII)
DEPENDABOT_AUTHOR = "dependabot[bot]"
ECOSYSTEM_BY_PREFIX = {
    "dependabot/github_actions/": "github-actions",
    "dependabot/uv/": "uv",
    "dependabot/pre_commit/": "pre-commit",
}
EXPECTED_FILES = {
    "uv": ("pyproject.toml", "uv.lock"),
    # GitHub accepts both extensions for workflow files. A local composite action is updated from its
    # own `directories` entry (issue #763); Dependabot reads every YAML there, but only the action's
    # metadata file is one GitHub runs, so any other file stays unexpected.
    "github-actions": (
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        ".github/actions/*/action.yml",
        ".github/actions/*/action.yaml",
    ),
    "pre-commit": (".pre-commit-config.yaml",),
}
# The dependabot.yml ecosystem whose cooldown applies to each kind of bump.
ECOSYSTEM_BY_KIND = {"pypi": "uv", "action": "github-actions", "pre-commit": "pre-commit"}
SUPERSEDED_WINDOW_DAYS = 7
GREEN_CONCLUSIONS = {"success", "skipped", "neutral"}
# In CI this job is itself an in-progress check run on the head commit.
SELF_CHECK_NAME = "vet"
# GitHub's documented default when `cooldown.default-days` is absent.
DEFAULT_COOLDOWN_DAYS = 3
PAGE_SIZE = 100

# A trailing subpath (`github/codeql-action/init@...`) still names the `owner/repo` that owns the tag,
# but is an action of its own with its own metadata. The version comment is optional: a bare SHA
# pin is still a bump, vetted as "no tag to compare".
# YAML may quote the scalar (`uses: "owner/repo@<sha>" # v1`); the comment follows the closing quote.
USES_PATTERN = re.compile(
    r"^\s*-?\s*uses:\s*(?P<quote>[\"']?)(?P<repo>[\w.-]+/[\w.-]+)(?P<subpath>(?:/[\w./-]+)?)"
    r"@(?P<sha>[0-9a-f]{40})(?P=quote)(?:\s*#\s*(?P<version>v?\d[\w.+-]*))?",
    re.ASCII,
)
# A block-style `rev:` of .pre-commit-config.yaml and the version comment on that same line
# (`# v1.2.3`, or `# frozen: v1.2.3` as `pre-commit autoupdate --freeze` writes it).
REV_LINE = re.compile(
    r"^[ \t]*(?:-[ \t]*)?rev:[ \t]*(?P<quote>[\"']?)(?P<rev>[^\s\"'#]+)(?P=quote)"
    r"(?:[ \t]*#[ \t]*(?:frozen:[ \t]*)?(?P<comment>v?\d[\w.-]*))?",
    re.ASCII,
)
# GitHub reads `action.yml`, else `action.yaml`, at the root of the action (metadata syntax docs).
ACTION_METADATA_FILES = ("action.yml", "action.yaml")
# Action metadata is third-party text that ends up in a report agents read: only names shaped
# like an input id or a runtime are echoed.
METADATA_NAME = re.compile(r"[A-Za-z_][\w-]{0,63}", re.ASCII)
RUNTIME_NAME = re.compile(r"[\w.-]{1,32}", re.ASCII)
# Third-party metadata may differ in any number of inputs and outputs. Listing all of them could
# push a PR comment past GitHub's 65,536-character body limit (the --comment POST then fails with
# exit 2) and grows what agents read without bound; names are at most 64 characters, so capping
# the count caps the length too.
METADATA_DIFF_LIMIT = 10
# GitHub calls a workflow only as a file directly in `.github/workflows/`; deeper paths are Actions.
REUSABLE_WORKFLOW = re.compile(r"\.github/workflows/[^/]+\.ya?ml")
# GitHub rejects a comment body over 65,536 characters (the --comment POST then exits 2). Rows come
# from PR content (one per bump, per pinned action path and, when lines moved, per old x new pin
# pair), so the table keeps the most severe rows that fit; the verdict line still counts every
# check and --json keeps them all. The margin leaves room for the two omission lines (rows and
# observations) and the verdict line.
REPORT_BODY_LIMIT = 60_000
REPORT_TAIL_MARGIN = 200
VERDICT_ORDER = ("BLOCK", "WARN", "OK")
OBSERVATION_HEADER = "補助観測 (判定に含めない。PyPI が返す sdist の publisher metadata を旧新で比べただけで、署名・配布物は検証していない):"
OBSERVATION_LABELS = {
    "same": "同一",
    "first_provided": "新版で初めて提供",
    "both_missing": "旧新とも未提供",
    "uncomparable": "比較不能",
}
# Report cells are plain text: escape what could open a link, image, code span, HTML or a new cell.
MARKDOWN_SPECIAL = re.compile(r"([\\|`\[\]<])")
SHORT_SHA = 12
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
GITHUB_HOST = "github.com"
URL_SCHEMES = {"http", "https", "ssh"}
# The path of a repository itself (`/owner/repo`, `.git` and a trailing `/` allowed), no page inside it.
REPOSITORY_PATH = re.compile(r"/(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?", re.ASCII)
# Git's scp-like remote `git@github.com:owner/repo.git` has no scheme for urlsplit to read.
SCP_REMOTE = re.compile(r"(?:[\w.-]+@)?(?P<host>[\w.-]+):(?P<path>[^/].*)", re.ASCII)
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.ASCII)
# Advisory IDs come from a third party and reach the report; only the GHSA shape is echoed.
GHSA_ID = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}", re.ASCII)
# GitHub's affected-version syntax: `= n`, or a lower and / or upper bound joined by ", ". The
# space after the operator is documented but not always written (`>=2019.03.09`); anything else
# (Pillow's `1.0 - 2.0`, a bare version list, `≤`) is not the syntax and stays unevaluable.
ADVISORY_BOUND = re.compile(r"(?P<operator>>=|<=|>|<|=) ?(?P<version>[^\s,]+)", re.ASCII)

FetchJson = Callable[[str], Any]
FetchText = Callable[[str], str]
PostJson = Callable[[str, dict[str, Any]], Any]
FetchList = Callable[[str], list[Any]]
Fetchers = tuple[FetchJson, FetchText, PostJson, FetchList]


class NotDependabotPullRequestError(Exception):
    """Raised for a PR Dependabot did not author; vetting a human PR would mislabel it."""


@dataclass(frozen=True)
class Bump:
    name: str
    old: str | None
    new: str
    kind: str  # "pypi" | "action" | "pre-commit"
    # The new SHA of a workflow `uses:` or of a SHA-pinned pre-commit `rev:`.
    sha: str | None = None


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    dependency: str
    verdict: str
    detail: str
    links: list[str] = field(default_factory=list)
    # "old → new" of the bump this row was computed for; two bumps can share a dependency name.
    change: str = "-"


@dataclass(frozen=True)
class Observation:
    """A comparison with no verdict: reported beside the checks, never counted in the PR's verdict."""

    observation_id: str
    dependency: str
    change: str
    status: str
    detail: str
    links: list[str] = field(default_factory=list)


@dataclass
class PullRequestVerdict:
    number: int
    ecosystem: str | None
    head_sha: str
    bumps: list[Bump]
    checks: list[CheckResult]
    observations: list[Observation] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        verdicts = {check.verdict for check in self.checks}
        return next((level for level in ("BLOCK", "WARN") if level in verdicts), "OK")


@dataclass(frozen=True)
class VettingFailure:
    """A PR the run could not check; `reason` is our own text, never a third-party error body."""

    number: int
    reason: str


# --- network -----------------------------------------------------------------------------


USER_AGENT = "fetch-tokyo-idsc-dependabot-vetting"


def _raise_for_status(response: requests.Response, url: str) -> requests.Response:
    if not response.ok:
        message = http_fetch.status_line(response, url)
        raise requests.HTTPError(f"{message}: {http_fetch.error_detail(response)}", response=response)
    return response


def _http_get(url: str, token: str | None, accept: str) -> requests.Response:
    return _raise_for_status(http_fetch.get(url, token, accept=accept, user_agent=USER_AGENT), url)


def _http_post(url: str, token: str | None, payload: dict[str, Any]) -> requests.Response:
    # The only POSTs are the OSV query and the opt-in PR comment, so the transport is not generalised.
    headers = http_fetch.request_headers(url, token, accept="application/json", user_agent=USER_AGENT)
    response = requests.post(url, json=payload, headers=headers, timeout=http_fetch.TIMEOUT_SECONDS)
    return _raise_for_status(response, url)


def make_fetchers(token: str | None) -> Fetchers:
    """Build the three network accessors; tests substitute them wholesale."""

    def fetch_json(url: str) -> Any:
        accept = PYPI_INTEGRITY_ACCEPT if url.startswith(PYPI_INTEGRITY_ROOT) else "application/vnd.github+json"
        return _http_get(url, token, accept).json()

    def fetch_text(url: str) -> str:
        return _http_get(url, token, "application/vnd.github.raw+json").text

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        return _http_post(url, token, payload).json()

    def fetch_list(url: str) -> list[Any]:
        """Every page of a listing GitHub pages by cursor: the next page is only named in `Link`."""
        items: list[Any] = []
        page_url = url
        for _ in range(LIST_MAX_PAGES):
            response = _http_get(page_url, token, "application/vnd.github+json")
            page = response.json()
            if not isinstance(page, list):
                raise TypeError(f"{page_url} did not answer with a list")
            items.extend(page)
            following = response.links.get("next", {}).get("url")
            if not following:
                return items
            page_url = following
        raise ValueError(f"{url} returned more than {LIST_MAX_PAGES} pages")

    return fetch_json, fetch_text, post_json, fetch_list


def make_poster(token: str | None) -> PostJson:
    def post(url: str, payload: dict[str, Any]) -> Any:
        return _http_post(url, token, payload).json()

    return post


def _osv_key(payload: dict[str, Any]) -> str:
    package = payload["package"]
    page = f"#{payload['page_token']}" if "page_token" in payload else ""
    return f"{package['ecosystem']}/{package['name']}@{payload.get('version', '*')}{page}"


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and response is not None and response.status_code == 404


def _not_found(url: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 404
    response.url = url
    return requests.HTTPError(f"404 Not Found for {url}", response=response)


# The fields each GitHub endpoint is read for (None keeps a value whole, a one-item list applies
# to every element). Titles, bodies, check-run output and links are never read: recording them
# only bloated the fixture past 300 KB (issue #765) and put free text where agents read it.
# Shapes match the whole path: a repository may itself be called `check-runs` or `pulls`.
REPO_PATH = r"^/repos/[^/]+/[^/]+"
FIXTURE_FIELDS: tuple[tuple[re.Pattern[str], Any], ...] = (
    (
        re.compile(REPO_PATH + r"/pulls/\d+$"),
        {
            "user": {"login": None},
            "created_at": None,
            "html_url": None,
            "head": {"ref": None, "sha": None},
            "base": {"sha": None},
        },
    ),
    (re.compile(REPO_PATH + r"/pulls/\d+/files$"), [{"filename": None}]),
    (
        re.compile(REPO_PATH + r"/pulls/\d+/commits$"),
        [{"author": {"login": None}, "commit": {"committer": {"date": None}}}],
    ),
    (re.compile(REPO_PATH + r"/pulls$"), [{"number": None, "user": {"login": None}}]),
    (re.compile(REPO_PATH + r"/compare/[^/]+$"), {"merge_base_commit": {"sha": None}}),
    (
        re.compile(REPO_PATH + r"/commits/[^/]+/check-runs$"),
        {"check_runs": [{"name": None, "status": None, "conclusion": None}]},
    ),
    (
        re.compile(REPO_PATH + r"/releases$"),
        [{"tag_name": None, "published_at": None, "draft": None, "prerelease": None}],
    ),
    (re.compile(REPO_PATH + r"/releases/tags/"), {"published_at": None}),
    (re.compile(REPO_PATH + r"/git/ref/tags/"), {"object": None}),
    (re.compile(REPO_PATH + r"/git/tags/[^/]+$"), {"tagger": {"date": None}, "object": None}),
    (re.compile(REPO_PATH + r"/git/commits/[^/]+$"), {"committer": {"date": None}}),
    (
        re.compile(REPO_PATH + r"/security-advisories$"),
        [
            {
                "ghsa_id": None,
                "state": None,
                "withdrawn_at": None,
                "vulnerabilities": [{"package": None, "vulnerable_version_range": None}],
            }
        ],
    ),
    (re.compile(REPO_PATH + "$"), {"full_name": None}),
)
PYPI_RELEASE_PATH = re.compile(r"^/pypi/(?P<name>[^/]+)/(?P<version>[^/]+)/json$")
# An Action's metadata may sit at any path, `.github/workflows/action.yml` included, so the path
# alone cannot tell it from a workflow of the same name: the PR's own files (its pulls/N/files)
# are what the script parses whole, and only they are kept whole.
ACTION_METADATA_PATH = re.compile(REPO_PATH + r"/contents/(?:[^?]+/)?action\.ya?ml$")
PULL_FILES_PATH = re.compile(r"^(?P<repo>/repos/[^/]+/[^/]+)/pulls/\d+/files$")


def _pick(value: Any, spec: Any) -> Any:
    if spec is None:
        return value
    if isinstance(spec, list):
        return [_pick(item, spec[0]) for item in value] if isinstance(value, list) else value
    if isinstance(value, dict):
        return {key: _pick(value[key], inner) for key, inner in spec.items() if key in value}
    return value


def _trim_for_fixture(url: str, payload: Any, candidates: dict[str, Version] | None = None) -> Any:
    """Keep only what the script reads, so the fixture stays small and inert.

    `candidates` maps a PyPI project to the lowest version already fetched as a candidate.
    `later_releases` only asks for releases above the candidate, so older ones are dropped
    without naming any version here.
    """
    parts = urlsplit(url)
    if url.startswith(PYPI_INTEGRITY_ROOT):
        return _trim_provenance(payload)
    if parts.hostname == "pypi.org":
        # The release time is the earliest upload and a release counts as yanked when every
        # file is, so one synthetic file per release preserves both answers.
        info = payload.get("info") or {}
        trimmed: dict[str, Any] = {"info": {key: info.get(key) for key in ("yanked", "requires_python")}}
        # Only repository URLs are read; labels and other URLs are publisher-written text.
        trimmed["info"]["project_urls"] = {str(index): url for index, url in enumerate(_repository_urls(info))}
        if "urls" in payload:
            # The provenance comparison names the sdist by its file name; the other files are compacted.
            sdists = [_sdist_file(item) for item in payload["urls"] if item.get("packagetype") == "sdist"]
            others = [item for item in payload["urls"] if item.get("packagetype") != "sdist"]
            trimmed["urls"] = ([_compact_files(others)] if others else []) + sdists
        if "releases" in payload:
            floor = (candidates or {}).get(parts.path.split("/")[2])
            trimmed["releases"] = {
                version: [_compact_files(files)] if files else []
                for version, files in payload["releases"].items()
                if floor is None or ((parsed := _parse_version(version)) is not None and parsed > floor)
            }
        return trimmed
    spec = next((fields for pattern, fields in FIXTURE_FIELDS if pattern.match(parts.path)), None)
    return _pick(payload, spec)


def _compact_files(files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "upload_time_iso_8601": min(item["upload_time_iso_8601"] for item in files),
        "yanked": all(item.get("yanked", False) for item in files),
    }


def _sdist_file(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": item["filename"],
        "packagetype": "sdist",
        "upload_time_iso_8601": item["upload_time_iso_8601"],
        "yanked": item.get("yanked", False),
    }


def _bounded(value: str) -> str:
    # A value the report would not show becomes a digest: still compared exactly, never stored as prose.
    return value if not value or PUBLISHER_VALUE.fullmatch(value) else f"({_digest(value)})"


def _trim_provenance(payload: Any) -> dict[str, Any]:
    """Each publisher's identity only: the bundles are kilobytes of certificates nothing reads.

    An unreadable publisher keeps nothing and replays as unreadable; a malformed answer is not
    recorded at all, it fails the check as it would live.
    """
    bundles: list[dict[str, Any]] = []
    for publisher in _publishers(payload):
        identity = _publisher_identity(publisher)
        kept = {} if identity is None else {"kind": identity[0]} | {key: _bounded(value) for key, value in identity[1]}
        bundles.append({"publisher": kept})
    return {"attestation_bundles": bundles}


def make_recording_fetchers(token: str | None, record_dir: Path) -> Fetchers:
    """Live fetchers that also write every successful response into a replayable fixture."""
    live_json, live_text, live_post, live_list = make_fetchers(token)
    record_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}
    candidates: dict[str, Version] = {}
    pr_files: set[str] = set()

    def save(key: str, payload: Any, *, raw: bool, refresh: bool = False) -> None:
        if key in index and not refresh:
            return
        slug = re.sub(r"[^A-Za-z0-9]+", "-", key.split(" ", 1)[1])[-60:].strip("-")
        filename = index.get(key) or f"{len(index):03d}-{slug}.{'txt' if raw else 'json'}"
        target = record_dir / filename
        if raw:
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        index[key] = filename
        (record_dir / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")

    def fetch_json(url: str) -> Any:
        payload = _trim_for_fixture(url, live_json(url), candidates)
        parts = urlsplit(url)
        if files := PULL_FILES_PATH.match(parts.path):
            # The same quoting as file_at, so the contents URL of each file matches below.
            pr_files.update(f"{files['repo']}/contents/{quote(item['filename'])}" for item in payload)
        match = PYPI_RELEASE_PATH.match(parts.path) if parts.hostname == "pypi.org" else None
        if match and (version := _parse_version(match["version"])) is not None:
            candidates[match["name"]] = min(version, candidates.get(match["name"], version))
        # A later PR may bring a lower candidate of the same project: keep the widest release list.
        save(f"GET {url}", payload, raw=False, refresh=parts.hostname == "pypi.org" and match is None)
        return payload

    def fetch_text(url: str) -> str:
        text = live_text(url)
        path = urlsplit(url).path
        metadata = ACTION_METADATA_PATH.match(path) and path not in pr_files
        save(f"GET {url}", _trim_action_metadata(text) if metadata else text, raw=True)
        return text

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        response = live_post(url, payload)
        save(f"POST {url} {_osv_key(payload)}", response, raw=False)
        return response

    def fetch_list(url: str) -> list[Any]:
        payload = _trim_for_fixture(url, live_list(url))
        save(f"GET {url}", payload, raw=False)
        return list(payload)

    return fetch_json, fetch_text, post_json, fetch_list


def make_fixture_fetchers(fixture_dir: Path) -> Fetchers:
    """Replay a recorded fixture offline; anything not recorded answers 404, like the live API."""
    index: dict[str, str] = json.loads((fixture_dir / "index.json").read_text(encoding="utf-8"))

    def load(key: str, url: str) -> Any:
        if key not in index:
            raise _not_found(url)
        return (fixture_dir / index[key]).read_text(encoding="utf-8")

    def fetch_json(url: str) -> Any:
        return json.loads(load(f"GET {url}", url))

    def fetch_text(url: str) -> str:
        return str(load(f"GET {url}", url))

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        return json.loads(load(f"POST {url} {_osv_key(payload)}", url))

    # Every page was joined into one recording.
    return fetch_json, fetch_text, post_json, fetch_json


def _memoize(fetch: Callable[[str], Any]) -> Callable[[str], Any]:
    """Several checks resolve the same tag or release; ask the network once per URL."""
    cache: dict[str, Any] = {}

    def cached(url: str) -> Any:
        if url not in cache:
            try:
                cache[url] = fetch(url)
            except requests.HTTPError as exc:
                if not _is_not_found(exc):
                    raise
                cache[url] = exc
        if isinstance(cache[url], requests.HTTPError):
            raise cache[url]
        return cache[url]

    return cached


def _get_or_none(fetch_json: FetchJson, url: str) -> Any:
    """404 is a signal (no tag, no release); every other failure aborts with exit 2."""
    try:
        return fetch_json(url)
    except requests.HTTPError as exc:
        if _is_not_found(exc):
            return None
        raise


def _paged(fetch_json: FetchJson, url: str, key: str | None = None) -> list[Any]:
    items: list[Any] = []
    page = 1
    while True:
        payload = fetch_json(f"{url}{'&' if '?' in url else '?'}per_page={PAGE_SIZE}&page={page}")
        batch = payload[key] if key else payload
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
        page += 1


# --- pull request input --------------------------------------------------------------------


def load_pull_request(fetch_json: FetchJson, repo: str, number: int) -> dict[str, Any]:
    return fetch_json(f"{GITHUB_API}/repos/{repo}/pulls/{number}")


def changed_files(fetch_json: FetchJson, repo: str, number: int) -> list[str]:
    return [item["filename"] for item in _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/pulls/{number}/files")]


def pull_request_commits(fetch_json: FetchJson, repo: str, number: int) -> list[dict[str, Any]]:
    return _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/pulls/{number}/commits")


def merge_base(fetch_json: FetchJson, repo: str, base_sha: str, head_sha: str) -> str:
    return fetch_json(f"{GITHUB_API}/repos/{repo}/compare/{base_sha}...{head_sha}")["merge_base_commit"]["sha"]


def file_at(fetch_text: FetchText, repo: str, path: str, ref: str) -> str | None:
    try:
        return fetch_text(f"{GITHUB_API}/repos/{repo}/contents/{quote(path)}?ref={ref}")
    except requests.HTTPError as exc:
        if _is_not_found(exc):
            return None
        raise


def detect_ecosystem(head_ref: str) -> str | None:
    return next((eco for prefix, eco in ECOSYSTEM_BY_PREFIX.items() if head_ref.startswith(prefix)), None)


def open_dependabot_prs(fetch_json: FetchJson, repo: str) -> list[int]:
    pulls = _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/pulls?state=open")
    return sorted(pull["number"] for pull in pulls if pull["user"]["login"] == DEPENDABOT_AUTHOR)


# --- parsers -------------------------------------------------------------------------------


def _parse_version(text: str | None) -> Version | None:
    if text is None:
        return None
    try:
        return Version(text.removeprefix("v"))
    except InvalidVersion:
        return None


def _numeric_version(text: str | None) -> Version | None:
    return Version(text.removeprefix("v")) if text and NUMERIC_VERSION.fullmatch(text) else None


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _version_key(text: str) -> tuple[int, Version | str]:
    parsed = _parse_version(text)
    return (1, parsed) if parsed is not None else (0, text)


def _pair_bumps(before: dict[str, set[str]], after: dict[str, set[str]], kind: str) -> list[Bump]:
    bumps: list[Bump] = []
    for name in sorted(after):
        old_versions = before.get(name, set())
        removed = sorted(old_versions - after[name], key=_version_key)
        for new in sorted(after[name] - old_versions, key=_version_key):
            # A lock can hold several versions of one package (resolution forks): the predecessor
            # is the highest removed version not above the new one, else the lowest removed one.
            below = [old for old in removed if _version_key(old) <= _version_key(new)]
            old = below[-1] if below else removed[0] if removed else None
            bumps.append(Bump(name, old, new, kind))
    return bumps


def _lock_versions(text: str | None) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for package in tomllib.loads(text or "").get("package", []):
        # Only registry packages exist on PyPI; the project itself is `editable` / `virtual`.
        if "registry" in package.get("source", {}):
            versions.setdefault(package["name"], set()).add(package["version"])
    return versions


def uv_lock_bumps(before: str | None, after: str | None) -> list[Bump]:
    return _pair_bumps(_lock_versions(before), _lock_versions(after), "pypi")


@dataclass(frozen=True)
class ActionPinChange:
    """One `uses:` path moved to a new commit; unlike a Bump, subpaths of one repository stay apart."""

    repo: str
    subpath: str
    old_sha: str | None
    old_version: str | None
    new_sha: str
    new_version: str

    @property
    def action(self) -> str:
        return f"{self.repo}/{self.subpath}" if self.subpath else self.repo


Pin = tuple[str | None, str | None]


def _action_pins(text: str | None) -> dict[tuple[str, str], list[tuple[int, str, str]]]:
    """Each `uses:` line of a path in file order: (line number, sha, version)."""
    pins: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
    for number, line in enumerate((text or "").splitlines()):
        if match := USES_PATTERN.match(line):
            version = match["version"] or match["sha"][:SHORT_SHA]
            key = (match["repo"], match["subpath"].removeprefix("/"))
            pins.setdefault(key, []).append((number, match["sha"], version))
    return pins


def _without_pins(text: str | None) -> list[str]:
    """The file with every pinned commit and its version comment cut out of the `uses:` lines."""
    return [
        line[: match.start("sha")] + line[match.end() :] if (match := USES_PATTERN.match(line)) else line
        for line in (text or "").splitlines()
    ]


def action_pin_changes(before: str | None, after: str | None) -> list[ActionPinChange]:
    old_pins, new_pins = _action_pins(before), _action_pins(after)
    # Dependabot only rewrites pins in place. Then each `uses:` line still belongs to the same step
    # (its name and `with:` around it unchanged), so the line's old pin is what that step moved from.
    in_place = before is not None and _without_pins(before) == _without_pins(after)
    changes: list[ActionPinChange] = []
    for (repo, subpath), new_uses in sorted(new_pins.items()):
        old_uses = old_pins.get((repo, subpath), [])
        pairs: list[tuple[Pin, tuple[str, str]]]
        if in_place and [number for number, _, _ in old_uses] == [number for number, _, _ in new_uses]:
            # Steps converging on one pin, or onto a pin a sibling kept, each keep their own predecessor.
            pairs = [((old[1], old[2]), (new[1], new[2])) for old, new in zip(old_uses, new_uses, strict=True)]
        else:
            # Anything else moved, so which step became which is unknown, and a pin still in the file
            # may now serve another step (a -> b, b -> c). Compare each new pin with every old pin of
            # the path rather than guess one; with no old pin there is nothing to compare.
            old_by_sha = {sha: version for _, sha, version in old_uses}
            new_by_sha = {sha: version for _, sha, version in new_uses}
            old_candidates: list[Pin] = list(old_by_sha.items()) or [(None, None)]
            pairs = [(old, new) for new in new_by_sha.items() for old in old_candidates]
        for (old_sha, old_version), (new_sha, new_version) in dict.fromkeys(pairs):
            if old_sha != new_sha:
                changes.append(ActionPinChange(repo, subpath, old_sha, old_version, new_sha, new_version))
    return changes


def workflow_bumps(before: str | None, after: str | None) -> list[Bump]:
    """A bump per new commit and the pin it replaced, paired like `action_pin_changes`.

    One action may be pinned to several series (v4 in one job, v5 in another); the highest old
    pin is not what a partial update replaced. Where the pairing is unknown, every candidate old
    pin is its own bump, so a major jump from any of them still shows.
    """
    old_shas: set[tuple[str, str]] = set()
    old_versions: dict[str, list[str | None]] = {}
    for (repo, _), uses in _action_pins(before).items():
        for _, sha, version in uses:
            old_shas.add((repo, sha))
            old_versions.setdefault(repo, []).append(version)
    bumps: list[Bump] = []
    for change in action_pin_changes(before, after):
        # A pin already in the file was vetted when it arrived; only new commits are bumps.
        if (change.repo, change.new_sha) in old_shas:
            continue
        # A path without an old pin of its own (a new subpath) may continue any pin of its repository.
        olds = [change.old_version] if change.old_sha else old_versions.get(change.repo) or [None]
        bumps += [Bump(change.repo, old, change.new_version, "action", sha=change.new_sha) for old in olds]
    return _dedupe(bumps)


def _pre_commit_revs(text: str | None) -> dict[str, tuple[str, str | None]]:
    if not text:
        return {}
    revs: dict[str, tuple[str, str | None]] = {}
    entries: list[tuple[str, str]] = []
    for entry in yaml.safe_load(text).get("repos", []):
        rev = entry.get("rev")
        if rev is None:  # `local` and `meta` repositories carry no version
            continue
        entries.append((entry["repo"], str(rev)))
    # The k-th `rev:` line is the k-th entry's: a version comment belongs to the rev on its own line,
    # never to the same rev of another repository or to a comment on the next line. Lines that
    # cannot be paired (flow style, a count or value mismatch) lend no comment rather than a guess.
    lines = [match for line in text.splitlines() if (match := REV_LINE.match(line))]
    paired = len(lines) == len(entries)
    for index, (repo, rev) in enumerate(entries):
        line = lines[index] if paired else None
        revs[repo] = (rev, line["comment"] if line and line["rev"] == rev else None)
    return revs


def pre_commit_bumps(before: str | None, after: str | None) -> list[Bump]:
    old_revs, new_revs = _pre_commit_revs(before), _pre_commit_revs(after)
    bumps: list[Bump] = []
    for repo_url, (rev, comment) in sorted(new_revs.items()):
        if old_revs.get(repo_url, (None, None))[0] == rev:
            continue
        name = github_repository(repo_url) or repo_url
        old_rev, old_comment = old_revs.get(repo_url, (None, None))
        is_sha = SHA_PATTERN.fullmatch(rev) is not None
        bumps.append(
            Bump(
                name,
                old_comment or old_rev,
                (comment or rev) if is_sha else rev,
                "pre-commit",
                sha=rev if is_sha else None,
            )
        )
    return bumps


def requires_python_floor(pyproject_text: str | None) -> Version:
    spec = tomllib.loads(pyproject_text or "").get("project", {}).get("requires-python")
    if not spec:
        raise ValueError("pyproject.toml has no project.requires-python")
    floors = [Version(item.version) for item in SpecifierSet(spec) if item.operator in {">=", "~=", "=="}]
    if not floors:
        raise ValueError(f"requires-python {spec!r} has no lower bound")
    # Every lower bound applies at once, so the tightest one is the effective floor.
    return max(floors)


def cooldown_days(dependabot_yml_text: str | None, ecosystem: str) -> int:
    config = yaml.safe_load(dependabot_yml_text or "") or {}
    for update in config.get("updates", []):
        if update.get("package-ecosystem") == ecosystem:
            return int((update.get("cooldown") or {}).get("default-days", DEFAULT_COOLDOWN_DAYS))
    return DEFAULT_COOLDOWN_DAYS


def _dedupe(bumps: Iterable[Bump]) -> list[Bump]:
    # #745 moved the same setup-uv pin in five workflows; vet it once. Two different SHAs under
    # the same version comment stay separate so the one that does not match its tag still BLOCKs.
    return list(dict.fromkeys(bumps))


# --- release metadata ----------------------------------------------------------------------


def github_repository(url: str) -> str | None:
    """`owner/repo` when the URL is a repository on github.com itself, else None.

    The host must be github.com exactly, not merely appear in the URL (a mirror at
    `https://mirror.example/github.com/owner/repo` is not GitHub), and the path must be the
    repository, not a page in it: `/owner/repo/issues` is left alone rather than trimmed.
    """
    scp = None if "://" in url else SCP_REMOTE.fullmatch(url)
    if scp:
        host, path = scp["host"], f"/{scp['path']}"
    else:
        parts = urlsplit(url)
        try:
            port = parts.port
        except ValueError:
            return None
        if parts.scheme not in URL_SCHEMES or port is not None or parts.query or parts.fragment:
            return None
        host, path = parts.hostname or "", parts.path
    match = REPOSITORY_PATH.fullmatch(path)
    if host.lower() != GITHUB_HOST or match is None or {match["owner"], match["name"]} & {".", ".."}:
        return None
    return f"{match['owner']}/{match['name']}"


def _github_repo(bump: Bump) -> str | None:
    # Pre-commit hooks hosted outside GitHub keep their full URL as the name.
    return None if bump.kind == "pypi" or not REPOSITORY_NAME.fullmatch(bump.name) else bump.name


def _tag(bump: Bump) -> str | None:
    """The tag a GitHub bump claims: the `rev:` itself, or the version comment beside a SHA."""
    if bump.sha and bump.sha.startswith(bump.new):  # no version comment beside the SHA
        return None
    return bump.new


def _resolve_tag(fetch_json: FetchJson, repo: str, tag: str) -> tuple[str, datetime | None] | None:
    """Return the commit a tag points at and, for annotated tags, the tagger date."""
    ref = _get_or_none(fetch_json, f"{GITHUB_API}/repos/{repo}/git/ref/tags/{quote(tag)}")
    if ref is None:
        return None
    target = ref["object"]
    tagged: datetime | None = None
    while target["type"] == "tag":
        annotated = fetch_json(f"{GITHUB_API}/repos/{repo}/git/tags/{target['sha']}")
        tagged = tagged or _parse_time(annotated["tagger"]["date"])
        target = annotated["object"]
    return target["sha"], tagged


def _pypi_upload_time(files: list[dict[str, Any]]) -> datetime | None:
    return min((_parse_time(item["upload_time_iso_8601"]) for item in files), default=None)


def release_published_at(fetch_json: FetchJson, bump: Bump) -> datetime | None:
    if bump.kind == "pypi":
        release = fetch_json(PYPI_RELEASE.format(name=bump.name, version=bump.new))
        return _pypi_upload_time(release["urls"])
    repo, tag = _github_repo(bump), _tag(bump)
    if repo is None or tag is None:
        return None
    release = _get_or_none(fetch_json, f"{GITHUB_API}/repos/{repo}/releases/tags/{quote(tag)}")
    if release is not None and release.get("published_at"):
        return _parse_time(release["published_at"])
    resolved = _resolve_tag(fetch_json, repo, tag)
    if resolved is None:
        return None
    sha, tagged = resolved
    if tagged is not None:
        return tagged
    return _parse_time(fetch_json(f"{GITHUB_API}/repos/{repo}/git/commits/{sha}")["committer"]["date"])


def later_releases(fetch_json: FetchJson, bump: Bump) -> list[tuple[Version, datetime]] | None:
    """Stable releases newer than the bump, or None when the source publishes no releases.

    PyPI versions are PEP 440. GitHub tags are ordered only when purely numeric (as advisory ranges
    are): PEP 440 would read SemVer `v1.0.0-1` as the post-release 1.0.0.post1 and `v1.0.1+build` as
    a local version, so neither the candidate nor such a release is placed by that order.
    """
    current = _parse_version(bump.new) if bump.kind == "pypi" else _numeric_version(_tag(bump))
    if current is None:
        return None
    found: list[tuple[Version, datetime]] = []
    if bump.kind == "pypi":
        for text, files in fetch_json(PYPI_PROJECT.format(name=bump.name))["releases"].items():
            version, uploaded = _parse_version(text), _pypi_upload_time(files)
            if all(item.get("yanked", False) for item in files) or uploaded is None:
                continue
            if version is not None and not version.is_prerelease and version > current:
                found.append((version, uploaded))
        return sorted(found)
    repo = _github_repo(bump)
    if repo is None:
        return None
    # `/releases/latest` follows floating tags (docs/dependency-pipeline.md check 4), so sort ourselves.
    releases = _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/releases")
    if not releases:
        return None
    for release in releases:
        version = _numeric_version(release["tag_name"])
        if release.get("draft") or release.get("prerelease") or version is None:
            continue
        if version > current and release.get("published_at"):
            found.append((version, _parse_time(release["published_at"])))
    return sorted(found)


def _release_link(bump: Bump) -> str:
    if bump.kind == "pypi":
        return f"https://pypi.org/project/{bump.name}/{bump.new}/"
    if (repo := _github_repo(bump)) and (tag := _tag(bump)):
        return f"https://github.com/{repo}/releases/tag/{tag}"
    return f"https://github.com/{bump.name}"


# --- checks --------------------------------------------------------------------------------


def _result(check_id: str, bump: Bump | None, verdict: str, detail: str, links: Sequence[str] = ()) -> CheckResult:
    if bump is None:
        return CheckResult(check_id, "-", verdict, detail, list(links))
    return CheckResult(check_id, bump.name, verdict, detail, list(links), f"{bump.old or '(新規)'} → {bump.new}")


def check_yanked(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    release = fetch_json(PYPI_RELEASE.format(name=bump.name, version=bump.new))
    files = release["urls"]
    if release["info"].get("yanked") or (files and all(item.get("yanked", False) for item in files)):
        # `yanked_reason` is publisher-written free text and stays out of a report agents read.
        return [_result("yanked", bump, "BLOCK", "PyPI で yank 済み (理由は PyPI で確認)", [_release_link(bump)])]
    return [_result("yanked", bump, "OK", "PyPI で yank されていない", [_release_link(bump)])]


def _osv_links(ids: Iterable[str]) -> list[str]:
    return [f"https://osv.dev/vulnerability/{vuln_id}" for vuln_id in ids]


Interval = tuple[Version, Version | None, bool]  # start, end (None = unbounded), end inclusive


def _affected_intervals(events: list[dict[str, str]]) -> tuple[list[Interval], Version | None] | None:
    """Turn one OSV ECOSYSTEM range into affected intervals plus an exclusive upper cap.

    None when a bound is not a parseable version. Follows the OSV evaluation algorithm: a
    version that is not before any `limit` (`*` being infinite) is outside the range, whatever
    the introduced / fixed events say, so the limits collapse into a single cap.
    """
    parsed: list[tuple[str, Version | None]] = []
    for event in events:
        (kind, bound), *_ = event.items()
        if bound == "*":
            parsed.append((kind, None))
            continue
        limit = OSV_ZERO if kind == "introduced" and bound == "0" else _numeric_version(bound)
        if limit is None:
            return None
        parsed.append((kind, limit))
    limits = [limit for kind, limit in parsed if kind == "limit"]
    cap = None if not limits or None in limits else max(limit for limit in limits if limit is not None)
    # Publishers are only asked to pre-sort events; the evaluation itself walks them sorted.
    status = sorted(
        ((kind, limit) for kind, limit in parsed if kind != "limit" and limit is not None), key=lambda item: item[1]
    )
    intervals: list[Interval] = []
    start: Version | None = None
    for kind, limit in status:
        if kind == "introduced" and start is None:
            start = limit
        elif kind in {"fixed", "last_affected"} and start is not None:
            intervals.append((start, limit, kind == "last_affected"))
            start = None
    if start is not None:
        intervals.append((start, None, False))
    return intervals, cap


def _range_overlaps(events: list[dict[str, str]], low: Version, high: Version | None) -> bool | None:
    """Whether a range affects any version in [low, high); high None means "only `low` itself"."""
    evaluated = _affected_intervals(events)
    if evaluated is None:
        return None
    intervals, cap = evaluated
    if high is None:
        return (cap is None or low < cap) and any(
            start <= low and (end is None or low < end or (inclusive and low == end))
            for start, end, inclusive in intervals
        )
    if cap is not None:
        high = min(high, cap)
    return low < high and any(
        start < high and (end is None or low < end or (inclusive and low == end)) for start, end, inclusive in intervals
    )


def _action_precision(bump: Bump) -> tuple[str, Version | None]:
    """How exactly the version comment names a release: exact `vX.Y.Z`, floating `vN` / `vN.M`, or unknown."""
    version = _numeric_version(_tag(bump))
    if version is None:
        return "unknown", None
    return ("exact" if len(version.release) >= 3 else "floating"), version


def _imprecise_label(bump: Bump, precision: str) -> str:
    if precision == "floating":
        return f"浮動タグ {bump.new}"
    return "版コメントが無い SHA pin" if _tag(bump) is None else f"数値だけでない版 {bump.new}"


def _prefix_bounds(prefix: tuple[int, ...]) -> tuple[Version, Version]:
    # `v7` spans [7.0.0, 8.0.0); `v7.1` spans [7.1.0, 7.2.0).
    upper = (*prefix[:-1], prefix[-1] + 1)
    return Version(".".join(map(str, prefix))), Version(".".join(map(str, upper)))


def _action_hits(vulns: list[dict[str, Any]], bump: Bump, names: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Match GitHub Actions advisories client side: OSV ignores `version` for this ecosystem.

    Returns (hits, possible, unevaluable): a hit pins the exact version, a possible match is an
    advisory that may cover a floating or unknown version, and an unevaluable range is neither.
    """
    precision, version = _action_precision(bump)
    hits: list[str] = []
    possible: list[str] = []
    unevaluable: list[str] = []
    for vuln in vulns:
        for affected in vuln.get("affected", []):
            package = affected.get("package", {})
            # `*` names every package in the ecosystem (OSV schema, affected[].package).
            name = package.get("name", "").lower()
            if package.get("ecosystem") != "GitHub Actions" or (name != "*" and name not in names):
                continue
            if version is None:
                possible.append(vuln["id"])
                continue
            listed = [item.removeprefix("v") for item in affected.get("versions", [])]
            if precision == "exact":
                low, high = version, None
                if bump.new.removeprefix("v") in listed:
                    hits.append(vuln["id"])
                    continue
            else:
                prefix = version.release
                low, high = _prefix_bounds(prefix)
                if any((v := _parse_version(item)) and v.release[: len(prefix)] == prefix for item in listed):
                    possible.append(vuln["id"])
                    continue
            for item in affected.get("ranges", []):
                overlap = (
                    _range_overlaps(item.get("events", []), low, high) if item.get("type") == "ECOSYSTEM" else None
                )
                if overlap is None:
                    unevaluable.append(vuln["id"])
                elif overlap:
                    (hits if precision == "exact" else possible).append(vuln["id"])
    return sorted(set(hits)), sorted(set(possible)), sorted(set(unevaluable) - set(hits) - set(possible))


def _osv_vulns(post_json: PostJson, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect every page: OSV may answer with nothing but a `next_page_token`."""
    vulns: list[dict[str, Any]] = []
    for _ in range(OSV_MAX_PAGES):
        response = post_json(OSV_QUERY, payload)
        vulns.extend(response.get("vulns", []))
        if not response.get("next_page_token"):
            return vulns
        payload = {**payload, "page_token": response["next_page_token"]}
    raise ValueError(f"OSV returned more than {OSV_MAX_PAGES} pages for {_osv_key(payload)}")


def _active(vulns: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    # A withdrawn advisory was retracted by its publisher and no longer says anything.
    return [vuln for vuln in vulns if not vuln.get("withdrawn")]


def check_advisory(fetch_json: FetchJson, post_json: PostJson, bump: Bump) -> list[CheckResult]:
    if bump.kind == "pypi":
        payload = {"package": {"name": bump.name, "ecosystem": "PyPI"}, "version": bump.new}
        hits = sorted(vuln["id"] for vuln in _active(_osv_vulns(post_json, payload)))
        possible: list[str] = []
        unevaluable: list[str] = []
    else:
        # OSV package names are case-sensitive while GitHub accepts any casing in `uses:`, and a
        # transferred repository keeps answering to its old name through a redirect.
        repository = _get_or_none(fetch_json, f"{GITHUB_API}/repos/{bump.name}")
        spellings = list(dict.fromkeys([bump.name, repository["full_name"] if repository else bump.name]))
        vulns: dict[str, dict[str, Any]] = {}
        for name in spellings:
            payload = {"package": {"name": name, "ecosystem": "GitHub Actions"}}
            vulns.update((vuln["id"], vuln) for vuln in _active(_osv_vulns(post_json, payload)))
        names = {name.lower() for name in spellings}
        hits, possible, unevaluable = _action_hits(list(vulns.values()), bump, names)
    if hits:
        return [_result("advisory", bump, "BLOCK", f"OSV に該当 ({', '.join(hits)})", _osv_links(hits))]
    reasons: list[str] = []
    if possible:
        label = _imprecise_label(bump, _action_precision(bump)[0])
        reasons.append(f"{label} のため厳密判定不能 ({', '.join(possible)})。pin の SHA に対応する release を確認")
    if unevaluable:
        reasons.append(f"評価できない range ({', '.join(unevaluable)})")
    if reasons:
        return [_result("advisory", bump, "WARN", "; ".join(reasons), _osv_links([*possible, *unevaluable]))]
    return [_result("advisory", bump, "OK", "OSV に該当なし")]


def _repository_urls(info: dict[str, Any]) -> dict[str, str]:
    """Each project URL that is a GitHub repository itself, with its `owner/repo`, in the publisher's order."""
    found: dict[str, str] = {}
    for url in (info.get("project_urls") or {}).values():
        if isinstance(url, str) and (repo := github_repository(url)):
            found[url] = repo
    return found


def _project_repositories(release: dict[str, Any]) -> list[str]:
    # GitHub names are case-insensitive: `Astral-sh/Ruff.git` is the same repository, and one
    # spelling keeps its advisory listing a single cached URL across packages.
    return list(dict.fromkeys(repo.lower() for repo in _repository_urls(release["info"]).values()))


ADVISORY_COMPARISONS: dict[str, Callable[[Version, Version], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "=": operator.eq,
}


def _in_advisory_range(text: Any, version: Version) -> bool | None:
    """Whether a `vulnerable_version_range` covers the version; None when it is not GitHub's syntax."""
    if not isinstance(text, str) or not text.strip():
        return None
    terms: list[tuple[str, Version]] = []
    for term in text.split(","):
        match = ADVISORY_BOUND.fullmatch(term.strip())
        bound = _parse_version(match["version"]) if match else None
        if match is None or bound is None:
            return None
        terms.append((match["operator"], bound))
    # One field holds one range: a single term, or a lower bound followed by an upper bound. Read
    # as AND, anything else (`= 1.0, = 1.1` listing versions) would cover nothing and pass as OK;
    # so would bounds that cross (`>= 2, < 1`) or meet with a strict side (`> 1, < 1`).
    kinds = [kind for kind, _ in terms]
    if len(terms) > 2 or (len(terms) == 2 and (kinds[0] not in {">=", ">"} or kinds[1] not in {"<=", "<"})):
        return None
    if len(terms) == 2:
        (lower_kind, lower), (upper_kind, upper) = terms
        if lower > upper or (lower == upper and (lower_kind, upper_kind) != (">=", "<=")):
            return None
    return all(ADVISORY_COMPARISONS[kind](version, bound) for kind, bound in terms)


def _pypi_name(name: Any) -> str | None:
    """The normalised name, or None when the advisory names nothing PyPI could hold (`psf/black`)."""
    try:
        return canonicalize_name(name, validate=True) if isinstance(name, str) else None
    except InvalidName:
        return None


def _repository_advisory_match(advisory: dict[str, Any], name: str, version: Version) -> str | None:
    """One of hit / possible (the package is unnamed or unreadable) / unevaluable, or None: not this package."""
    outcomes: set[str] = set()
    # An advisory listing no vulnerability says neither which package nor which versions.
    for vulnerability in advisory.get("vulnerabilities") or [{}]:
        package = vulnerability.get("package") or {}
        ecosystem, named = package.get("ecosystem"), _pypi_name(package.get("name"))
        # Another ecosystem, or another package of the same repository, is not this candidate.
        if (ecosystem is not None and ecosystem != "pip") or (named is not None and named != name):
            continue
        inside = _in_advisory_range(vulnerability.get("vulnerable_version_range"), version)
        if inside is None:
            outcomes.add("unevaluable")
        elif inside:
            outcomes.add("hit" if named is not None and ecosystem == "pip" else "possible")
    return next((outcome for outcome in ("hit", "possible", "unevaluable") if outcome in outcomes), None)


def _osv_ids(post_json: PostJson, name: str) -> set[str]:
    """Every ID and alias OSV carries for the package, withdrawn ones excepted."""
    vulns = _active(_osv_vulns(post_json, {"package": {"name": name, "ecosystem": "PyPI"}}))
    return {vuln_id for vuln in vulns for vuln_id in [vuln.get("id"), *(vuln.get("aliases") or [])] if vuln_id}


def check_repository_advisory(
    fetch_json: FetchJson, post_json: PostJson, fetch_list: FetchList, bump: Bump
) -> list[CheckResult]:
    """Advisories the project published in its own GitHub repository, for the ones OSV does not carry yet.

    The repository comes from the candidate's PyPI `project_urls`. Not finding one, an affected range
    that is not GitHub's syntax, or an advisory without a readable package is a WARN, never "no advisory".
    """
    release = fetch_json(PYPI_RELEASE.format(name=bump.name, version=bump.new))
    repositories = _project_repositories(release)
    if not repositories:
        detail = "PyPI の project_urls に GitHub の repo が無いため repo advisory を確認できない"
        return [_result("repo_advisory", bump, "WARN", detail, [_release_link(bump)])]
    listed: dict[str, list[dict[str, Any]]] = {}
    for repo in repositories:
        # 404: shaped like a repository but not one, such as a `github.com/sponsors/<user>` link.
        if (advisories := _get_or_none(fetch_list, REPOSITORY_ADVISORIES.format(repo=repo))) is not None:
            listed[repo] = advisories
    if not listed:
        detail = (
            f"project_urls の GitHub repo ({', '.join(repositories)}) がどれも 404 のため repo advisory を確認できない"
        )
        return [_result("repo_advisory", bump, "WARN", detail, [_release_link(bump)])]
    version, name = Version(bump.new), canonicalize_name(bump.name)
    published = [
        (repo, advisory)
        for repo, advisories in listed.items()
        for advisory in advisories
        if advisory.get("state") == "published" and not advisory.get("withdrawn_at")
    ]
    matched = [
        (repo, advisory, outcome)
        for repo, advisory in published
        if (outcome := _repository_advisory_match(advisory, name, version)) is not None
    ]
    # OSV judges what it carries with the reviewed range; the repository's own text may be looser.
    covered = _osv_ids(post_json, bump.name) if matched else set()
    pending = [
        (repo, advisory, outcome) for repo, advisory, outcome in matched if advisory.get("ghsa_id") not in covered
    ]
    by_outcome = {
        outcome: [(repo, advisory) for repo, advisory, found in pending if found == outcome]
        for outcome in ("hit", "possible", "unevaluable")
    }
    if by_outcome["hit"]:
        ids, links = _advisory_refs(by_outcome["hit"])
        return [_result("repo_advisory", bump, "BLOCK", f"OSV 未掲載の repo advisory に該当 ({ids})", links)]
    reasons: list[str] = []
    refs: list[str] = []
    for outcome, label in (
        ("possible", "PyPI の名前として読めない package の advisory の範囲に入る"),
        ("unevaluable", "影響範囲を解釈できない"),
    ):
        if by_outcome[outcome]:
            ids, links = _advisory_refs(by_outcome[outcome])
            reasons.append(f"{label} ({ids})")
            refs += links
    if reasons:
        detail = f"{'; '.join(reasons)}。advisory 本文の対象 package と影響範囲を手で照合する"
        return [_result("repo_advisory", bump, "WARN", detail, list(dict.fromkeys(refs)))]
    detail = f"{', '.join(listed)} の公開 repo advisory {len(published)} 件に該当なし"
    if deferred := len(matched) - len(pending):
        detail += f" (OSV 掲載済みの {deferred} 件は advisory 行で判定)"
    return [_result("repo_advisory", bump, "OK", detail)]


def _advisory_refs(found: Sequence[tuple[str, dict[str, Any]]]) -> tuple[str, list[str]]:
    """Displayable IDs and their links; a value not shaped like a GHSA ID is neither shown nor linked."""
    ids: list[str] = []
    links: list[str] = []
    for repo, advisory in found:
        ghsa = str(advisory.get("ghsa_id"))
        valid = GHSA_ID.fullmatch(ghsa) is not None
        ids.append(ghsa if valid else "(表示できない ID)")
        links.append(f"https://github.com/{repo}/security/advisories{f'/{ghsa}' if valid else ''}")
    return _capped(ids, ", "), list(dict.fromkeys(links))[:METADATA_DIFF_LIMIT]


Identity = tuple[str, tuple[tuple[str, str], ...]]  # kind, (field, value) in PUBLISHER_IDENTITY order


@dataclass(frozen=True)
class Provenance:
    """The publishers PyPI names for one file; `complete` is False when any is unreadable or none is named."""

    identities: frozenset[Identity]
    complete: bool


def _publishers(payload: Any) -> list[Any]:
    """The publisher of each attestation bundle; an answer not shaped like the documented one fails the check."""
    bundles = payload.get("attestation_bundles") if isinstance(payload, dict) else None
    if not isinstance(bundles, list) or not all(isinstance(bundle, dict) for bundle in bundles):
        raise ValueError("the provenance answer is not shaped like the Integrity API's")
    return [bundle.get("publisher") for bundle in bundles]


def _publisher_identity(publisher: Any) -> Identity | None:
    """None for an unknown kind or a missing or non-text field: never guessed to match anything."""
    kind = publisher.get("kind") if isinstance(publisher, dict) else None
    if not isinstance(kind, str) or kind not in PUBLISHER_IDENTITY:
        return None
    required, optional = PUBLISHER_IDENTITY[kind]
    values: list[tuple[str, str]] = []
    for name in required + optional:
        value = publisher.get(name)
        if name in optional and value is None:
            value = ""
        if not isinstance(value, str) or (name in required and not value):
            return None
        values.append((name, value))
    return kind, tuple(values)


def _provenance(payload: Any) -> Provenance:
    # A set: bundle order means nothing, and one publisher attesting twice is still that publisher.
    identities = [_publisher_identity(publisher) for publisher in _publishers(payload)]
    known = frozenset(identity for identity in identities if identity is not None)
    return Provenance(known, bool(identities) and None not in identities)


def _publisher_text(provenance: Provenance) -> str:
    def shown(value: str) -> str:
        return value if PUBLISHER_VALUE.fullmatch(value) else "(なし)" if not value else "(表示できない値)"

    texts = sorted(
        f"{kind} " + " ".join(f"{key}={shown(value)}" for key, value in values)
        for kind, values in provenance.identities
    )
    if not provenance.complete:
        texts.append("解釈できない publisher")
    return _capped(texts, ", ")


def _sdist_provenance_urls(fetch_json: FetchJson, bump: Bump) -> tuple[list[str], str | None]:
    """The Integrity API URL of the old and the new sdist, or why there is no pair to compare."""
    if bump.old is None:
        return [], "旧版が無い (新規の依存) ため比較なし"
    urls: list[str] = []
    for label, version in (("旧版", bump.old), ("新版", bump.new)):
        release = _get_or_none(fetch_json, PYPI_RELEASE.format(name=bump.name, version=version))
        if release is None:
            return [], f"{label} {version} の PyPI release が 404 のため比較不能"
        # The file name comes from PyPI, never built from the version; a wheel never stands in.
        sdists = [item["filename"] for item in release["urls"] if item.get("packagetype") == "sdist"]
        if len(sdists) != 1:
            reason = "sdist が無い" if not sdists else f"sdist が {len(sdists)} 件あり 1 つに決められない"
            return [], f"{label} {version} の release に {reason}ため比較不能"
        urls.append(PYPI_INTEGRITY.format(name=bump.name, version=version, filename=quote(sdists[0])))
    return urls, None


def _compare_provenance(old: Provenance | None, new: Provenance | None) -> tuple[str, str]:
    """(status, detail), the status being "WARN" when a publisher the old sdist had is gone or replaced."""
    if old is None:
        if new is None:
            return "both_missing", "旧新とも sdist の provenance を PyPI が提供していない (404)"
        return "first_provided", f"新版の sdist で初めて提供された ({_publisher_text(new)})。旧版は 404"
    if new is None:
        return (
            "WARN",
            f"旧版 sdist にあった provenance が新版で提供されていない (404)。旧版の publisher: {_publisher_text(old)}",
        )
    if not old.complete:
        return "uncomparable", "旧版 sdist の publisher に解釈できないもの (未知の kind か欠けた欄) があるため比較不能"
    if not new.complete:
        detail = (
            "新版 sdist の publisher に解釈できないもの (未知の kind か欠けた欄) があり、"
            f"旧版の publisher ({_publisher_text(old)}) と同じか確認できない"
        )
    elif new.identities != old.identities:
        detail = f"sdist の publisher が変わった: {_publisher_text(old)} → {_publisher_text(new)}"
    else:
        return "same", f"旧新とも sdist の publisher は {_publisher_text(new)}"
    return "WARN", detail


def check_provenance(fetch_json: FetchJson, bump: Bump) -> tuple[list[CheckResult], list[Observation]]:
    """Compare who PyPI says published the old and the new sdist: metadata only, not a signature check.

    Only a publisher the old sdist had going missing or changing is a WARN. Everything else, a match
    included, is an observation: no provenance is not proof of danger, and a match is no proof of safety.
    """
    links, reason = _sdist_provenance_urls(fetch_json, bump)
    if reason is not None:
        status, detail = "uncomparable", reason
    else:
        old, new = (
            None if (payload := _get_or_none(fetch_json, url)) is None else _provenance(payload) for url in links
        )
        status, detail = _compare_provenance(old, new)
    detail = f"{detail}。{PROVENANCE_SCOPE}"
    if status == "WARN":
        return [_result("provenance", bump, "WARN", detail, links)], []
    change = f"{bump.old or '(新規)'} → {bump.new}"
    return [], [Observation("provenance", bump.name, change, status, detail, links)]


def check_python_range(fetch_json: FetchJson, bump: Bump, floor: Version) -> list[CheckResult]:
    spec = fetch_json(PYPI_RELEASE.format(name=bump.name, version=bump.new))["info"].get("requires_python")
    if not spec:
        return [_result("python_range", bump, "OK", "requires_python の宣言なし", [_release_link(bump)])]
    try:
        supported = SpecifierSet(spec).contains(floor, prereleases=True)
    except InvalidSpecifier:
        return [
            _result("python_range", bump, "WARN", f"requires_python {spec!r} を解釈できない", [_release_link(bump)])
        ]
    verdict = "OK" if supported else "BLOCK"
    relation = "⊇" if supported else "⊉"
    return [_result("python_range", bump, verdict, f"{spec} {relation} {floor}", [_release_link(bump)])]


def check_tag_sha(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    repo, tag = _github_repo(bump), _tag(bump)
    if repo is None or tag is None:
        return [_result("tag_sha", bump, "WARN", "照合するタグを特定できない (GitHub 以外か版コメント無し)")]
    resolved = _resolve_tag(fetch_json, repo, tag)
    link = f"https://github.com/{repo}/releases/tag/{tag}"
    if resolved is None:
        return [_result("tag_sha", bump, "BLOCK", f"タグ {tag} が存在しない (404)", [link])]
    if resolved[0] != bump.sha:
        return [
            _result("tag_sha", bump, "BLOCK", f"タグ {tag} は {resolved[0]} を指し、pin {bump.sha} と不一致", [link])
        ]
    return [_result("tag_sha", bump, "OK", f"タグ {tag} → {resolved[0]}", [link])]


def check_tag_exists(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    repo = _github_repo(bump)
    if repo is None:
        return [_result("tag_exists", bump, "WARN", "GitHub 以外のリポジトリのためタグを確認できない")]
    link = f"https://github.com/{repo}/releases/tag/{bump.new}"
    if _resolve_tag(fetch_json, repo, bump.new) is None:
        return [_result("tag_exists", bump, "BLOCK", f"タグ {bump.new} が存在しない (404)", [link])]
    return [_result("tag_exists", bump, "OK", f"タグ {bump.new} が存在する", [link])]


def _is_dependabot_commit(commit: dict[str, Any]) -> bool:
    return (commit.get("author") or {}).get("login") == DEPENDABOT_AUTHOR


def proposed_at(pr: dict[str, Any], commits: list[dict[str, Any]]) -> tuple[datetime, str]:
    """When Dependabot proposed the candidate the PR now holds, and where that time comes from.

    Dependabot rewrites its commit when it moves an open PR to a newer version (#799 was opened
    2026-10-12 and rewritten 2026-10-19), so `created_at` dates an older candidate. A rebase moves
    the commit too, but never past the moment of vetting, so a release still inside the cooldown
    then is never counted as old enough.
    """
    dates = [_parse_time(commit["commit"]["committer"]["date"]) for commit in commits if _is_dependabot_commit(commit)]
    if dates:
        return max(dates), "Dependabot の最新 commit"
    return _parse_time(pr["created_at"]), "PR 作成"


def check_cooldown(fetch_json: FetchJson, bump: Bump, proposed: tuple[datetime, str], days: int) -> list[CheckResult]:
    published = release_published_at(fetch_json, bump)
    if published is None:
        return [_result("cooldown", bump, "WARN", "公開時刻を取得できない (タグ無し、または照合先不明)")]
    # Calendar days in UTC, as Dependabot counts them: #745 was proposed 6.45 days after the release.
    at, source = proposed
    elapsed = (at.date() - published.date()).days
    detail = f"{published.date()} → {at.date()} ({source}) = {elapsed} 日 ({'≥' if elapsed >= days else '<'} {days})"
    return [_result("cooldown", bump, "OK" if elapsed >= days else "WARN", detail, [_release_link(bump)])]


def check_superseded(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    precision = "exact" if bump.kind == "pypi" else _action_precision(bump)[0]
    # `# v7` may pin 7.1.0; without the concrete release every 7.x would count as a successor. A
    # pre-commit `rev:` names one tag, so only its unknown form (not purely numeric, no tag) is unordered.
    if precision == "unknown" or (precision == "floating" and bump.kind == "action"):
        return [_result("superseded", bump, "OK", f"{_imprecise_label(bump, precision)} のため評価不能")]
    later = later_releases(fetch_json, bump)
    if later is None:
        return [_result("superseded", bump, "OK", "release が無いため評価不能")]
    published = release_published_at(fetch_json, bump)
    if published is not None:
        # A higher line released before this backport (2.0.0 months before 1.9.1) is not a successor.
        later = [(version, at) for version, at in later if at >= published]
    if not later:
        return [_result("superseded", bump, "OK", "後続 release 無し")]
    if published is None:
        # Later releases exist but none can be ruled out without the candidate's own date.
        return [_result("superseded", bump, "WARN", "当該版の公開時刻を取得できず評価不能")]
    window = timedelta(days=SUPERSEDED_WINDOW_DAYS)
    quick = [(version, at) for version, at in later if at - published <= window]
    first_version, first_at = (quick or later)[0]
    gap = (first_at - published).total_seconds() / 86400
    detail = f"{first_version} が {first_at:%Y-%m-%dT%H:%MZ} ({gap:.1f} 日後)"
    if quick:
        return [_result("superseded", bump, "WARN", f"{detail}。{SUPERSEDED_WINDOW_DAYS} 日以内の後続 {len(quick)} 件")]
    return [_result("superseded", bump, "OK", f"{detail}。{SUPERSEDED_WINDOW_DAYS} 日の窓の外")]


def check_major_bump(bump: Bump) -> list[CheckResult]:
    old, new = _parse_version(bump.old), _parse_version(bump.new)
    if old is None or new is None:
        return [_result("major_bump", bump, "OK", "旧版が無いか版として解釈できないため比較なし")]
    if old.major != new.major:
        return [_result("major_bump", bump, "WARN", f"major {old.major} → {new.major}")]
    return [_result("major_bump", bump, "OK", f"major {new.major} のまま")]


def _required(spec: dict[str, Any]) -> bool:
    # _MetadataLoader leaves YAML booleans as text; any other type is not a boolean, and reading
    # it as "optional" would report a contract change as compatible.
    value = spec.get("required", "false")
    if not isinstance(value, str) or value.strip().lower() not in {"true", "false"}:
        raise ValueError("required is not a boolean")
    return value.strip().lower() == "true"


def _metadata_name(name: Any) -> str:
    return str(name) if METADATA_NAME.fullmatch(str(name)) else "(表示できない名前)"


def _digest(value: Any) -> str:
    """A bounded stand-in for third-party text, equal exactly when the type and the value are."""
    return hashlib.sha256(f"{type(value).__name__}:{value!r}".encode()).hexdigest()


def _capped(items: Sequence[str], separator: str) -> str:
    shown = separator.join(items[:METADATA_DIFF_LIMIT])
    rest = len(items) - METADATA_DIFF_LIMIT
    return f"{shown}{separator}ほか {rest} 件" if rest > 0 else shown


def _mapping(metadata: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    value = metadata.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} is not a mapping")
    if not all(isinstance(spec, dict) for spec in value.values()):
        raise ValueError(f"an entry of {key} is not a mapping")
    return {str(name): spec for name, spec in value.items()}


class _MetadataLoader(yaml.SafeLoader):
    """SafeLoader without YAML 1.1 booleans: `on` and `yes` are distinct input / output ids."""


_MetadataLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _parse_action_metadata(text: str) -> dict[str, Any] | None:
    """The metadata, or None when a field the comparison reads is missing or of the wrong type."""
    try:
        metadata = yaml.load(text, Loader=_MetadataLoader)
        # Fail closed on every field the comparison reads: a wrong type is "unreadable", never "unchanged".
        using = metadata["runs"]["using"]
        if not isinstance(using, str) or not RUNTIME_NAME.fullmatch(using):
            return None
        for spec in _mapping(metadata, "inputs").values():
            _required(spec)
            # A default is a scalar the runner reads as text; a sequence or mapping cannot be compared.
            if isinstance(spec.get("default"), (dict, list, set)):
                return None
        _mapping(metadata, "outputs")
    except (yaml.YAMLError, TypeError, KeyError, ValueError):
        return None
    return metadata


UNREADABLE_METADATA = "# Unreadable action metadata; --record keeps none of its text.\n"


def _trimmed_name(name: str) -> str:
    # A name the report would not show anyway becomes a digest: still distinct, never prose.
    return name if METADATA_NAME.fullmatch(name) else f"({_digest(name)})"


def _trim_action_metadata(text: str) -> str:
    """Only what _contract_changes reads, so descriptions and branding stay out of the fixture.

    Every kept value is bounded: a default is compared, never reported, so its digest replays the
    same comparison without storing third-party text of any length.
    """
    metadata = _parse_action_metadata(text)
    if metadata is None:
        return UNREADABLE_METADATA
    inputs: dict[str, dict[str, str]] = {}
    for name, spec in _mapping(metadata, "inputs").items():
        kept_spec = {"required": "true" if _required(spec) else "false"}
        if "default" in spec:
            kept_spec["default"] = _digest(spec["default"])
        inputs[_trimmed_name(name)] = kept_spec
    kept = {
        "runs": {"using": metadata["runs"]["using"]},
        "inputs": inputs,
        "outputs": {_trimmed_name(name): {} for name in _mapping(metadata, "outputs")},
    }
    return str(yaml.safe_dump(kept, sort_keys=False))


def _load_action_metadata(
    fetch_text: FetchText, change: ActionPinChange, sha: str
) -> tuple[str | None, dict[str, Any] | None]:
    """(path, metadata) at one pinned commit: path None when neither file exists, metadata None when unreadable."""
    for filename in ACTION_METADATA_FILES:
        path = f"{change.subpath}/{filename}" if change.subpath else filename
        text = file_at(fetch_text, change.repo, path, sha)
        if text is not None:
            return path, _parse_action_metadata(text)
    return None, None


def _contract_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What a caller pinned to the old commit may trip over; additions it can ignore are not listed.

    `required: true` alone does not make the runner reject a missing input, and an undeclared
    output can still be set, so these are reasons to read the release notes, not proof of breakage.
    """
    reasons: list[str] = []
    if (old_using := str(old["runs"]["using"])) != (new_using := str(new["runs"]["using"])):
        reasons.append(f"runs.using {old_using} → {new_using}")
    old_inputs, new_inputs = _mapping(old, "inputs"), _mapping(new, "inputs")
    for name, spec in new_inputs.items():
        required = _required(spec)
        default = "default あり" if "default" in spec else "default なし、未指定時の扱いは action 次第"
        if name not in old_inputs:
            if required:
                reasons.append(f"必須 input {_metadata_name(name)} を追加 ({default})")
            continue
        old_spec = old_inputs[name]
        if required and not _required(old_spec):
            reasons.append(f"input {_metadata_name(name)} が任意 → 必須 ({default})")
        # A caller that omits the input gets the default, so adding, changing or dropping it changes
        # what that caller passes. The values are third-party text and never reach the report.
        # Compared with their YAML types (`'1'` and `1` differ), through the digest a recording keeps.
        had, has = "default" in old_spec, "default" in spec
        if had and has and _digest(old_spec["default"]) != _digest(spec["default"]):
            change = "変更"
        else:
            change = "削除" if had and not has else "追加" if has and not had else ""
        if change:
            reasons.append(f"input {_metadata_name(name)} の default を{change} ({'必須' if required else '任意'})")
    for name, spec in old_inputs.items():
        if name not in new_inputs:
            # A caller that passed it is now ignored; one that relied on its default gets nothing.
            default = "default あり" if "default" in spec else "default なし"
            reasons.append(f"input {_metadata_name(name)} を削除 ({default})")
    new_outputs = _mapping(new, "outputs")
    if removed := [_metadata_name(name) for name in _mapping(old, "outputs") if name not in new_outputs]:
        reasons.append(f"output を削除: {_capped(removed, ', ')}")
    return reasons


def _uncomparable(change: ActionPinChange) -> str | None:
    parts = change.subpath.split("/") if change.subpath else []
    if any(part in {"", ".", ".."} for part in parts):
        return "サブパスを解釈できないため比較不能"
    if REUSABLE_WORKFLOW.fullmatch(change.subpath):
        return "再利用ワークフローは action metadata を持たないため比較不能"
    return None


def check_action_metadata(fetch_text: FetchText, change: ActionPinChange) -> list[CheckResult]:
    """Compare `runs.using`, required inputs and outputs between the old and the new pinned commit."""

    def row(verdict: str, detail: str, links: Sequence[str] = ()) -> list[CheckResult]:
        transition = f"{change.old_version or '(新規)'} → {change.new_version}"
        return [CheckResult("action_metadata", change.action, verdict, detail, list(links), transition)]

    if reason := _uncomparable(change):
        return row("WARN", reason)
    # Checked here, after the path checks above, so the loop below sees a str old_sha.
    if change.old_sha is None:
        return row("WARN", "置き換えた旧 pin が無いため比較不能 (新規追加など)")
    loaded: list[tuple[str, dict[str, Any]]] = []
    links: list[str] = []
    for label, sha in (("旧", change.old_sha), ("新", change.new_sha)):
        path, metadata = _load_action_metadata(fetch_text, change, sha)
        if path is None:
            return row("WARN", f"{label} pin {sha[:SHORT_SHA]} に action.yml / action.yaml が無い (404)", links)
        links.append(f"https://github.com/{change.repo}/blob/{sha}/{path}")
        if metadata is None:
            return row("WARN", f"{label} pin の {path} を解釈できないため比較不能", links)
        loaded.append((path, metadata))
    (_, old), (_, new) = loaded
    if reasons := _contract_changes(old, new):
        return row("WARN", _capped(reasons, "; "), links)
    detail = f"runs.using {new['runs']['using']} のまま、必須 input の追加・output の削除なし"
    return row("OK", detail, links)


def _expected(path: str, ecosystem: str | None) -> bool:
    patterns = EXPECTED_FILES[ecosystem] if ecosystem else [p for group in EXPECTED_FILES.values() for p in group]
    # PurePosixPath.match anchors a relative pattern at the right only (`vendor/.github/workflows/x.yml`
    # would match), so the part count pins it to the whole path.
    parts = PurePosixPath(path).parts
    return any(
        len(parts) == len(PurePosixPath(pattern).parts) and PurePosixPath(path).match(pattern) for pattern in patterns
    )


def check_pr_hygiene(
    pr: dict[str, Any], files: list[str], commits: list[dict[str, Any]], ecosystem: str | None, bumps: list[Bump]
) -> list[CheckResult]:
    problems: list[str] = []
    if not bumps:
        problems.append("bump を検出できなかった (SHA pin 以外の uses: など。変更を手で確認する)")
    if ecosystem is None:
        problems.append("head.ref の接頭辞がどのエコシステムにも一致しない")
    if extra := [path for path in files if not _expected(path, ecosystem)]:
        problems.append(f"期待外のファイル: {', '.join(extra)}")
    humans = [c for c in commits if not _is_dependabot_commit(c)]
    if humans:
        problems.append(f"Dependabot 以外の commit {len(humans)} 件")
    if problems:
        return [_result("pr_hygiene", None, "WARN", "; ".join(problems))]
    prefix = next(p for p, eco in ECOSYSTEM_BY_PREFIX.items() if eco == ecosystem)
    detail = f"{prefix}、{', '.join(files)}、commit {len(commits)}"
    return [_result("pr_hygiene", None, "OK", detail, [pr.get("html_url", "")] if pr.get("html_url") else [])]


def check_ci_green(fetch_json: FetchJson, repo: str, head_sha: str) -> list[CheckResult]:
    runs = _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/commits/{head_sha}/check-runs", key="check_runs")
    runs = [run for run in runs if run["name"] != SELF_CHECK_NAME]
    if not runs:
        return [_result("ci_green", None, "WARN", "check run が 0 件")]
    pending = [run for run in runs if run["status"] != "completed" or run.get("conclusion") not in GREEN_CONCLUSIONS]
    if pending:
        states = ", ".join(f"{run['name']}: {run.get('conclusion') or run['status']}" for run in pending)
        return [_result("ci_green", None, "WARN", f"未完了または失敗: {states}")]
    counts = Counter(run["conclusion"] for run in runs)
    return [_result("ci_green", None, "OK", " + ".join(f"{name} ×{n}" for name, n in sorted(counts.items())))]


# --- orchestration -------------------------------------------------------------------------

PARSERS: dict[str, tuple[Callable[[str | None, str | None], list[Bump]], Callable[[str], bool]]] = {
    "uv": (uv_lock_bumps, lambda path: path == "uv.lock"),
    "github-actions": (workflow_bumps, lambda path: _expected(path, "github-actions")),
    "pre-commit": (pre_commit_bumps, lambda path: path == ".pre-commit-config.yaml"),
}


def _collect_bumps(
    fetch_text: FetchText, repo: str, files: list[str], ecosystem: str | None, before_ref: str, after_ref: str
) -> list[Bump]:
    bumps: list[Bump] = []
    for name in [ecosystem] if ecosystem else list(PARSERS):
        parse, owns = PARSERS[name]
        for path in files:
            if owns(path):
                bumps.extend(
                    parse(file_at(fetch_text, repo, path, before_ref), file_at(fetch_text, repo, path, after_ref))
                )
    return _dedupe(bumps)


def _collect_action_changes(
    fetch_text: FetchText, repo: str, files: list[str], ecosystem: str | None, before_ref: str, after_ref: str
) -> list[ActionPinChange]:
    if ecosystem not in {None, "github-actions"}:
        return []
    changes: list[ActionPinChange] = []
    for path in files:
        if _expected(path, "github-actions"):
            changes += action_pin_changes(
                file_at(fetch_text, repo, path, before_ref), file_at(fetch_text, repo, path, after_ref)
            )
    # #745 moved the same pin in five workflows; compare it once.
    return list(dict.fromkeys(changes))


def vet_pull_request(
    fetch_json: FetchJson, fetch_text: FetchText, post_json: PostJson, fetch_list: FetchList, repo: str, number: int
) -> PullRequestVerdict:
    fetch_json = _memoize(fetch_json)
    # Workflow files are parsed twice (bumps per repository, metadata per action path).
    fetch_text = _memoize(fetch_text)
    # Packages of one repository (`types-*` stubs in python/typeshed) share its advisories.
    fetch_list = _memoize(fetch_list)
    pr = load_pull_request(fetch_json, repo, number)
    if pr["user"]["login"] != DEPENDABOT_AUTHOR:
        raise NotDependabotPullRequestError(
            f"PR #{number} は Dependabot の PR ではない (author: {pr['user']['login']})"
        )
    head_sha = pr["head"]["sha"]
    ecosystem = detect_ecosystem(pr["head"]["ref"])
    files = changed_files(fetch_json, repo, number)
    commits = pull_request_commits(fetch_json, repo, number)
    before_ref = merge_base(fetch_json, repo, pr["base"]["sha"], head_sha)
    bumps = _collect_bumps(fetch_text, repo, files, ecosystem, before_ref, head_sha)
    action_changes = _collect_action_changes(fetch_text, repo, files, ecosystem, before_ref, head_sha)
    proposed = proposed_at(pr, commits)
    dependabot_yml = file_at(fetch_text, repo, ".github/dependabot.yml", head_sha)
    floor = (
        requires_python_floor(file_at(fetch_text, repo, "pyproject.toml", head_sha))
        if any(bump.kind == "pypi" for bump in bumps)
        else None
    )

    checks: list[CheckResult] = []
    observations: list[Observation] = []
    for bump in bumps:
        if bump.kind == "pypi":
            checks += check_yanked(fetch_json, bump)
        if bump.kind in {"pypi", "action"}:
            checks += check_advisory(fetch_json, post_json, bump)
        if bump.kind == "pypi":
            checks += check_repository_advisory(fetch_json, post_json, fetch_list, bump)
        if bump.kind == "pypi" and floor is not None:
            checks += check_python_range(fetch_json, bump, floor)
        if bump.kind == "action" or (bump.kind == "pre-commit" and bump.sha):
            checks += check_tag_sha(fetch_json, bump)
        if bump.kind == "pre-commit" and not bump.sha:
            checks += check_tag_exists(fetch_json, bump)
        days = cooldown_days(dependabot_yml, ECOSYSTEM_BY_KIND[bump.kind])
        checks += check_cooldown(fetch_json, bump, proposed, days)
        checks += check_superseded(fetch_json, bump)
        checks += check_major_bump(bump)
        if bump.kind == "pypi":
            # After `superseded`: --record bounds the release history it keeps by the lowest release
            # fetched so far, and this fetches the old one.
            warned, observed = check_provenance(fetch_json, bump)
            checks += warned
            observations += observed
    for change in action_changes:
        checks += check_action_metadata(fetch_text, change)
    checks += check_pr_hygiene(pr, files, commits, ecosystem, bumps)
    checks += check_ci_green(fetch_json, repo, head_sha)
    return PullRequestVerdict(number, ecosystem, head_sha, bumps, checks, observations)


def vet_pull_requests(
    fetchers: Fetchers, repo: str, numbers: Sequence[int]
) -> tuple[list[PullRequestVerdict], list[VettingFailure]]:
    """Vet each PR on its own: one PR that cannot be checked must not discard the others' verdicts.

    The full error goes to stderr only; the reports name the PR and the exception type.
    """
    verdicts: list[PullRequestVerdict] = []
    failures: list[VettingFailure] = []
    for number in numbers:
        try:
            verdicts.append(vet_pull_request(*fetchers, repo, number))
        except NotDependabotPullRequestError as exc:
            print(str(exc), file=sys.stderr)
            failures.append(VettingFailure(number, str(exc)))
        except Exception as exc:  # exit 1 means BLOCK, so even a bug must surface as a failure
            print(f"PR #{number} の検査自体が失敗した: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures.append(VettingFailure(number, f"検査自体が失敗した ({type(exc).__name__})"))
    return verdicts, failures


# --- reports -------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def _link(url: str) -> str:
    # Repository and tag names in URLs come from the PR; percent-encode anything that could
    # close the link target or the table cell.
    return quote(url, safe=":/?#=&%@+,;~")


def verdict_line(verdict: PullRequestVerdict) -> str:
    level = verdict.verdict
    if level == "OK":
        return "判定: OK"
    return f"判定: {level} ({sum(check.verdict == level for check in verdict.checks)} 件)"


def render_pull_request(verdict: PullRequestVerdict) -> str:
    # PR titles and bodies are deliberately absent: agents read this report (AGENTS.md).
    lines = [
        f"## PR #{verdict.number} ({verdict.ecosystem or 'unknown'})",
        "",
        f"head: `{verdict.head_sha}`",
        "",
        "| 依存 | 旧 → 新 | 検査 | 結果 | 根拠 |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows = [_row(check) for check in verdict.checks]
    header = ["", OBSERVATION_HEADER, ""] if verdict.observations else []
    budget = REPORT_BODY_LIMIT - REPORT_TAIL_MARGIN - len("\n".join(lines + header))
    kept: set[int] = set()
    for index in sorted(range(len(rows)), key=lambda index: VERDICT_ORDER.index(verdict.checks[index].verdict)):
        if len(rows[index]) + 1 <= budget:
            kept.add(index)
            budget -= len(rows[index]) + 1
    lines += [row for index, row in enumerate(rows) if index in kept]
    if omitted := Counter(check.verdict for index, check in enumerate(verdict.checks) if index not in kept):
        counts = " / ".join(f"{level} {omitted[level]}" for level in VERDICT_ORDER if omitted[level])
        lines += ["", f"表に載せきれない {omitted.total()} 行を省略 ({counts})。全件は --json で確認する"]
    # Observations carry no verdict, so they only take what the rows left: none can push a row out.
    notes: list[str] = []
    for note in map(_note, verdict.observations):
        if len(note) + 1 <= budget:
            notes.append(note)
            budget -= len(note) + 1
    lines += header + notes
    if dropped := len(verdict.observations) - len(notes):
        lines += ["", f"載せきれない補助観測 {dropped} 件を省略。全件は --json で確認する"]
    lines += ["", verdict_line(verdict)]
    return "\n".join(lines) + "\n"


def _links(links: Sequence[str]) -> str:
    return " ".join(f"[{index}]({_link(link)})" for index, link in enumerate(links, start=1))


def _row(check: CheckResult) -> str:
    evidence = f"{_cell(check.detail)} {_links(check.links)}".strip()
    cells = [_cell(check.dependency), _cell(check.change), _cell(check.check_id), _cell(check.verdict), evidence]
    return f"| {' | '.join(cells)} |"


def _note(item: Observation) -> str:
    label = OBSERVATION_LABELS[item.status]
    return f"- {_cell(item.dependency)} {_cell(item.change)} {label}: {_cell(item.detail)} {_links(item.links)}".strip()


def render_failure(failure: VettingFailure) -> str:
    return f"## PR #{failure.number} (検査失敗)\n\n{_cell(failure.reason)}。判定なし (OK でも BLOCK でもない)\n"


def render_markdown(verdicts: Sequence[PullRequestVerdict], failures: Sequence[VettingFailure] = ()) -> str:
    if not verdicts and not failures:
        return "# Dependabot PR の事前検証\n\nopen な Dependabot PR はない。\n"
    sections = [render_pull_request(verdict) for verdict in verdicts] + [render_failure(item) for item in failures]
    return "# Dependabot PR の事前検証\n\n" + "\n".join(sections)


def render_json(
    verdicts: Sequence[PullRequestVerdict], failures: Sequence[VettingFailure] = ()
) -> list[dict[str, Any]]:
    # A failed PR keeps the shape of a vetted one (no bumps, no checks) so `.[].checks[]` still reads it.
    failed: list[dict[str, Any]] = [
        {
            "pr": item.number,
            "ecosystem": None,
            "head_sha": None,
            "verdict": "ERROR",
            "error": item.reason,
            "bumps": [],
            "checks": [],
            "observations": [],
        }
        for item in failures
    ]
    return [
        {
            "pr": verdict.number,
            "ecosystem": verdict.ecosystem,
            "head_sha": verdict.head_sha,
            "verdict": verdict.verdict,
            "bumps": [{key: value for key, value in asdict(bump).items() if key != "sha"} for bump in verdict.bumps],
            "checks": [
                {
                    "id": check.check_id,
                    "dependency": check.dependency,
                    "change": check.change,
                    "verdict": check.verdict,
                    "detail": check.detail,
                    "links": check.links,
                }
                for check in verdict.checks
            ],
            # Not checks: they never count towards `verdict`.
            "observations": [
                {
                    "id": item.observation_id,
                    "dependency": item.dependency,
                    "change": item.change,
                    "status": item.status,
                    "detail": item.detail,
                    "links": item.links,
                }
                for item in verdict.observations
            ],
        }
        for verdict in verdicts
    ] + failed


def resolve_repo(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return github_repository(remote)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int, action="append", help="検査する PR 番号 (複数可)")
    target.add_argument("--all-open", action="store_true", help="open な Dependabot PR をすべて検査する")
    parser.add_argument("--repo", help="owner/name (既定: GITHUB_REPOSITORY → git remote origin)")
    parser.add_argument("--report", type=Path, help="Markdown レポートの書き出し先")
    parser.add_argument("--json", type=Path, help="JSON レポートの書き出し先")
    parser.add_argument("--comment", action="store_true", help="各 PR にレポートをコメントする (手元専用)")
    replay = parser.add_mutually_exclusive_group()
    replay.add_argument("--fixture", type=Path, help="録画済み応答でオフライン再生する")
    replay.add_argument("--record", type=Path, help="実際の応答を録画しながら検査する")
    args = parser.parse_args(argv)

    if args.comment and os.environ.get("GITHUB_ACTIONS") == "true":
        # CI runs with a read-only token on Dependabot PRs; posting is a deliberate local act.
        print("--comment は GitHub Actions 内では使えない (手元から明示的に実行する)", file=sys.stderr)
        return 2
    repo = resolve_repo(args.repo)
    if repo is None:
        print("--repo、GITHUB_REPOSITORY、git remote origin のいずれからもリポジトリを特定できない", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN") or None

    # Setup, output and posting fail the same way as the checks: exit 1 means BLOCK, so any
    # other failure must surface as 2 (a broken fixture or an unwritable report is not a verdict).
    try:
        if args.fixture:
            fetchers = make_fixture_fetchers(args.fixture)
        elif args.record:
            fetchers = make_recording_fetchers(token, args.record)
        else:
            fetchers = make_fetchers(token)
        numbers = args.pr or open_dependabot_prs(fetchers[0], repo)
        verdicts, failures = vet_pull_requests(fetchers, repo, numbers)
        if failures and not verdicts:
            # Nothing was vetted: write no report, which the workflow's `[ -f report.md ]` guard expects.
            return 2
        markdown = render_markdown(verdicts, failures)
        print(markdown, end="")
        if args.report:
            args.report.write_text(markdown, encoding="utf-8")
        if args.json:
            report = json.dumps(render_json(verdicts, failures), ensure_ascii=False, indent=2) + "\n"
            args.json.write_text(report, encoding="utf-8")
        if args.comment:
            post = make_poster(token)
            for verdict in verdicts:
                url = f"{GITHUB_API}/repos/{repo}/issues/{verdict.number}/comments"
                post(url, {"body": render_pull_request(verdict)})
    except (requests.RequestException, ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as exc:
        print(f"検査自体が失敗した: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # exit 1 means BLOCK, so even a bug must surface as 2
        print(f"検査自体が想定外の例外で失敗した: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    # A PR that could not be checked is neither OK nor BLOCK, whatever the others say.
    blocked = any(verdict.verdict == "BLOCK" for verdict in verdicts)
    return 2 if failures else 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
