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
#   $1 workflow_name         - ワークフロー識別子 (fetch-data-daily|fetch-data-weekly|fetch-data|process-data|migrate-metadata)
#   $2 workflow_display_name - PR本文に表示するワークフロー名
#
# 必須環境変数:
#   - GITHUB_TOKEN      : GitHub APIアクセス用トークン
#   - CURRENT_DATE      : 実行日 (YYYY-MM-DD形式)
#   - FETCH_TIMESTAMP   : 実行タイムスタンプ (英数字、アンダースコア、ハイフンのみ)
#   - GITHUB_RUN_ID     : GitHub ActionsのRun ID (数値)
#
# オプション環境変数（ワークフロー別）:
#   共通:
#     - AUTO_MERGE (true|false) - PR自動マージを有効化 (デフォルト: false)
#     - FORCE_MERGE_ON_FAILURE (true|false) - 検証失敗時も強制マージ (デフォルト: false)
#   fetch-data-daily:
#     - CURRENT_YEAR, CURRENT_WEEK, CURRENT_MONTH
#     - PREVIOUS_WEEK, PREVIOUS_MONTH
#   fetch-data-weekly:
#     - START_YEAR, END_YEAR, CURRENT_WEEK
#     - CHECK_PREVIOUS_YEAR, PREVIOUS_YEAR
#     - VALIDATION_SUCCESS (true|false)
#   fetch-data:
#     - START_YEAR, END_YEAR
#     - DATA_TYPES, MODE
#     - VERIFY_CONTINUITY, CONTINUITY_VALID
#     - NEW_FILES, MODIFIED_FILES, CHANGED_FILES (CSVカウント用)
#   process-data:
#     - TARGET_FILES (処理対象ファイル)
#     - VERIFY_OUTPUT (true|false)
#     - VALIDATION_PASSED (true|false)
#     - NEW_FILES, MODIFIED_FILES, CHANGED_FILES (処理済みファイルカウント用)
#   migrate-metadata:
#     - TARGET_VERSION (マイグレーション目標バージョン)
#     - MIGRATED_COUNT (マイグレーション対象ファイル数)
#
# GitHub環境変数（デフォルト値あり）:
#   - GITHUB_SERVER_URL : GitHubサーバーURL (デフォルト: https://github.com)
#   - GITHUB_REPOSITORY : リポジトリ名 (owner/repo形式)
#
# 出力:
#   - PR_URL    : 作成されたPRのURL (GITHUB_ENVに設定)
#   - PR_NUMBER : PR番号 (GITHUB_ENVに設定、自動マージに使用)
#
# 動作:
#   1. 新規ブランチを作成してチェックアウト
#   2. 変更をコミット
#   3. リモートにプッシュ
#   4. PRを作成（ラベル付与）
#   5. 自動マージを設定（squashマージ、マージ後ブランチ自動削除）
#      - auto-merge有効化により、CI/CD成功後に自動的にマージ
#      - マージ完了後、ブランチは自動的に削除される
#      - ブランチ保護ルールの要件を満たす必要がある
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

# ワークフロー固有の必須環境変数チェック
case "$WORKFLOW_NAME" in
  migrate-metadata)
    if [ -z "${TARGET_VERSION:-}" ]; then
      echo "Error: TARGET_VERSION is required for migrate-metadata workflow" >&2
      exit 1
    fi
    if [ -z "${MIGRATED_COUNT:-}" ]; then
      echo "Error: MIGRATED_COUNT is required for migrate-metadata workflow" >&2
      exit 1
    fi
    ;;
esac

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
  process-data)
    BRANCH_NAME="data-process-${FETCH_TIMESTAMP}-${GITHUB_RUN_ID}"
    ;;
  migrate-metadata)
    BRANCH_NAME="metadata-migration-${FETCH_TIMESTAMP}-${GITHUB_RUN_ID}"
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
# ワークフローに応じて適切なファイルをカウント

# デフォルト値の初期化（防御的プログラミング）
NEW_FILES="${NEW_FILES:-}"
MODIFIED_FILES="${MODIFIED_FILES:-}"
CHANGED_FILES="${CHANGED_FILES:-}"

