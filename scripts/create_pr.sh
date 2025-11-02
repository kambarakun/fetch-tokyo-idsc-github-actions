#!/bin/bash
set -e

# ============================================================
# PR作成用の共通スクリプト
#
# 説明:
#   GitHub Actionsワークフローから呼び出されるPR作成の共通処理。
#   ブランチ作成、コミット、プッシュ、PR作成、自動マージ設定を実行。
#
# 使用方法:
#   scripts/create_pr.sh <workflow_name> <workflow_display_name>
#
# 引数:
#   $1 workflow_name         - ワークフロー識別子 (fetch-data-daily|fetch-data-weekly|fetch-data)
#   $2 workflow_display_name - PR本文に表示するワークフロー名
#
# 必須環境変数:
#   - GITHUB_TOKEN      : GitHub APIアクセス用トークン
#   - CURRENT_DATE      : 実行日 (YYYY-MM-DD形式)
#   - FETCH_TIMESTAMP   : 実行タイムスタンプ (英数字、アンダースコア、ハイフンのみ)
#   - GITHUB_RUN_ID     : GitHub ActionsのRun ID (数値)
#
# オプション環境変数（ワークフロー別）:
#   fetch-data-daily:
#     - CURRENT_YEAR, CURRENT_WEEK, CURRENT_MONTH
#     - PREVIOUS_WEEK, PREVIOUS_MONTH
#   fetch-data-weekly:
#     - START_YEAR, END_YEAR, CURRENT_WEEK
#     - CHECK_PREVIOUS_YEAR, PREVIOUS_YEAR
#     - VALIDATION_SUCCESS (true|false)
#   fetch-data:
#     - START_YEAR, END_YEAR
#     - DATA_TYPES, SKIP_EXISTING
#     - VERIFY_CONTINUITY, CONTINUITY_VALID
#     - NEW_FILES, MODIFIED_FILES, CHANGED_FILES (CSVカウント用)
#
# GitHub環境変数（デフォルト値あり）:
#   - GITHUB_SERVER_URL : GitHubサーバーURL (デフォルト: https://github.com)
#   - GITHUB_REPOSITORY : リポジトリ名 (owner/repo形式)
#
# 出力:
#   - PR_URL    : 作成されたPRのURL (GITHUB_ENVに設定)
#   - PR_NUMBER : PR番号 (GITHUB_ENVに設定、自動マージに使用)
#
# 終了コード:
#   0 : 成功
#   1 : エラー（引数不足、環境変数未設定、git操作失敗等）
#
# セキュリティ:
#   - 全ての必須環境変数の形式を検証
#   - シェルインジェクション対策済み
#   - エラー出力は標準エラーへ
# ============================================================

# 引数の検証
if [ $# -lt 2 ] || [ -z "$1" ] || [ -z "$2" ]; then
  echo "Error: Both workflow_name and workflow_display_name must be non-empty" >&2
  echo "Usage: $0 <workflow_name> <workflow_display_name>" >&2
  echo "Example: $0 fetch-data-daily '毎日簡易チェック'" >&2
  exit 1
fi

WORKFLOW_NAME="$1"
WORKFLOW_DISPLAY_NAME="$2"

# 必須環境変数の検証
REQUIRED_VARS="GITHUB_TOKEN CURRENT_DATE FETCH_TIMESTAMP GITHUB_RUN_ID"
for var in $REQUIRED_VARS; do
  if [ -z "${!var}" ]; then
    echo "Error: Required environment variable '$var' is not set" >&2
    exit 1
  fi
done

# 入力のサニタイズ（シェルインジェクション対策）
# CURRENT_DATEの形式検証（YYYY-MM-DD形式のみ許可）
if ! echo "$CURRENT_DATE" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'; then
  echo "Error: CURRENT_DATE must be in YYYY-MM-DD format, got: $CURRENT_DATE" >&2
  exit 1
fi

# GITHUB_RUN_IDの数値検証
if ! echo "$GITHUB_RUN_ID" | grep -qE '^[0-9]+$'; then
  echo "Error: GITHUB_RUN_ID must be numeric, got: $GITHUB_RUN_ID" >&2
  exit 1
fi

# FETCH_TIMESTAMPの形式検証（英数字、アンダースコア、ハイフンのみ許可）
if ! echo "$FETCH_TIMESTAMP" | grep -qE '^[a-zA-Z0-9_-]+$'; then
  echo "Error: FETCH_TIMESTAMP contains invalid characters: $FETCH_TIMESTAMP" >&2
  exit 1
fi

# GitHub関連の環境変数（デフォルト値あり）
GITHUB_SERVER_URL="${GITHUB_SERVER_URL:-https://github.com}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"

# ブランチ名の作成（ワークフローに応じて接尾辞を変更）
case "$WORKFLOW_NAME" in
  fetch-data-daily)
    BRANCH_NAME="data-update-daily-${FETCH_TIMESTAMP}-${GITHUB_RUN_ID}"
    ;;
  fetch-data-weekly)
    BRANCH_NAME="data-update-weekly-${FETCH_TIMESTAMP}-${GITHUB_RUN_ID}"
    ;;
  fetch-data)
    BRANCH_NAME="data-update-${FETCH_TIMESTAMP}-${GITHUB_RUN_ID}"
    ;;
  *)
    BRANCH_NAME="data-update-${WORKFLOW_NAME}-${FETCH_TIMESTAMP}-${GITHUB_RUN_ID}"
    ;;
