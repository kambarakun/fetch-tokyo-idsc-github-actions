# CLAUDE.md - 東京都感染症発生動向データ自動化プロジェクトガイド

このファイルは、Claude Code（claude.ai/code）が本プロジェクトで効率的に作業するためのガイドラインを提供します。

## 最終更新日

2025-09-24

## バージョン

1.0.0

==============================================================================

## 🚀 プロジェクト構造クイックリファレンス

### 📁 コアファイルの場所
```bash
# プロジェクト仕様
.kiro/specs/tokyo-epidemic-data-automation/
├── requirements.md          # 要求仕様書
├── design.md               # 設計書
└── tasks.md                # タスク一覧

# GitHub Actions ワークフロー
.github/workflows/
├── claude.yml              # Claude AI統合
├── claude-code-review.yml  # コードレビュー自動化
└── fetch-data.yml          # データ取得自動化（作成予定）

# データ保存ディレクトリ（作成予定）
data/
├── raw/                    # 生データ（Shift_JIS）
├── processed/              # 処理済みデータ
└── logs/                   # ログファイル

# ソースコード（作成予定）
src/
├── fetcher/                # データ取得モジュール
├── processor/              # データ処理モジュール
└── utils/                  # ユーティリティ
```

### 📁 主要スクリプト（作成予定）
```bash
# データ取得
scripts/fetch_data.py       # データ取得メインスクリプト
scripts/validate_data.py    # データ検証
scripts/retry_failed.py     # 失敗した取得のリトライ

# データ処理
scripts/process_data.py     # データ処理と変換
scripts/generate_report.py  # レポート生成

# ユーティリティ
scripts/check_duplicates.py # 重複チェック
scripts/cleanup_old.py      # 古いデータのクリーンアップ
```

==============================================================================

## 🔧 ワークフロー別クイックコマンド

### 🔍 ファイル検索
```bash
# プロジェクト仕様の確認（存在しない場合はスキップ）
[ -f .kiro/specs/tokyo-epidemic-data-automation/requirements.md ] && \
  cat .kiro/specs/tokyo-epidemic-data-automation/requirements.md || \
  echo "requirements.md は未作成です"

# GitHub Actionsワークフローの確認
ls -la .github/workflows/

# データディレクトリの確認（存在しない場合はスキップ）
[ -d data ] && ls -la data/ || echo "data/ は未作成です"
```

### ✅ ローカルテスト
```bash
# データ取得のテスト（存在しない場合はスキップ）
[ -f scripts/fetch_data.py ] && python scripts/fetch_data.py --test || echo "scripts/fetch_data.py は未作成です"

# データ検証（存在しない場合はスキップ）
[ -f scripts/validate_data.py ] && python scripts/validate_data.py data/raw/latest.csv || echo "scripts/validate_data.py は未作成です"

# 処理のテスト（存在しない場合はスキップ）
[ -f scripts/process_data.py ] && python scripts/process_data.py --dry-run || echo "scripts/process_data.py は未作成です"
```

### 📦 デプロイとスケジューリング
```bash
# GitHub Actionsワークフローの有効化（要: gh CLI インストール & gh auth login）
gh workflow enable fetch-data.yml

# 手動実行
gh workflow run fetch-data.yml

# スケジュール設定（.github/workflows/fetch-data.yml内で設定）
```

==============================================================================

## 📊 プロジェクトの現在のステータス

### 進捗概要（2025-09-24時点）
- **仕様定義**: 完了 ✅
- **GitHub Actions設定**: 基本設定完了（Claude統合済み）
- **データ取得モジュール**: 未実装 ⏳
- **データ処理モジュール**: 未実装 ⏳
- **自動化ワークフロー**: 未実装 ⏳

### 次のステップ
1. TokyoEpidemicSurveillanceFetcherクラスの実装
2. GitHub Actionsワークフローの作成
3. エラーハンドリングとリトライロジックの実装
4. データ保存とメタデータ管理の実装
5. 通知システムの設定

## メタインストラクション：このファイルの使用方法

- **基本原則**: 東京都の感染症データを自動的に取得・管理する堅牢なシステムを構築する
- **私の役割**: プロジェクトオーナー/データアナリスト
- **あなたの役割**: 自動化システムの構築を支援する熟練したアシスタント
- **リロード**: **すべての**アシスタントレスポンスの開始時に、このファイルを再読み込みして準拠を確認してください
- **確認**: 破壊的操作（ファイルの削除、大規模変更、データベース更新など）の前に、「実行してよろしいですか？（y/n）」と確認してください

