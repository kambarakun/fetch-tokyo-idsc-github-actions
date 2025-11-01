# CLAUDE.md - 東京都感染症発生動向データ自動化プロジェクトガイド

このファイルは、Claude Code（claude.ai/code）が本プロジェクトで効率的に作業するためのガイドラインを提供します。

## 最終更新日

2025-11-01

## バージョン

1.1.0

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

# ソースコード
src/
├── fetchers/               # データ取得モジュール
│   ├── base_fetcher.py    # 基本フェッチャー
│   └── enhanced_fetcher.py # 拡張フェッチャー
├── managers/               # 管理モジュール
│   ├── config_manager.py   # 設定管理
│   └── storage_manager.py  # ストレージ管理
└── utils/                  # ユーティリティ

# テスト
tests/
├── test_enhanced_fetcher.py
├── test_config_manager.py
└── test_storage_manager.py
```

### 📁 主要スクリプト
```bash
# データ取得
scripts/fetch_data.py       # データ取得メインスクリプト
scripts/check_missing.py    # 欠番チェックユーティリティ

# パッケージ管理
pyproject.toml              # プロジェクト設定とパッケージ定義
uv.lock                     # 依存関係のロックファイル
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
# テストスイートの実行
uv run pytest

# カバレッジレポート付きテスト
uv run pytest --cov=src --cov-report=html

# データ取得のドライラン
uv run python scripts/fetch_data.py --dry-run

# 欠番チェック
uv run python scripts/check_missing.py data/raw
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

### 進捗概要（2025-11-01時点）
- **仕様定義**: 完了 ✅
- **GitHub Actions設定**: 完了 ✅（データ取得とテストワークフロー）
- **データ取得モジュール**: 完了 ✅（基本・拡張フェッチャー実装済み）
- **データ処理モジュール**: 完了 ✅（ストレージ管理、設定管理実装済み）
- **自動化ワークフロー**: 完了 ✅（週次自動実行設定済み）
- **テストスイート**: 完了 ✅（50テスト、カバレッジ設定済み）

### システムステータス
- **本番稼働準備完了**
- 初回実行時は2000年からの全データ取得を推奨
- 以降は週次の増分更新で運用

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

## 2. パッケージ管理ガイドライン

### 2.1 uvの使用（必須）

このプロジェクトでは、Pythonパッケージ管理に**uvを必ず使用**してください。pipやpoetryは使用しません。

#### インストールと基本コマンド
```bash
# uvのインストール（初回のみ）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存関係のインストール
uv sync

# 開発用依存関係も含めてインストール
uv sync --all-extras

# パッケージの追加
uv add requests  # 本番用
uv add --dev pytest  # 開発用

# パッケージの削除
uv remove requests

# スクリプトの実行
uv run python scripts/fetch_data.py
uv run pytest

# 仮想環境のアクティベート（通常は不要）
source .venv/bin/activate
```

#### 重要な原則
- **絶対にpip installを直接使わない**
- **pyproject.tomlがマスター定義**
- **uv.lockファイルは必ずコミット**（再現性の保証）
- **GitHub Actionsでもuvを使用**（高速化と再現性）

### 2.2 依存関係の管理

```toml
# pyproject.toml での依存関係定義
[project]
dependencies = [
    "requests>=2.31.0",  # 本番用依存関係
    "PyYAML>=6.0.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",  # 開発用依存関係
    "pytest-cov>=4.1.0",
]
```

## 3. テスト方針

### 3.1 t-wadaアプローチの採用

このプロジェクトでは、和田卓人（t-wada）氏が提唱するテスト駆動開発（TDD）の原則を採用します。

#### 基本原則
1. **テストファーストではなくテストと共に**
   - 実装とテストを交互に書く
   - Red → Green → Refactor のサイクル

2. **AAA（Arrange-Act-Assert）パターン**
   ```python
   def test_fetch_with_retry_success(self):
       # Arrange: 準備
       mock_response = Mock()
       mock_response.status_code = 200

       # Act: 実行
       result = self.fetcher.fetch_with_retry(...)

       # Assert: 検証
       self.assertTrue(result.success)
   ```

3. **テストの独立性**
   - 各テストは独立して実行可能
   - テスト間の依存関係を排除
   - setUp/tearDownで状態を管理

4. **テスト名は仕様書**
   ```python
   def test_重複データは保存されない(self):
   def test_エラー時は最大3回リトライする(self):
   def test_レート制限に達したら待機する(self):
   ```

### 3.2 テストの実行方法

```bash
# 全テスト実行
uv run pytest

# カバレッジ付き実行
uv run pytest --cov=src --cov-report=html

# 特定のテストのみ実行
uv run pytest tests/test_enhanced_fetcher.py

# 詳細出力
uv run pytest -vv

# 並列実行（高速化）
uv run pytest -n auto
```

### 3.3 モックとテストダブル

```python
# 外部APIはモック化
@patch('requests.Session.post')
def test_api_call(self, mock_post):
    mock_post.return_value.status_code = 200

# 時間依存のテストは時刻を固定
@patch('time.time', return_value=1234567890)
def test_timestamp(self, mock_time):
    pass
```

## 4. 開発ワークフロー

### 4.1 実装アプローチ

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

### 4.2 開発チェックリスト

- [x] TokyoEpidemicSurveillanceFetcherクラスの実装
- [x] エラーハンドリングとリトライロジック
- [x] データ保存とファイル管理
- [x] メタデータとログ記録
- [x] GitHub Actionsワークフローの作成
- [x] 通知システムの設定
- [x] テストとバリデーション
- [x] ドキュメント作成

