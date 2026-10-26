#!/usr/bin/env bash
# Open or update one notification issue per pipeline anomaly kind (issue #733).
#
# Runs as the `Notify pipeline anomaly` step (if: always()) after `Create Pull Request`, so a
# failure here turns only this step red and never affects the data PR or auto-merge.
#
# Inputs (env):
#   PIPELINE_NAME             daily | weekly | manual | process
#   JOB_STATUS                ${{ job.status }} at this step (success | failure | cancelled)
#   FETCH_STATUS              success | failed (fetch pipelines)
#   PROCESS_RESULT            success | failed | skipped | dry_run
#   RAW_CHANGED_COUNT         number | unknown (raw change detection failed)
#   PROCESSING_COVERAGE_STATUS, AUTO_MERGE_GATE_STATUS, AUTO_MERGE_BLOCKERS, AUTO_MERGE_SETUP_FAILED, PR_URL
#   GITHUB_REPOSITORY, GITHUB_SERVER_URL, GITHUB_RUN_ID
#   GH_TOKEN (or GITHUB_TOKEN)
#
# Kinds, highest priority first: fetch_failed > process_failed > job_failed >
# auto_merge_setup_failed > auto_merge_blocked. The title carries only the highest kind and no
# date, so a repeated failure comments on the open issue instead of opening a new one.
# Written for bash 3.2 as well (local macOS runs the tests): no mapfile, no associative arrays.
set -euo pipefail

notification_error() {
  echo "::error title=Pipeline notification failed::$1"
  exit 1
}

case "${PIPELINE_NAME:-}" in
  daily | weekly | manual) PIPELINE_LABEL="data-collection" ;;
  process) PIPELINE_LABEL="data-processing" ;;
  *) notification_error "PIPELINE_NAME must be daily, weekly, manual or process" ;;
esac

KINDS=""
add_kind() {
  KINDS="${KINDS:+${KINDS} }$1"
}

[ "${FETCH_STATUS:-}" = "failed" ] && add_kind fetch_failed
[ "${PROCESS_RESULT:-}" = "failed" ] && add_kind process_failed
# A cancellation alone is not reported: process-data cancels superseded runs on purpose.
[ "${JOB_STATUS:-}" = "failure" ] && add_kind job_failed
[ "${AUTO_MERGE_SETUP_FAILED:-}" = "true" ] && add_kind auto_merge_setup_failed
# Without a PR the blocked gate already fails the job (`Fail when gate is blocked without PR`).
if [ "${AUTO_MERGE_GATE_STATUS:-}" = "blocked" ] && [ -n "${PR_URL:-}" ]; then
  add_kind auto_merge_blocked
fi

if [ -z "$KINDS" ]; then
  echo "No pipeline anomaly"
  exit 0
fi

PRIMARY_KIND="${KINDS%% *}"

if [ -z "${GH_TOKEN:-}" ]; then
  if [ -z "${GITHUB_TOKEN:-}" ]; then
    notification_error "${PRIMARY_KIND} was detected but neither GH_TOKEN nor GITHUB_TOKEN is set"
  fi
  export GH_TOKEN="$GITHUB_TOKEN"
fi
if [ -z "${GITHUB_REPOSITORY:-}" ]; then
  notification_error "${PRIMARY_KIND} was detected but GITHUB_REPOSITORY is not set"
fi

RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-unknown}"
export ISSUE_TITLE="🚨 [${PIPELINE_NAME}] データパイプライン異常: ${PRIMARY_KIND}"

BODY_FILE=$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/pipeline_issue.XXXXXX")
trap 'rm -f "$BODY_FILE"' EXIT