esac

echo "Creating branch: $BRANCH_NAME"

# 新しいブランチにチェックアウト
if ! git checkout -b "$BRANCH_NAME"; then
  echo "Error: Failed to create branch '$BRANCH_NAME'" >&2
  exit 1
fi

# 変更内訳の取得
# 環境変数が設定されている場合はそれを使用（fetch-data.ymlのCSVカウント等）
# 設定されていない場合はgitから計算（最適化: 1回のgit diffで全情報取得）
if [ -z "$NEW_FILES" ] || [ -z "$MODIFIED_FILES" ]; then
  # 一度のgit diffで全ての変更情報を取得（パフォーマンス最適化）
  GIT_STATUS=$(git diff --cached --name-status)
  if [ -z "$NEW_FILES" ]; then
    # data/raw/配下のCSVファイルのみをカウント（メタデータは除外）
    NEW_FILES=$(echo "$GIT_STATUS" | grep "^A" | grep -E '^A\s+data/raw/[^/]+\.csv$' 2>/dev/null | wc -l | xargs)
  fi
  if [ -z "$MODIFIED_FILES" ]; then
    # data/raw/配下のCSVファイルのみをカウント（修正時も同様）
    MODIFIED_FILES=$(echo "$GIT_STATUS" | grep "^M" | grep -E '^M\s+data/raw/[^/]+\.csv$' 2>/dev/null | wc -l | xargs)
  fi
fi
# CHANGED_FILESのデフォルト値設定
if [ -z "$CHANGED_FILES" ]; then
  CHANGED_FILES=$((NEW_FILES + MODIFIED_FILES))
fi

# コミットメッセージの作成（統一形式）
if [ "$CHANGED_FILES" -gt 0 ]; then
  if [ "$NEW_FILES" -gt 0 ] && [ "$MODIFIED_FILES" -gt 0 ]; then
    FILE_DETAIL="新規${NEW_FILES}件/更新${MODIFIED_FILES}件"
  elif [ "$NEW_FILES" -gt 0 ]; then
    FILE_DETAIL="新規${NEW_FILES}件"
  else
    FILE_DETAIL="更新${MODIFIED_FILES}件"
  fi

  COMMIT_MSG="データ更新: $CURRENT_DATE - ${CHANGED_FILES}件 ($FILE_DETAIL)"
else
  COMMIT_MSG="データ更新: $CURRENT_DATE"
fi

# コミット実行
if ! git commit -m "$COMMIT_MSG"; then
  echo "Error: Failed to commit changes" >&2
  exit 1
fi

# ブランチをプッシュ
if ! git push origin "$BRANCH_NAME"; then
  echo "Error: Failed to push branch '$BRANCH_NAME'" >&2
  exit 1
fi

# Pull Request の作成（統一形式）
PR_TITLE="$COMMIT_MSG"

