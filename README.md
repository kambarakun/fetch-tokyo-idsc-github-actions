# 東京都感染症発生動向データ自動収集システム

東京都感染症発生動向情報システムから定期的にデータを自動取得するシステムです。

## 📋 概要

- 東京都の感染症発生動向データを週次で自動収集
- GitHub Actionsによる自動実行（毎週月曜19:00 JST）
- 9種類のデータタイプに対応
- エラーハンドリングとリトライ機能
- データ整合性検証（SHA256ハッシュ）

## 🚀 インストール

### uvを使用（完全推奨）

```bash
# プロジェクトのクローン
git clone https://github.com/kambarakun/fetch-tokyo-idsc-github-actions.git
cd fetch-tokyo-idsc-github-actions

# 依存関係のインストール
uv sync

# 開発用依存関係も含める場合
uv sync --all-extras

# pre-commitフックのインストール（開発者向け）
uv run pre-commit install
```

### pre-commitによるコード品質管理

開発時はpre-commitを使用してコード品質を維持します：

```bash
# 初回設定（必須）
uv run pre-commit install

# すべてのファイルに対して手動実行
uv run pre-commit run --all-files

# 特定のフックのみ実行
uv run pre-commit run black --all-files
uv run pre-commit run isort --all-files

# フックの自動更新
uv run pre-commit autoupdate
```

pre-commitによる自動チェック項目：

- ✅ Pythonコードフォーマット（black, isort）
- ✅ コード品質チェック（flake8, mypy）
- ✅ ファイル末尾改行・空白削除
- ✅ YAML/JSON構文チェック
- ✅ シークレット検出
- ✅ 大きなファイルの追加防止

## 📊 使用方法

### ローカル実行

```bash
# 最新データのみ取得
uv run python scripts/fetch_data.py

# 指定期間のデータ取得
uv run python scripts/fetch_data.py --start-year 2000 --end-year 2025

# ドライラン（テスト実行）
uv run python scripts/fetch_data.py --dry-run

# 欠番チェック
uv run python scripts/check_missing.py data/raw
```

### GitHub Actions

自動実行：

- 毎週月曜日 19:00 JST に自動実行

手動実行：

1. GitHub リポジトリの Actions タブを開く
2. "Fetch Tokyo Epidemic Data" ワークフローを選択
3. "Run workflow" をクリック
4. 必要に応じてパラメータを設定して実行

## 📁 データ構造

収集されたデータは以下の構造で保存されます：

```
data/
├── raw/                    # 生データ（Shift_JIS）
│   └── 2025/
│       └── 01/
│           └── week_01/
│               ├── sentinel_weekly_gender_2025_1_*.csv
│               └── metadata.json
├── processed/             # 処理済みデータ
└── logs/                  # ログファイル
```

## 🛠️ 設定

`config/config.yml` で詳細な設定が可能：

- データ収集間隔
- 対象データタイプ
- エラー通知設定
- ストレージ設定

## 🧪 テスト

```bash
# テスト実行
uv run pytest

# カバレッジレポート付き
uv run pytest --cov=src --cov-report=html
```

## 📝 ライセンス

MIT

## 🔗 関連情報

- データソース: [東京都感染症発生動向情報](https://survey.tmiph.metro.tokyo.lg.jp/epidinfo/epimenu.do)
- 設計書: `.kiro/specs/tokyo-epidemic-data-automation/`
