#!/usr/bin/env bash
# List data/raw/*.csv files added or changed since a base commit, one repo-relative path per line.
#
# Usage: scripts/list_changed_raw_files.sh <base-commit>
#
# Covers commits after <base-commit>, staged and unstaged edits, and untracked new files.
# Deleted files and byte-identical rewrites are not listed; nested paths such as
# data/raw/.metadata/*.csv are excluded. Exits 2 when <base-commit> is empty or unknown so
# callers can fail closed instead of treating "no output" as "nothing changed".
# Written for bash 3.2 (macOS /bin/bash) so the tests can run locally.
set -euo pipefail

base="${1:-}"
if [ -z "$base" ]; then
  echo "usage: $0 <base-commit>" >&2
  exit 2
fi
if ! git rev-parse --verify --quiet "${base}^{commit}" > /dev/null; then
  echo "error: unknown base commit: $base" >&2
  exit 2
fi

cd "$(git rev-parse --show-toplevel)"

# :(glob) keeps * from matching "/", so data/raw/.metadata/*.csv stays out.
# --no-renames reports a rename's destination as added instead of an R entry that AM filters out.
{
  git -c core.quotePath=false diff --no-renames --name-only --diff-filter=AM "$base" -- ':(glob)data/raw/*.csv'
  git -c core.quotePath=false ls-files --others --exclude-standard -- ':(glob)data/raw/*.csv'
} | LC_ALL=C sort -u
