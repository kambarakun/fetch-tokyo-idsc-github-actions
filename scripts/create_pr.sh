#!/bin/bash
set -e

# ============================================================
# PR作成用の共通スクリプト
#
# 使用方法:
#   scripts/create_pr.sh <workflow_name> <workflow_display_name>
#
# 必要な環境変数:
#   - GITHUB_TOKEN
#   - CURRENT_DATE
#   - CHANGED_FILES
#   - FETCH_TIMESTAMP
#   - GITHUB_RUN_ID
#   - その他ワークフロー固有の変数
# ============================================================

# 引数の検証
if [ $# -lt 2 ]; then
  echo "Usage: $0 <workflow_name> <workflow_display_name>"
  echo "Example: $0 fetch-data-daily '毎日簡易チェック'"
  exit 1
fi

WORKFLOW_NAME="$1"
WORKFLOW_DISPLAY_NAME="$2"

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
git checkout -b "$BRANCH_NAME"

# 変更内訳の取得
NEW_FILES=$(git diff --cached --name-status | grep "^A" 2>/dev/null | wc -l | xargs)
MODIFIED_FILES=$(git diff --cached --name-status | grep "^M" 2>/dev/null | wc -l | xargs)
CHANGED_FILES="${CHANGED_FILES:-0}"

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
git commit -m "$COMMIT_MSG"

# ブランチをプッシュ
git push origin "$BRANCH_NAME"

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
      echo "- **現在の週**: 第${CURRENT_WEEK}週"
      ;;
    fetch-data-weekly)
      echo "- **現在の週**: 第${CURRENT_WEEK}週"
      ;;
  esac

  echo ""
  echo "### 🎯 チェック範囲"

  # ワークフロー固有のチェック範囲
  case "$WORKFLOW_NAME" in
    fetch-data-daily)
      echo "- **対象年**: ${CURRENT_YEAR}年"
      echo "- **対象週**: 第${PREVIOUS_WEEK}週, 第${CURRENT_WEEK}週"
      echo "- **対象月**: ${PREVIOUS_MONTH}月, ${CURRENT_MONTH}月"
      echo "- **チェック方式**: 最新週＋前週の週次データ、当月＋前月の月次データ"
      ;;
    fetch-data-weekly)
      echo "- **対象期間**: ${START_YEAR}年 - ${END_YEAR}年"
      echo "- **チェック方式**: 全ファイルの最新状態確認（既存ファイル更新チェック含む）"
      if [ "$CHECK_PREVIOUS_YEAR" = "true" ]; then
        echo "- **前年データ**: ${PREVIOUS_YEAR}年のデータも確認済み"
      fi
      ;;
    fetch-data)
      echo "- **対象期間**: ${START_YEAR}年 - ${END_YEAR}年"
      echo "- **データタイプ**: ${DATA_TYPES:-ALL}"
      echo "- **チェック方式**: ${SKIP_EXISTING:+既存ファイルスキップ}${SKIP_EXISTING:-全ファイル取得}"
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
PR_NUMBER=$(echo "$PR_URL" | sed 's/.*\/pull\///')
echo "PR_NUMBER=$PR_NUMBER" >> $GITHUB_ENV

# 自動マージを有効化（squashマージを使用）
echo "🔄 自動マージを設定中..."
gh pr merge "$PR_NUMBER" --auto --squash || {
  echo "⚠️ 自動マージの設定に失敗しました。手動でマージしてください。"
}