# PR本文をファイルで作成（統一フォーマット）
PR_BODY_FILE="/tmp/pr_body.md"
{
  echo "## 🤖 自動データ更新"
  echo ""
  echo "このPull Requestは GitHub Actions により自動生成されました。"
  echo ""
  echo "### 📊 実行情報"
  echo "- **ワークフロー**: $WORKFLOW_DISPLAY_NAME ($WORKFLOW_NAME)"
  echo "- **実行日時**: $CURRENT_DATE"
  echo "- **実行ID**: [${GITHUB_RUN_ID}](${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID})"

  # ワークフロー固有の情報を追加
  case "$WORKFLOW_NAME" in
    fetch-data-daily)
      [ -n "${CURRENT_WEEK:-}" ] && echo "- **現在の週**: 第${CURRENT_WEEK}週"
      ;;
    fetch-data-weekly)
      [ -n "${CURRENT_WEEK:-}" ] && echo "- **現在の週**: 第${CURRENT_WEEK}週"
      ;;
  esac

  echo ""
  echo "### 🎯 チェック範囲"

  # ワークフロー固有のチェック範囲
  case "$WORKFLOW_NAME" in
    fetch-data-daily)
      [ -n "${CURRENT_YEAR:-}" ] && echo "- **対象年**: ${CURRENT_YEAR}年"
      if [ -n "${PREVIOUS_WEEK:-}" ] && [ -n "${CURRENT_WEEK:-}" ]; then
        echo "- **対象週**: 第${PREVIOUS_WEEK}週, 第${CURRENT_WEEK}週"
      fi
      if [ -n "${PREVIOUS_MONTH:-}" ] && [ -n "${CURRENT_MONTH:-}" ]; then
        echo "- **対象月**: ${PREVIOUS_MONTH}月, ${CURRENT_MONTH}月"
      fi
      echo "- **チェック方式**: 最新週＋前週の週次データ、当月＋前月の月次データ"
      ;;
    fetch-data-weekly)
      if [ -n "${START_YEAR:-}" ] && [ -n "${END_YEAR:-}" ]; then
        echo "- **対象期間**: ${START_YEAR}年 - ${END_YEAR}年"
      fi
      echo "- **チェック方式**: 全ファイルの最新状態確認（既存ファイル更新チェック含む）"
      if [ "${CHECK_PREVIOUS_YEAR:-false}" = "true" ] && [ -n "${PREVIOUS_YEAR:-}" ]; then
        echo "- **前年データ**: ${PREVIOUS_YEAR}年のデータも確認済み"
      fi
      ;;
    fetch-data)
      if [ -n "${START_YEAR:-}" ] && [ -n "${END_YEAR:-}" ]; then
        echo "- **対象期間**: ${START_YEAR}年 - ${END_YEAR}年"
      fi
      echo "- **データタイプ**: ${DATA_TYPES:-ALL}"
      if [ "${SKIP_EXISTING:-false}" = "true" ]; then
        echo "- **チェック方式**: 既存ファイルスキップ"
      else
        echo "- **チェック方式**: 全ファイル取得"
      fi
      ;;
  esac

  echo ""
  echo "### 📈 更新統計"
  if [ "$NEW_FILES" -gt 0 ] || [ "$MODIFIED_FILES" -gt 0 ]; then
    echo "- **新規ファイル**: ${NEW_FILES:-0}件"
    echo "- **更新ファイル**: ${MODIFIED_FILES:-0}件"
  fi
  echo "- **合計変更**: ${CHANGED_FILES}件"
  echo ""

  # ワークフロー固有のチェック項目
  echo "### ✅ チェック項目"

  case "$WORKFLOW_NAME" in
    fetch-data-weekly)
      echo "- [x] 既存ファイルの更新確認"
      echo "- [x] 新規ファイルの取得"
      if [ "$VALIDATION_SUCCESS" = "true" ]; then
        echo "- [x] データ整合性の検証"
      else
        echo "- [ ] データ整合性の検証 ⚠️ **要確認**"
      fi
      if [ "$CHECK_PREVIOUS_YEAR" = "true" ]; then
        echo "- [x] 前年データの確認"
      fi
      ;;
    fetch-data)
      echo "- [x] データ取得完了"
      echo "- [x] ファイル検証済み"
      if [ "$SKIP_EXISTING" = "true" ]; then
        echo "- [x] 既存ファイルをスキップ"
      fi
      if [ "$VERIFY_CONTINUITY" = "true" ]; then
        if [ "$CONTINUITY_VALID" = "true" ]; then
          echo "- [x] データ連続性検証"
        else
          echo "- [ ] データ連続性検証 ⚠️ **欠損あり（詳細はログを確認）**"
        fi
      fi
      ;;
    *)
      echo "- [x] データ取得完了"
      echo "- [x] ファイル検証済み"
      ;;
  esac

  # 検証エラーがある場合の警告
  if [ "$VALIDATION_SUCCESS" = "false" ]; then
    echo ""
    echo "### ⚠️ 警告"
    echo "データ検証でエラーが検出されました。マージ前に検証レポートを確認してください。"
  fi

  echo ""
  echo "### ℹ️ 備考"

  # ワークフロー固有の備考
  case "$WORKFLOW_NAME" in
    fetch-data-daily)
      echo "- 毎日実行では直近のデータのみをチェックしています"
      echo "- 全データの包括的チェックは毎週木曜日に実行されます"
      ;;
    fetch-data-weekly)
      echo "- 週次チェックでは既存ファイルも含めて最新状態を確認しています"
      echo "- 東京都のデータは通常、木曜日16時以降に更新されます"
      ;;
    fetch-data)
      echo "- このワークフローは手動実行またはスケジュール実行により起動されました"
      if [ -n "$DATA_TYPES" ]; then
        echo "- データタイプが指定されています: $DATA_TYPES"
      fi
      ;;
  esac

  echo ""
  echo "---"
  echo "*This PR was automatically created by GitHub Actions workflow.*"
} > "$PR_BODY_FILE"