kind_action() {
  case "$1" in
    fetch_failed)
      echo "- \`fetch_failed\`: logs artifact の取得ログ (\`*_<FETCH_TIMESTAMP>.log\`) で失敗したデータ種別とエラーを確認し、データ源の復旧後に再実行する"
      ;;
    process_failed)
      if [ "${RAW_CHANGED_COUNT:-}" = "unknown" ]; then
        echo "- \`process_failed\`: raw の変更検出 (\`scripts/list_changed_raw_files.sh\`) に失敗したため、変換は実行していない (処理ログは作られない)。ワークフローログの \`Raw change detection failed\` 注記を確認する"
      else
        echo "- \`process_failed\`: logs artifact の処理ログ (\`process_*.log\` / \`console_*.log\`) でエラーを確認し、修正後に再処理する"
      fi
      ;;
    job_failed)
      echo "- \`job_failed\`: ワークフロー実行で失敗したステップとその注記を確認する"
      ;;
    auto_merge_setup_failed)
      echo "- \`auto_merge_setup_failed\`: \`gh pr merge --auto\` に失敗した。PR を確認して手動でマージする"
      ;;
    auto_merge_blocked)
      echo "- \`auto_merge_blocked\`: ゲートが自動マージを止めた (ブロック理由: ${AUTO_MERGE_BLOCKERS:-unknown})。PR の「自動マージゲート」を確認し、問題が無ければ手動でマージする"
      ;;
  esac
}

{
  echo "## 🚨 データパイプライン異常 (${PIPELINE_NAME})"
  echo ""
  echo "- **主な種別**: \`${PRIMARY_KIND}\`"
  KIND_LIST=""
  for kind in $KINDS; do
    KIND_LIST="${KIND_LIST:+${KIND_LIST}, }\`${kind}\`"
  done
  echo "- **検出した種別**: ${KIND_LIST}"
  echo "- **ワークフロー実行**: ${RUN_URL}"
  echo "- **Pull Request**: ${PR_URL:-なし}"
  echo ""
  echo "### ステータス"
  echo ""
  echo "| 項目 | 値 |"
  echo "| --- | --- |"
  echo "| ジョブ (job.status) | ${JOB_STATUS:-unknown} |"
  echo "| 取得 (FETCH_STATUS) | ${FETCH_STATUS:-not_applicable} |"
  echo "| 処理 (PROCESS_RESULT) | ${PROCESS_RESULT:-not_run} |"
  echo "| raw 変更件数 (RAW_CHANGED_COUNT) | ${RAW_CHANGED_COUNT:-not_applicable} |"
  echo "| 処理カバレッジ (PROCESSING_COVERAGE_STATUS) | ${PROCESSING_COVERAGE_STATUS:-not_applicable} |"
  echo "| 自動マージ判定 (AUTO_MERGE_GATE_STATUS) | ${AUTO_MERGE_GATE_STATUS:-not_evaluated} |"
  echo "| ブロック理由 (AUTO_MERGE_BLOCKERS) | ${AUTO_MERGE_BLOCKERS:-not_evaluated} |"
  echo "| auto-merge 設定失敗 (AUTO_MERGE_SETUP_FAILED) | ${AUTO_MERGE_SETUP_FAILED:-false} |"
  echo ""
  echo "### 対応"
  echo ""
  for kind in $KINDS; do
    kind_action "$kind"
  done
  echo ""
  echo "同じ種別の異常が続く間は、この issue にコメントが追記される。解消を確認したら手動でクローズする。"
} > "$BODY_FILE"

# Exact-title lookup, as in dependency-pipeline-watchdog.yml: the title reaches jq through env.
# --limit 200: the default 30 can push this issue out of range behind other automated issues.
EXISTING=$(gh issue list \
  --repo "$GITHUB_REPOSITORY" \
  --state open \
  --label automated \
  --limit 200 \
  --json number,title \
  --jq 'map(select(.title == env.ISSUE_TITLE)) | .[0].number // empty')

if [ -n "$EXISTING" ]; then
  gh issue comment "$EXISTING" --repo "$GITHUB_REPOSITORY" --body-file "$BODY_FILE"
  echo "::notice title=Pipeline anomaly notified::${PRIMARY_KIND}: commented on issue #${EXISTING}"
else
  gh issue create \
    --repo "$GITHUB_REPOSITORY" \
    --title "$ISSUE_TITLE" \
    --body-file "$BODY_FILE" \
    --label "bug,automated,${PIPELINE_LABEL}"
  echo "::notice title=Pipeline anomaly notified::${PRIMARY_KIND}: opened a new issue"
fi