## 1. プロジェクトコンテキスト

### 1.1 プロジェクト概要

**目的**: 東京都感染症発生動向情報システムからデータを定期的に自動取得し、GitHub上で管理する
**ドメイン**: 公衆衛生データの収集と管理
**主要ユーザー**: データアナリスト、疫学研究者、公衆衛生担当者

### 1.2 技術スタック

| コンポーネント     | 説明                                                    |
| ----------------- | ------------------------------------------------------ |
| 言語              | Python 3.11+                                           |
| 自動化            | GitHub Actions                                         |
| データ形式        | CSV（Shift_JIS エンコーディング）                        |
| バージョン管理    | Git/GitHub                                             |
| エラー通知        | GitHub Issues                                          |
| データソース      | 東京都感染症発生動向情報システム                          |

### 1.3 コア機能

#### データ取得
- TokyoEpidemicSurveillanceFetcherクラスを使用した自動データ取得
- スケジュール実行（毎週）
- エラー時の指数バックオフリトライ（最大3回）

#### データ管理
- 年/月/週の階層ディレクトリ構造
- タイムスタンプ付きファイル命名
- SHA256ハッシュによるデータ整合性検証
- 重複データの検出とスキップ

#### エラーハンドリング
- 詳細なエラーログ
- GitHub Issues による自動通知
- レート制限の適切な処理
- ネットワークエラーのタイムアウト処理

### 1.4 プロジェクト制約

- **データエンコーディング**: Shift_JISを維持（互換性のため）
- **実行環境**: GitHub Actions (Ubuntu latest)
- **データサイズ**: 大きなCSVファイルの処理に対応
- **プライバシー**: 個人情報を含まない集計データのみ

## 2. 開発ワークフロー

### 2.1 実装アプローチ

#### フェーズ1: 基本実装
```python
# TokyoEpidemicSurveillanceFetcherクラスの実装
# データ取得の基本機能
# CSVファイルの保存
```

#### フェーズ2: エラーハンドリング
```python
# リトライロジックの実装
# エラーログの記録
# 通知システムの設定
```

#### フェーズ3: 自動化
```yaml
# GitHub Actionsワークフローの作成
# スケジュール設定
# 自動コミットとプッシュ
```

#### フェーズ4: 監視と改善
```python
# データ品質チェック
# パフォーマンス最適化
# ドキュメント作成
```

### 2.2 開発チェックリスト

- [ ] TokyoEpidemicSurveillanceFetcherクラスの実装
- [ ] エラーハンドリングとリトライロジック
- [ ] データ保存とファイル管理
- [ ] メタデータとログ記録
- [ ] GitHub Actionsワークフローの作成
- [ ] 通知システムの設定
- [ ] テストとバリデーション
- [ ] ドキュメント作成

## 3. コーディング規約

### 3.1 Pythonコード規約

- **スタイルガイド**: PEP 8準拠
- **型ヒント**: Python 3.11+の型アノテーションを使用
- **エラーハンドリング**: 明示的なtry-exceptブロック
- **ログ**: 構造化ログの使用

### 3.2 ファイル命名規則

```python
# データファイル
f"tokyo_epidemic_{data_type}_{start_date}_{end_date}_{timestamp}.csv"

# メタデータ
f"metadata_{timestamp}.json"

# ログファイル
f"fetch_log_{date}.txt"
```

### 3.3 ディレクトリ構造

```bash
data/
├── raw/
│   └── 2025/
│       └── 01/
│           └── week_01/
│               ├── tokyo_epidemic_weekly_20250101_20250107_20250108120000.csv
│               └── metadata_20250108120000.json
├── processed/
└── logs/
```

## 4. デバッグとトラブルシューティング

### 4.1 一般的なエラー

| エラー           | 原因                                    | 解決策                          |
| --------------- | -------------------------------------- | ------------------------------ |
| ConnectionError | ネットワーク接続の問題                    | リトライロジックの確認            |
| EncodingError   | Shift_JISエンコーディングの問題          | エンコーディング指定の確認        |
| RateLimitError  | APIレート制限                           | 遅延の追加                      |
| DuplicateError  | 重複データ                              | ハッシュチェックの確認            |

### 4.2 検証スクリプト