# 優先順位1: stats.jsonから読み取る（最も正確）
# データ取得スクリプトが記録した実際の処理結果を使用
STATS_JSON_PATH="data/logs/stats_${FETCH_TIMESTAMP}.json"
if [ -f "$STATS_JSON_PATH" ] && command -v jq >/dev/null 2>&1; then
  echo "Reading file counts from stats.json..." >&2
  if jq -e . "$STATS_JSON_PATH" >/dev/null 2>&1; then
    STATS_NEW=$(jq -r '.new_files // 0' "$STATS_JSON_PATH" 2>/dev/null || echo "0")
    STATS_UPDATED=$(jq -r '.updated_files // 0' "$STATS_JSON_PATH" 2>/dev/null || echo "0")

    # 数値検証
    if [[ "$STATS_NEW" =~ ^[0-9]+$ ]] && [[ "$STATS_UPDATED" =~ ^[0-9]+$ ]]; then
      NEW_FILES="$STATS_NEW"
      MODIFIED_FILES="$STATS_UPDATED"
      echo "✓ Using stats.json: new=$NEW_FILES, updated=$MODIFIED_FILES (source: stats.json)" >&2
      STATS_READ_SUCCESS=true
    else
      echo "⚠️ Warning: Invalid numbers in stats.json, falling back to other methods" >&2
      STATS_READ_SUCCESS=false
    fi
  else
    echo "⚠️ Warning: stats.json is not valid JSON, falling back to other methods" >&2
    STATS_READ_SUCCESS=false
  fi
else
  STATS_READ_SUCCESS=false
fi

# 優先順位2: 環境変数が設定されているか確認
if [ "$STATS_READ_SUCCESS" != "true" ] && { [ -z "$NEW_FILES" ] || [ -z "$MODIFIED_FILES" ]; }; then
  echo "Calculating file counts from git diff..." >&2
  # 一度のgit diffで全ての変更情報を取得（パフォーマンス最適化）
  GIT_STATUS=$(git diff --cached --name-status)

  # ワークフローに応じて異なるファイルパターンをカウント
  case "$WORKFLOW_NAME" in
    migrate-metadata)
      # メタデータマイグレーションはJSONファイルをカウント
      if [ -z "$NEW_FILES" ]; then
        NEW_FILES=$(echo "$GIT_STATUS" | grep -E '^A\s+data/raw/\.metadata/.*\.json$' 2>/dev/null | wc -l | xargs)
      fi
      if [ -z "$MODIFIED_FILES" ]; then
        MODIFIED_FILES=$(echo "$GIT_STATUS" | grep -E '^M\s+data/raw/\.metadata/.*\.json$' 2>/dev/null | wc -l | xargs)
      fi
      ;;
    *)
      # その他のワークフローはCSVファイルをカウント
      if [ -z "$NEW_FILES" ]; then
        # data/raw/直下のCSVファイルのみをカウント
        # [^/]+でサブディレクトリ（.metadata/等）を除外、JSONやログファイルも除外
        NEW_FILES=$(echo "$GIT_STATUS" | grep -E '^A\s+data/raw/[^/]+\.csv$' 2>/dev/null | wc -l | xargs)
      fi
      if [ -z "$MODIFIED_FILES" ]; then
        # data/raw/直下のCSVファイルのみをカウント（修正時も同様）
        MODIFIED_FILES=$(echo "$GIT_STATUS" | grep -E '^M\s+data/raw/[^/]+\.csv$' 2>/dev/null | wc -l | xargs)
      fi
      ;;
  esac
  echo "✓ Using git diff data: new=$NEW_FILES, updated=$MODIFIED_FILES (source: git diff)" >&2
elif [ "$STATS_READ_SUCCESS" != "true" ]; then
  echo "✓ Using environment variables: new=$NEW_FILES, updated=$MODIFIED_FILES (source: environment)" >&2
fi

# 3. 最終的なデフォルト値設定と検証
NEW_FILES="${NEW_FILES:-0}"
MODIFIED_FILES="${MODIFIED_FILES:-0}"

# 数値検証（空文字列や非数値の場合のフォールバック）
if ! [[ "$NEW_FILES" =~ ^[0-9]+$ ]]; then
  NEW_FILES=0
fi
if ! [[ "$MODIFIED_FILES" =~ ^[0-9]+$ ]]; then
  MODIFIED_FILES=0
fi

# 4. CHANGED_FILESの計算（必ず最新値で再計算）
# stats.jsonや環境変数からの値に関わらず、NEW_FILES + MODIFIED_FILESで統一
CHANGED_FILES=$((NEW_FILES + MODIFIED_FILES))

echo "Final counts: new=$NEW_FILES, updated=$MODIFIED_FILES, total=$CHANGED_FILES" >&2