### 4.3 新機能追加時のチェックリスト

- [ ] pyproject.tomlに依存関係を追加（uvを使用）
- [ ] テストを先に書く（TDDアプローチ）
- [ ] AAA パターンでテストを構造化
- [ ] モックを使用して外部依存を排除
- [ ] カバレッジ80%以上を維持
- [ ] GitHub Actionsでテスト自動実行を確認

## 5. コーディング規約

### 5.1 Pythonコード規約

- **スタイルガイド**: PEP 8準拠
- **型ヒント**: Python 3.11+の型アノテーションを使用
- **エラーハンドリング**: 明示的なtry-exceptブロック
- **ログ**: 構造化ログの使用

### 5.2 ファイル命名規則

```python
# データファイル
f"tokyo_epidemic_{data_type}_{start_date}_{end_date}_{timestamp}.csv"

# メタデータ
f"metadata_{timestamp}.json"

# ログファイル
f"fetch_log_{date}.txt"
```

### 5.3 ディレクトリ構造

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

## 6. デバッグとトラブルシューティング

### 6.1 一般的なエラー

| エラー           | 原因                                    | 解決策                          |
| --------------- | -------------------------------------- | ------------------------------ |
| ConnectionError | ネットワーク接続の問題                    | リトライロジックの確認            |
| EncodingError   | Shift_JISエンコーディングの問題          | エンコーディング指定の確認        |
| RateLimitError  | APIレート制限                           | 遅延の追加                      |
| DuplicateError  | 重複データ                              | ハッシュチェックの確認            |

### 6.2 検証スクリプト

```bash
# テスト実行
uv run pytest -vv

# 特定のテストをデバッグ
uv run pytest tests/test_enhanced_fetcher.py::TestEnhancedEpidemicDataFetcher::test_fetch_with_retry_success -vv

# ログ確認
tail -f data/logs/fetch_log_$(date +%Y%m%d).txt
```

## 7. GitHub Actions設定

### 7.1 ワークフローテンプレート

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
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - run: uv python install 3.11
      - run: uv sync
      - run: uv run python scripts/fetch_data.py
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

### 7.2 環境変数とシークレット

```bash
# リポジトリシークレットの設定（必要に応じて）
GITHUB_TOKEN        # 自動コミット用
NOTIFICATION_WEBHOOK # 通知用（オプション）

# 注意: シークレットはログに出力されないよう、GitHub Actionsの::add-mask::を使用してマスクしてください
```

## 8. プロジェクト固有のパターン

### 8.1 データ取得パターン

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

### 8.2 データ検証パターン

```python
# CSVデータの検証
def validate_csv(file_path):
    # エンコーディングチェック
    # カラム数チェック
    # データ型チェック
    # 日付範囲チェック
    pass
```

## 9. 重要な注意事項

- **必ず** データ取得前に既存データをチェックして重複を避ける
- **決して** 個人情報を含むデータを保存しない
- **常に** Shift_JISエンコーディングを維持する
- **定期的に** 古いデータのアーカイブを検討する
- **エラー時は** 必ずログを記録し、必要に応じて通知する

## 10. プロジェクト運用ガイドライン

### 10.1 日次チェック項目

- [ ] GitHub Actionsの実行状況確認
- [ ] エラーログの確認
- [ ] データ整合性チェック

### 10.2 週次メンテナンス

- [ ] データバックアップの確認
- [ ] ストレージ使用量の確認
- [ ] パフォーマンスメトリクスのレビュー
- [ ] テストカバレッジの確認

### 10.3 月次レビュー

- [ ] データ品質レポートの生成
- [ ] システム改善点の検討
- [ ] ドキュメントの更新

## 11. リファレンス

### 11.1 関連ドキュメント

- [東京都感染症発生動向情報システム](https://survey.tmiph.metro.tokyo.lg.jp/)
- [GitHub Actions ドキュメント](https://docs.github.com/ja/actions)
- [Python asyncio ドキュメント](https://docs.python.org/ja/3/library/asyncio.html)

- [uv ドキュメント](https://github.com/astral-sh/uv)
- [pytest ドキュメント](https://docs.pytest.org/)

### 11.2 プロジェクトファイル

- `.kiro/specs/tokyo-epidemic-data-automation/requirements.md` - 要求仕様
- `.kiro/specs/tokyo-epidemic-data-automation/design.md` - 設計書
- `.kiro/specs/tokyo-epidemic-data-automation/tasks.md` - タスク一覧

- `pyproject.toml` - パッケージ定義と設定
- `config/config.yml` - アプリケーション設定

## 12. トラブルシューティングFAQ

**Q: データ取得が失敗する**
A: ネットワーク接続、URLの変更、レート制限を確認してください。

**Q: 文字化けが発生する**
A: Shift_JISエンコーディングが正しく設定されているか確認してください。

**Q: GitHub Actionsが動作しない**
A: ワークフローの権限設定とシークレットの設定を確認してください。

**Q: テストが失敗する**
A: `uv sync --all-extras`で開発用依存関係をインストールしてから`uv run pytest`を実行してください。

**Q: uvコマンドが見つからない**
A: `curl -LsSf https://astral.sh/uv/install.sh | sh`でuvをインストールしてください。

---

# 重要な指示の再確認
求められたことだけを実行し、それ以上もそれ以下もしない。
目的達成に絶対に必要でない限り、ファイルを作成しない。
既存ファイルの編集を新規作成より常に優先する。
ユーザーから明示的に要求されない限り、ドキュメントファイル（*.md）やREADMEファイルを積極的に作成しない。