```bash
# データ検証
python scripts/validate_data.py --file data/raw/latest.csv

# 接続テスト
python scripts/test_connection.py

# ログ確認
tail -f data/logs/fetch_log_$(date +%Y%m%d).txt
```

## 5. GitHub Actions設定

### 5.1 ワークフローテンプレート

```yaml
name: Fetch Tokyo Epidemic Data
on:
  schedule:
    - cron: '0 10 * * 1'  # 毎週月曜日 19:00 JST
  workflow_dispatch:      # 手動実行も可能

permissions:
  contents: write
  actions: read

concurrency:
  group: fetch-data
  cancel-in-progress: true

jobs:
  fetch-data:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python scripts/fetch_data.py
      - name: Configure git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
      - name: Commit and push if changed
        run: |
          git add data/
          if ! git diff --staged --quiet; then
            git commit -m "データ更新: $(date +'%Y-%m-%d %H:%M')"
            git push
          else
            echo "No changes to commit."
          fi
```

### 5.2 環境変数とシークレット

```bash
# リポジトリシークレットの設定（必要に応じて）
GITHUB_TOKEN        # 自動コミット用
NOTIFICATION_WEBHOOK # 通知用（オプション）

# 注意: シークレットはログに出力されないよう、GitHub Actionsの::add-mask::を使用してマスクしてください
```

## 6. プロジェクト固有のパターン

### 6.1 データ取得パターン

```python
# 基本的な取得パターン（タイムアウトとジッター付き）
async def fetch_with_retry(url, max_retries=3):
    import random
    for attempt in range(max_retries):
        try:
            # fetchはタイムアウト引数に対応している想定
            response = await fetch(url, timeout=10)
            return response
        except (TimeoutError, FetchError) as e:  # 実際の例外クラスに置き換える
            if attempt == max_retries - 1:
                raise
            # ジッター付き指数バックオフ
            wait = (2 ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(wait)
    raise MaxRetriesExceeded()
```

### 6.2 データ検証パターン

```python
# CSVデータの検証
def validate_csv(file_path):
    # エンコーディングチェック
    # カラム数チェック
    # データ型チェック
    # 日付範囲チェック
    pass
```

## 7. 重要な注意事項

- **必ず** データ取得前に既存データをチェックして重複を避ける
- **決して** 個人情報を含むデータを保存しない
- **常に** Shift_JISエンコーディングを維持する
- **定期的に** 古いデータのアーカイブを検討する
- **エラー時は** 必ずログを記録し、必要に応じて通知する

## 8. プロジェクト運用ガイドライン

### 8.1 日次チェック項目

- [ ] GitHub Actionsの実行状況確認
- [ ] エラーログの確認
- [ ] データ整合性チェック

### 8.2 週次メンテナンス

- [ ] データバックアップの確認
- [ ] ストレージ使用量の確認
- [ ] パフォーマンスメトリクスのレビュー

### 8.3 月次レビュー

- [ ] データ品質レポートの生成
- [ ] システム改善点の検討
- [ ] ドキュメントの更新

## 9. リファレンス

### 9.1 関連ドキュメント

- [東京都感染症発生動向情報システム](https://survey.tmiph.metro.tokyo.lg.jp/)
- [GitHub Actions ドキュメント](https://docs.github.com/ja/actions)
- [Python asyncio ドキュメント](https://docs.python.org/ja/3/library/asyncio.html)

### 9.2 プロジェクトファイル

- `.kiro/specs/tokyo-epidemic-data-automation/requirements.md` - 要求仕様
- `.kiro/specs/tokyo-epidemic-data-automation/design.md` - 設計書
- `.kiro/specs/tokyo-epidemic-data-automation/tasks.md` - タスク一覧

## 10. トラブルシューティングFAQ

**Q: データ取得が失敗する**
A: ネットワーク接続、URLの変更、レート制限を確認してください。

**Q: 文字化けが発生する**
A: Shift_JISエンコーディングが正しく設定されているか確認してください。

**Q: GitHub Actionsが動作しない**
A: ワークフローの権限設定とシークレットの設定を確認してください。

---

# 重要な指示の再確認
求められたことだけを実行し、それ以上もそれ以下もしない。
目的達成に絶対に必要でない限り、ファイルを作成しない。
既存ファイルの編集を新規作成より常に優先する。
ユーザーから明示的に要求されない限り、ドキュメントファイル（*.md）やREADMEファイルを積極的に作成しない。