# コミットメッセージの作成（ワークフロー別）
case "$WORKFLOW_NAME" in
  migrate-metadata)
    # メタデータマイグレーション専用フォーマット
    # フォーマット: "メタデータ更新: バージョンX.X.Xへマイグレーション (Nファイル)"
    COMMIT_MSG="メタデータ更新: バージョン${TARGET_VERSION}へマイグレーション (${MIGRATED_COUNT}ファイル)"
    ;;
  *)
    # データ更新ワークフロー用の統一形式
    # 常に「X件 (新規Y件/更新Z件)」の形式で表示
    # 0件の場合も明示的に表示することで一貫性を保つ
    #
    # フォーマット: "データ更新: YYYY-MM-DD - X件 (新規Y件/更新Z件)"
    # 例:
    #   - データ更新: 2025-12-02 - 2件 (新規2件/更新0件)
    #   - データ更新: 2025-12-02 - 3件 (新規1件/更新2件)
    #   - データ更新: 2025-12-02 - 0件 (新規0件/更新0件)
    COMMIT_MSG="データ更新: $CURRENT_DATE - ${CHANGED_FILES}件 (新規${NEW_FILES}件/更新${MODIFIED_FILES}件)"
    ;;
esac

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
      if [ -n "${TWO_WEEKS_AGO:-}" ] && [ -n "${PREVIOUS_WEEK:-}" ] && [ -n "${CURRENT_WEEK:-}" ]; then
        echo "- **対象週**: 第${TWO_WEEKS_AGO}週, 第${PREVIOUS_WEEK}週, 第${CURRENT_WEEK}週（木曜更新考慮）"
      elif [ -n "${PREVIOUS_WEEK:-}" ] && [ -n "${CURRENT_WEEK:-}" ]; then
        echo "- **対象週**: 第${PREVIOUS_WEEK}週, 第${CURRENT_WEEK}週"
      fi
      if [ -n "${PREVIOUS_MONTH:-}" ] && [ -n "${CURRENT_MONTH:-}" ]; then
        echo "- **対象月**: ${PREVIOUS_MONTH}月, ${CURRENT_MONTH}月"
      fi
      echo "- **チェック方式**: 2週前＋前週＋最新週の週次データ、当月＋前月の月次データ（木曜更新考慮）"
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
    process-data)
      echo "- **対象**: ${TARGET_FILES:-ALL raw/*.csv}"
      echo "- **処理内容**: raw→processed変換（Shift_JIS→UTF-8、正規化）"
      if [ "${VERIFY_OUTPUT:-false}" = "true" ]; then
        echo "- **検証**: 出力データの品質チェックを実施"
      fi
      ;;
    migrate-metadata)
      # チェック範囲: マイグレーション対象の定義
      [ -n "${TARGET_VERSION:-}" ] && echo "- **目標バージョン**: ${TARGET_VERSION}"
      echo "- **対象ディレクトリ**: data/raw/.metadata/"
      ;;
  esac

  echo ""
  echo "### 📈 更新統計"

  # ワークフロー別の統計表示
  case "$WORKFLOW_NAME" in
    migrate-metadata)
      # メタデータマイグレーション専用の統計表示
      echo "#### メタデータファイル"
      echo "- **マイグレーション完了**: ${MIGRATED_COUNT}ファイル"
      echo ""
      ;;
    *)
      # stats.jsonから正常に読み込まれた場合、または実際に変更がある場合は詳細を表示
      # コミットメッセージと同じパターンで条件を記述（可読性・保守性向上）
      if [ "$CHANGED_FILES" -gt 0 ] || [ "$STATS_READ_SUCCESS" = true ]; then
        echo "#### 生データ（data/raw/）"
        echo "- **新規ファイル**: ${NEW_FILES:-0}件"
        echo "- **更新ファイル**: ${MODIFIED_FILES:-0}件"

        # 処理済みファイル数の表示（環境変数が設定されている場合のみ）
        if [ -n "${NEW_PROCESSED_FILES:-}" ] && [ "${NEW_PROCESSED_FILES}" -gt 0 ]; then
          echo ""
          echo "#### 処理済みデータ（data/processed/）"
          echo "- **処理済みファイル**: ${NEW_PROCESSED_FILES}件"

          # 処理統計の表示（stats.jsonから取得した場合）
          if [ -n "${STATS_TOTAL:-}" ]; then
            echo ""
            echo "#### 📊 データ処理統計"
            echo "- **処理対象**: ${STATS_TOTAL}件"
            echo "- **成功**: ${STATS_SUCCEEDED:-0}件"
            if [ "${STATS_FAILED:-0}" -gt 0 ]; then
              echo "- **失敗**: ${STATS_FAILED}件 ⚠️"
            else
              echo "- **失敗**: 0件 ✅"
            fi
          fi
        fi
        echo ""
      fi
      echo "- **合計変更**: ${CHANGED_FILES}件"
      echo ""
      ;;
  esac

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
    process-data)
      echo "- [x] raw→processed変換完了"
      echo "- [x] エンコーディング変換（Shift_JIS→UTF-8）"
      echo "- [x] データ正規化"
      if [ "${VERIFY_OUTPUT:-false}" = "true" ]; then
        if [ "${VALIDATION_PASSED:-false}" = "true" ]; then
          echo "- [x] 出力データ検証"
        else
          echo "- [ ] 出力データ検証 ⚠️ **要確認**"
        fi
      fi
      ;;
    migrate-metadata)
      echo "- [x] メタデータファイルのバージョン確認"
      echo "- [x] マイグレーション実行"
      echo "- [x] バージョン更新完了"
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
    process-data)
      echo "- このワークフローは手動実行により起動されました"
      echo "- 処理済みデータは data/processed/ ディレクトリに保存されます"
      if [ -n "$TARGET_FILES" ]; then
        echo "- 特定のファイルのみが処理対象として指定されています"
      else
        echo "- data/raw/ 内の全CSVファイルが処理対象です"
      fi
      ;;
    migrate-metadata)
      echo "- メタデータファイルのスキーマバージョンを更新しました"
      echo "- メタデータは data/raw/.metadata/ ディレクトリに保存されています"
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
  process-data)
    gh label create "data-processing" --description "Data processing (raw to processed)" --color "D876E3" 2>/dev/null || true
    SPECIFIC_LABEL="data-processing"
    ;;
  migrate-metadata)
    gh label create "metadata-migration" --description "Metadata schema migration" --color "FBCA04" 2>/dev/null || true
    SPECIFIC_LABEL="metadata-migration"
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

  # 自動マージ有効化の判定
  # AUTO_MERGE_EFFECTIVE = AUTO_MERGE && (検証成功 OR FORCE_MERGE_ON_FAILURE)
  AUTO_MERGE_REQUESTED="${AUTO_MERGE:-false}"
  FORCE_MERGE="${FORCE_MERGE_ON_FAILURE:-false}"

  # ワークフロー別の検証結果を取得
  VALIDATIONS_PASSED="true"  # デフォルトは検証成功
  case "$WORKFLOW_NAME" in
    fetch-data-weekly)
      if [ "${VALIDATION_SUCCESS:-}" = "false" ]; then
        VALIDATIONS_PASSED="false"
      fi
      ;;
    fetch-data)
      if [ "${CONTINUITY_VALID:-}" = "false" ] && [ "${VERIFY_CONTINUITY:-}" = "true" ]; then
        VALIDATIONS_PASSED="false"
      fi
      ;;
    process-data)
      if [ "${VALIDATION_PASSED:-}" = "false" ]; then
        VALIDATIONS_PASSED="false"
      fi
      ;;
  esac

  # AUTO_MERGE_EFFECTIVE の計算
  AUTO_MERGE_EFFECTIVE="false"
  if [ "$AUTO_MERGE_REQUESTED" = "true" ]; then
    if [ "$VALIDATIONS_PASSED" = "true" ] || [ "$FORCE_MERGE" = "true" ]; then
      AUTO_MERGE_EFFECTIVE="true"
    else
      echo "ℹ️ Auto-merge requested but validations failed. Skipping auto-merge." >&2
      echo "   Set FORCE_MERGE_ON_FAILURE=true to override this behavior." >&2
    fi
  fi

  # 自動マージの実行
  if [ "$AUTO_MERGE_EFFECTIVE" = "true" ]; then
    echo "🔄 自動マージを設定中..."
    if gh pr merge "$PR_NUMBER" --auto --squash --delete-branch; then
      echo "✅ Auto-merge configured successfully (branch will be deleted after merge)"
      if [ "$FORCE_MERGE" = "true" ] && [ "$VALIDATIONS_PASSED" = "false" ]; then
        echo "⚠️ Warning: Force merge enabled despite validation failures" >&2
      fi
    else
      EXIT_CODE=$?
      echo "⚠️ Note: Auto-merge setup failed. Check branch protection rules." >&2
      echo "   Manual merge may be required." >&2
      echo "   Error code: $EXIT_CODE" >&2
      echo "   Attempted command: gh pr merge $PR_NUMBER --auto --squash --delete-branch" >&2
      if [ $EXIT_CODE -eq 1 ]; then
        echo "💡 If auto-merge succeeds but branch deletion fails, you may need to delete the branch manually:" >&2
        echo "   git push origin --delete $BRANCH_NAME" >&2
      fi
    fi
  else
    echo "ℹ️ Auto-merge disabled (AUTO_MERGE=$AUTO_MERGE_REQUESTED, VALIDATIONS_PASSED=$VALIDATIONS_PASSED, FORCE_MERGE=$FORCE_MERGE)"
    echo "   Manual merge is required."
  fi
fi
