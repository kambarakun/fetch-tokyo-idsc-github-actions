# 東京都感染症発生動向データ自動収集システム

[![Fetch Tokyo Epidemic Data](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

東京都感染症発生動向情報システムから定期的にデータを自動取得・保存するGitHub Actionsベースのシステムです。

## 📊 データ構造とダウンロード内容

### 収集データタイプ（9種類）

本システムは以下の9種類のデータを自動収集します：

#### 週次データ（Weekly）

- **sentinel_weekly_gender** - 定点あたり患者報告数（性別）
- **sentinel_weekly_age** - 定点あたり患者報告数（年齢群）
- **sentinel_weekly_health_center** - 定点あたり患者報告数（保健所別）
- **sentinel_weekly_medical_district** - 定点あたり患者報告数（二次保健医療圏別）
- **notifiable_weekly** - 感染症患者報告数（全数把握疾患）

#### 月次データ（Monthly）

- **sentinel_monthly_gender** - 月別定点あたり患者報告数（性別）
- **sentinel_monthly_age** - 月別定点あたり患者報告数（年齢群）
- **sentinel_monthly_health_center** - 月別定点あたり患者報告数（保健所別）
- **sentinel_monthly_medical_district** - 月別定点あたり患者報告数（二次保健医療圏別）

### データディレクトリ構造

```
data/
├── raw/                                        # 生データ（Shift_JIS）
│   ├── .metadata/                             # メタデータ保存用
│   │   ├── hash_index.json                    # 重複チェック用ハッシュインデックス
│   │   └── *.json                             # 各データファイルのメタデータ
│   ├── sentinel_weekly_gender_2025_01.csv     # 2025年第1週の性別データ
│   ├── sentinel_weekly_age_2025_01.csv        # 2025年第1週の年齢群データ
│   ├── notifiable_weekly_weekly_2025_01.csv   # 2025年第1週の全数把握データ
│   └── sentinel_monthly_age_monthly_2025_01.csv # 2025年1月の月次年齢群データ
├── processed/                                  # 処理済みデータ
└── logs/                                       # ログファイル
```

### ファイル命名規則

- **週次データ**: `{data_type}_{year}_{week:02d}.csv`
  - 定点サーベイランス: `sentinel_weekly_{type}_{year}_{week:02d}.csv`
  - 全数把握: `notifiable_weekly_weekly_{year}_{week:02d}.csv`
  - 例: `sentinel_weekly_gender_2025_01.csv` (2025年第1週)
- **月次データ**: `{data_type}_monthly_{year}_{month:02d}.csv`
  - 例: `sentinel_monthly_age_monthly_2025_01.csv` (2025年1月)

## 📋 主な機能

- 🔄 **自動収集**: GitHub Actionsによる週次自動実行（毎週月曜19:00 JST）
- 🔍 **重複検出**: SHA256ハッシュによるデータ整合性検証
- 🔁 **リトライ機能**: エラー時の自動リトライ（最大3回）
- 📝 **メタデータ管理**: 各データファイルの収集情報を記録
- 🚨 **エラー通知**: GitHub Issuesによる自動通知
- 📊 **増分更新**: 既存データをスキップして新規データのみ取得

## ⚙️ 必要な設定

### GitHub Actions権限設定（重要）

このシステムが自動的にPull Requestを作成してデータを更新するためには、リポジトリ管理者による以下の設定が**必須**です：

1. **Settings → Actions → General → Workflow permissions** へ移動
2. **「Read and write permissions」** を選択
3. **「☑ Allow GitHub Actions to create and approve pull requests」** をチェック
4. **「Save」** をクリック

> ⚠️ この設定を行わないと、データ取得は成功してもPR作成でエラーになります

## 🚀 クイックスタート

### 前提条件

- Python 3.11以上
- [uv](https://github.com/astral-sh/uv) パッケージマネージャー

### インストール

```bash
# プロジェクトのクローン
git clone https://github.com/kambarakun/fetch-tokyo-idsc-github-actions.git
cd fetch-tokyo-idsc-github-actions

# uvのインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存関係のインストール
uv sync

# 開発用依存関係も含める場合
uv sync --all-extras
```

### GitHub Actions（推奨）

自動実行：

- 毎週月曜日 19:00 JST に自動実行されます

手動実行：

1. GitHub リポジトリの Actions タブを開く
2. "Fetch Tokyo Epidemic Data" ワークフローを選択
3. "Run workflow" をクリック
4. 必要に応じてパラメータを設定して実行

## 🖥️ ローカル実行（オプション）

開発やテスト用にローカルでも実行可能です：

### 基本的な使用方法

```bash
# 最新データのみ取得
uv run python scripts/fetch_data.py

# 指定期間のデータ取得（例: 2000年〜2025年）
uv run python scripts/fetch_data.py --start-year 2000 --end-year 2025

# ドライラン（テスト実行、実際のダウンロードは行わない）
uv run python scripts/fetch_data.py --dry-run

# 欠番チェック
uv run python scripts/check_missing.py data/raw
```

### 開発者向けコマンド

```bash
# pre-commitフックのインストール
uv run pre-commit install

# コード品質チェック
uv run pre-commit run --all-files

# テスト実行
uv run pytest

# カバレッジレポート付きテスト
uv run pytest --cov=src --cov-report=html
```

## 🛠️ 設定

`config/config.yml` で詳細な設定が可能です：

- **収集設定**: 対象データタイプ、期間、バッチサイズ
- **ストレージ設定**: 保存先ディレクトリ、エンコーディング
- **品質管理**: ファイルサイズ制限、異常検出
- **通知設定**: GitHub Issues、エラー通知

## 🧪 テスト

```bash
# 全テスト実行
uv run pytest

# カバレッジレポート付き
uv run pytest --cov=src --cov-report=html

# 特定のテストのみ
uv run pytest tests/test_enhanced_fetcher.py
```

## 📝 ライセンス

### ソフトウェアライセンス

このプロジェクトのソースコードは MIT ライセンスの下で公開されています。

[MIT License](LICENSE)

```
MIT License

Copyright (c) 2025 kambarakun

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### データの利用規約と著作権

⚠️ **重要**: 本システムで収集されるデータの著作権は **東京都** および **東京都健康安全研究センター** に帰属します。

#### データ提供元

- **機関**: 東京都健康安全研究センター（Tokyo Metropolitan Institute of Public Health）
- **データソース**: [東京都感染症発生動向情報システム](https://survey.tmiph.metro.tokyo.lg.jp/)
- **利用規約**: [東京都健康安全研究センター ご利用にあたって](https://www.tmiph.metro.tokyo.lg.jp/riyou/)

#### データ利用時の注意事項

- 収集されたデータの利用は、東京都健康安全研究センターの利用規約に従ってください
- 著作権法上認められた「私的使用のための複製」や「引用」を除き、無断での複製・転用は禁止されています
- **商用利用の禁止**: 商品のパンフレットや商品紹介ホームページなど、商用目的での利用は認められていません
- データを印刷物・電子媒体・放送等で利用する場合は、事前に東京都健康安全研究センターへの相談が必要です
- 本プロジェクトはデータの収集・管理を自動化するツールであり、データ自体の権利を主張するものではありません

#### 免責事項

- データの完全性・正確性に対する保証はありません
- データは予告なしに変更または削除される可能性があります
- データ利用により生じた損失に関して、本プロジェクトは一切責任を負いません

#### お問い合わせ

データ利用に関する詳細なお問い合わせは、直接東京都健康安全研究センターへご連絡ください：

- 住所: 〒169-0073 東京都新宿区百人町三丁目24番1号
- 電話: 03-3363-3231（代表）

## 🔗 関連情報

- **データソース**: [東京都感染症発生動向情報](https://survey.tmiph.metro.tokyo.lg.jp/epidinfo/epimenu.do)
- **プロジェクト設計書**: `.kiro/specs/tokyo-epidemic-data-automation/`
- **GitHub リポジトリ**: [fetch-tokyo-idsc-github-actions](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions)

## 🤝 貢献

Issues や Pull Requests を歓迎します。大きな変更を行う場合は、まず Issue を開いて変更内容について議論してください。

## 📧 連絡先

問題や質問がある場合は、[GitHub Issues](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/issues) でお知らせください。