# ラベル管理
echo "Managing labels for PR..."

# 共通ラベルを作成（既存の場合は無視）
gh label create "data-update" --description "Automated data update PR" --color "0E8A16" 2>/dev/null || true
gh label create "automated" --description "Automatically generated" --color "ededed" 2>/dev/null || true

# ワークフロー固有のラベル
case "$WORKFLOW_NAME" in
  fetch-data-daily)
    gh label create "daily-update" --description "Daily automated data check" --color "0075CA" 2>/dev/null || true
    SPECIFIC_LABEL="daily-update"
    ;;
  fetch-data-weekly)
    gh label create "weekly-update" --description "Weekly full data check" --color "5319E7" 2>/dev/null || true
    SPECIFIC_LABEL="weekly-update"
    ;;
  *)
    SPECIFIC_LABEL=""
    ;;
esac

# PR作成
if [ -n "$SPECIFIC_LABEL" ]; then
  PR_URL=$(gh pr create \
    --title "$PR_TITLE" \
    --body-file "$PR_BODY_FILE" \
    --base main \
    --head "$BRANCH_NAME" \
    --label "$SPECIFIC_LABEL" \
    --label "data-update" \
    --label "automated")
else
  PR_URL=$(gh pr create \
    --title "$PR_TITLE" \
    --body-file "$PR_BODY_FILE" \
    --base main \
    --head "$BRANCH_NAME" \
    --label "data-update" \
    --label "automated")
fi

echo "✅ Successfully created PR: $PR_URL"
echo "PR_URL=$PR_URL" >> $GITHUB_ENV

# PR番号を取得
PR_NUMBER=$(echo "$PR_URL" | grep -oE '[0-9]+$' || true)
if [ -z "$PR_NUMBER" ]; then
  echo "⚠️ Warning: Failed to extract PR number from: $PR_URL" >&2
else
  echo "PR_NUMBER=$PR_NUMBER" >> $GITHUB_ENV

  # 自動マージを有効化（squashマージを使用）
  echo "🔄 自動マージを設定中..."
  if gh pr merge "$PR_NUMBER" --auto --squash; then
    echo "✅ Auto-merge configured successfully"
  else
    echo "⚠️ Note: Auto-merge setup failed. Check branch protection rules." >&2
    echo "   Manual merge may be required." >&2
  fi
fi
