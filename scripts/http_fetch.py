"""HTTP transport shared by the Dependabot scripts (issue #765).

`vet_dependabot_prs.py` and `check_dependency_pipeline.py` call the same mix of hosts
(api.github.com, pypi.org, api.osv.dev, raw.githubusercontent.com) with one token. Only the
transport is shared: the headers, where the token may go, and the timeout. How a failed
response is reported stays with each caller, because the two disagree on purpose: the
watchdog keeps third-party error bodies out of its exceptions (they reach a tracking issue).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import requests

GITHUB_API_HOST = "api.github.com"
TIMEOUT_SECONDS = 30
ERROR_DETAIL_CHARS = 200


def request_headers(url: str, token: str | None, *, accept: str, user_agent: str) -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": user_agent}
    # Gate the credential on the host rather than on the caller: a future check cannot leak
    # it to pypi.org or raw.githubusercontent.com by picking the wrong fetcher.
    if token and urlsplit(url).hostname == GITHUB_API_HOST:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get(url: str, token: str | None, *, accept: str, user_agent: str) -> requests.Response:
    """GET without judging the status; each caller decides what a failure says and where."""
    headers = request_headers(url, token, accept=accept, user_agent=user_agent)
    return requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)


def status_line(response: requests.Response, url: str) -> str:
    return f"{response.status_code} {response.reason} for {url}"


def error_detail(response: requests.Response) -> str:
    """The start of a third-party error body on one line, for whichever channel the caller allows."""
    return " ".join(response.text.split())[:ERROR_DETAIL_CHARS]
