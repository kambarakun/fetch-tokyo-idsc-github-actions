# 東京都感染症発生動向データ自動収集システム

[![📊 東京都感染症データ取得（手動実行）](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data.yml)
[![📅 毎日データ簡易チェック](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-daily.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-daily.yml)
[![📆 毎週データ徹底チェック](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-weekly.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-weekly.yml)
[![🧪 テストスイート実行](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/test.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/kambarakun/fetch-tokyo-idsc-github-actions/graph/badge.svg)](https://codecov.io/gh/kambarakun/fetch-tokyo-idsc-github-actions)
[![License](https://img.shields.io/badge/License-Non--Commercial-orange.svg)](LICENSE.md)

<!-- start data-statistics -->

## 📊 データ収集状況（自動更新）

### 📅 最新データ

| 項目                     | 値                   |
| ------------------------ | -------------------- |
| **最新週次データ**       | 2025年第50週         |
| **最新月次データ**       | 2025年11月           |
| **最新データ取得日時**   | 2025-12-18 08:10 JST |
| **ファイル最終更新日時** | 2025-12-20 18:38 JST |

### 📈 収集統計

| 項目               | 値                          |
| ------------------ | --------------------------- |
| **総データ件数**   | 7,985件                     |
| **週次データ期間** | 2000年第1週 〜 2025年第50週 |
| **月次データ期間** | 2000年1月 〜 2025年11月     |
| **収集週数**       | 1,354週                     |
| **収集月数**       | 311ヶ月                     |
| **データ種別数**   | 9種類                       |

### 📋 データ種別内訳

| データ種別         | 件数    |
| ------------------ | ------- |
| 定点週次・保健所別 | 1,354件 |
| 全数週次           | 1,354件 |
| 定点週次・年齢群   | 1,354件 |
| 定点週次・医療圏別 | 1,341件 |
| 定点週次・性別     | 1,341件 |
| 定点月次・年齢群   | 311件   |
| 定点月次・保健所別 | 311件   |
| 定点月次・医療圏別 | 311件   |
| 定点月次・性別     | 308件   |

> 💡 このセクションは `scripts/update_readme_stats.py` により自動生成されています。

<!-- end data-statistics -->

東京都感染症発生動向情報システムから定期的にデータを自動取得・保存するGitHub Actionsベースのシステムです。

## 📋 主な機能

- 🔄 **自動収集**: GitHub Actionsによる2種類の自動実行
  - **📅 毎日データ簡易チェック**: 毎日17:00 JST - 最新週＋前週の週次データ、当月＋前月の月次データを確認・取得
  - **📆 毎週データ徹底チェック**: 毎週木曜17:30 JST - 現在年の全データ（週次・月次）を包括的チェック（1月は前年分も含む）
- 🔍 **重複検出**: SHA256ハッシュによるデータ整合性検証
- 🔁 **リトライ機能**: エラー時の自動リトライ（最大3回）
- 📝 **メタデータ管理**: 各データファイルの収集情報を記録
- 🚨 **エラー通知**: GitHub Issuesによる自動通知
- 📊 **増分更新**: 既存データをスキップして新規データのみ取得
- 🎯 **未発表データ自動除外**: 全て0の未発表データを自動検出してスキップ（可視化・分析の品質確保）
- 🔀 **自動PR作成**: データ更新時に自動でPull Request作成
- ✨ **自動マージ**: データ検証成功時にPRを自動的にマージ

## 📊 データ構造とダウンロード内容

### 収集データタイプ（9種類）

本システムは以下の9種類のデータを自動収集します：

| データタイプ                          | 報告形式 | 期間 | 分類             | 説明                                         |
| ------------------------------------- | -------- | ---- | ---------------- | -------------------------------------------- |
| **sentinel_weekly_gender**            | 定点     | 週次 | 性別             | 定点あたり患者報告数（性別）                 |
| **sentinel_weekly_age**               | 定点     | 週次 | 年齢群           | 定点あたり患者報告数（年齢群）               |
| **sentinel_weekly_health_center**     | 定点     | 週次 | 保健所別         | 定点あたり患者報告数（保健所別）             |
| **sentinel_weekly_medical_district**  | 定点     | 週次 | 二次保健医療圏別 | 定点あたり患者報告数（二次保健医療圏別）     |
| **notifiable_weekly**                 | 全数     | 週次 | 全数把握疾患     | 感染症患者報告数（全数把握疾患）             |
| **sentinel_monthly_gender**           | 定点     | 月次 | 性別             | 月別定点あたり患者報告数（性別）             |
| **sentinel_monthly_age**              | 定点     | 月次 | 年齢群           | 月別定点あたり患者報告数（年齢群）           |
| **sentinel_monthly_health_center**    | 定点     | 月次 | 保健所別         | 月別定点あたり患者報告数（保健所別）         |
| **sentinel_monthly_medical_district** | 定点     | 月次 | 二次保健医療圏別 | 月別定点あたり患者報告数（二次保健医療圏別） |

### データディレクトリ構造

```text
data/
├── raw/                                        # 生データ（Shift_JIS エンコーディング）
│   ├── .metadata/                             # メタデータ保存用
│   │   ├── hash_index.json                    # 重複チェック用ハッシュインデックス
│   │   └── *.json                             # 各データファイルのメタデータ
│   ├── sentinel_weekly_gender_2025_01.csv     # 2025年第1週の性別データ
│   ├── sentinel_weekly_age_2025_01.csv        # 2025年第1週の年齢群データ
│   ├── notifiable_weekly_2025_01.csv          # 2025年第1週の全数把握データ
│   └── sentinel_monthly_age_2025_01.csv       # 2025年1月の月次年齢群データ
├── processed/                                  # 処理済みデータ（UTF-8、性別分割済み）
│   ├── .metadata/                             # 処理ログ
│   │   └── processing_log.json                # 処理履歴
│   ├── normalized_notifiable_weekly_2000_01.csv              # 全数報告（UTF-8、メタデータ除去）
│   ├── normalized_sentinel_weekly_age_male_2000_01.csv       # 定点・年齢群・男性（UTF-8）
│   ├── normalized_sentinel_weekly_age_female_2000_01.csv     # 定点・年齢群・女性（UTF-8）
│   ├── normalized_sentinel_weekly_age_total_2000_01.csv      # 定点・年齢群・合計（UTF-8、元データの値を検証済み）
│   ├── normalized_sentinel_weekly_medical_district_male_2000_01.csv   # 定点・医療圏・男性（UTF-8）
│   ├── normalized_sentinel_weekly_medical_district_female_2000_01.csv # 定点・医療圏・女性（UTF-8）
│   └── normalized_sentinel_weekly_gender_2000_01.csv         # 定点・性別（UTF-8、性別列形式のため分割なし）
└── logs/                                       # ログファイル
```

### ファイル命名規則

**生データ（data/raw/）:**

- **共通パターン**: `{data_type}_{year}_{period:02d}.csv`
  - 週次データ例: `sentinel_weekly_gender_2025_01.csv` (2025年第1週)
  - 月次データ例: `sentinel_monthly_age_2025_01.csv` (2025年1月)
  - 全数把握例: `notifiable_weekly_2025_01.csv` (2025年第1週)

**処理済みデータ（data/processed/）:**

- **全数報告**: `normalized_{data_type}_{year}_{period}.csv`
  - 例: `normalized_notifiable_weekly_2000_01.csv`
- **定点監視（性別分割あり）**: `normalized_{data_type}_{gender}_{year}_{period}.csv`
  - 例: `normalized_sentinel_weekly_age_male_2000_01.csv`
  - gender: `male` (男性), `female` (女性), `total` (男女合計、ただし`medical_district`は出力されない）
- **定点監視（分割なし）**: `normalized_{data_type}_{year}_{period}.csv`
  - 例: `normalized_sentinel_weekly_gender_2000_01.csv`

### 🔄 データ処理の詳細

`data/processed/` ディレクトリには、`data/raw/` の生データを以下の処理を施したファイルが格納されます：

**処理内容:**

1. **エンコーディング変換**: Shift_JIS → UTF-8
   - **raw/**: Shift_JIS エンコーディング（東京都IDSCの元データ形式を維持）
   - **processed/**: UTF-8 エンコーディング（Python、R、Excel等の解析ツールで扱いやすい形式）
2. **メタデータ除去**: ヘッダー情報（集計期間等）や注釈行（`*` で始まる行）を除去し、純粋なデータ部分のみを抽出
3. **性別データ分割**: 性別セクション（男性・女性・男女合計）がある場合、最大3つのファイルに分割
   - 例: `sentinel_weekly_age_2000_01.csv` → `normalized_sentinel_weekly_age_male_2000_01.csv`, `normalized_sentinel_weekly_age_female_2000_01.csv`, `normalized_sentinel_weekly_age_total_2000_01.csv`
   - `medical_district`の場合: 元データにtotalセクションが含まれないため、male と female のみ出力
4. **男女合計の検証**:
   - 元データにtotalセクションがある場合: male + female = total の一致を検証
   - 生データを尊重し、totalの推定計算は行わない

**ファイル形式:**

- すべて UTF-8 エンコーディング
- CSV形式（ヘッダー行 + データ行）
- メタデータ・注釈なし（純粋なデータのみ）

**注意事項:**

- **生データ至上主義**: 元データに存在しないデータは推定計算で生成しません
- `sentinel_*_medical_district`: 元データにtotalセクションが含まれていないため、**totalファイルは出力されません**（male, female のみ）
- 他のデータタイプ（`age`, `health_center` 等）: 元データのtotalを使用し、male + female との一致を検証
- 検証時、定点数の列など加算が意味をなさない列は検証対象外です

**年齢別データの注釈（2000年〜現在、全期間共通）:**

年齢別データ（`sentinel_*_age`）には、東京都による以下の注釈があります。データ利用時はこれらに従って解釈してください。

| 注釈                                                                                                             | 対象疾患                | 内容                                                              |
| ---------------------------------------------------------------------------------------------------------------- | ----------------------- | ----------------------------------------------------------------- |
| `*急性呼吸器感染症（ARI）の「〜5ヶ月」は「0歳」、「1歳」は「1〜4歳」、「5歳」は「5〜9歳」と読み替えてください。` | 急性呼吸器感染症（ARI） | 年齢グループ化されており、該当しない年齢帯には`*`（非該当）が入る |
| `*小児科定点把握対象疾患のうち「20-29歳」は「20歳以上」と読み替えてください。`                                   | 小児科定点把握対象疾患  | 20歳以上は単一グループとして報告                                  |
| `*眼科疾患のうち、「70-79歳」は「70歳以上」と読み替えてください。`                                               | 眼科疾患                | 70歳以上は単一グループとして報告                                  |

**`*`（アスタリスク）マークについて:**

- `*`は**データなし・非該当**を意味します（「0件」ではありません）
- 主にARIカラムで、年齢グループ化により該当しない年齢帯に出現
- 生データ至上主義に基づき、`*`はそのまま保持されます

**データ分析時の`*`の扱い:**

データ分析時は以下のいずれかの方法で`*`を処理してください：

1. **欠損値として扱う**: `*`をNaN/NAに変換し、集計から除外
2. **該当年齢帯を参照**: 注釈に従い、グループ化された年齢帯のデータを使用
   - 例: ARIの「2歳」データが必要な場合 → 「1歳 (=1〜4歳)」のデータを参照
3. **行をフィルタリング**: `*`を含む行を除外して分析

## 🔄 GitHub Actionsワークフロー

### 🤖 自動実行ワークフロー

本システムは2種類の自動実行ワークフローを備えています：

| ワークフロー                  | 実行タイミング       | 内容                                                                                                      | 自動マージ        |
| ----------------------------- | -------------------- | --------------------------------------------------------------------------------------------------------- | ----------------- |
| **📅 毎日データ簡易チェック** | 毎日 17:00 JST       | 最新週＋前週の週次データ、当月＋前月の月次データをチェック・取得<br>データの定期更新を迅速に検出          | ✅ 有効           |
| **📆 毎週データ徹底チェック** | 毎週木曜日 17:30 JST | 現在年の全データ（週次・月次）を包括的にチェック<br>（1月は前年分も含む）<br>欠落データの補完と整合性確認 | ✅ 検証成功時のみ |

> 💡 **自動マージ機能**: データ更新PRは自動的にマージされます（週次はデータ検証成功時のみ）

### 📊 手動実行

必要に応じて手動でもワークフローを実行できます：

1. GitHub リポジトリの **Actions** タブを開く
2. 実行したいワークフローを選択：
   - **📊 東京都感染症データ取得（手動実行）** - 汎用データ取得
   - **📅 毎日データ簡易チェック** - 最新週＋前週、当月＋前月
   - **📆 毎週データ徹底チェック** - 現在年の全データ（週次・月次、1月は前年分も含む）
3. **"Run workflow"** をクリック
4. 必要に応じてパラメータを設定して実行

### ワークフロー一覧

#### データ収集ワークフロー

| ワークフロー名                            | ファイル                | 用途                                     | トリガー                          |
| ----------------------------------------- | ----------------------- | ---------------------------------------- | --------------------------------- |
| **📊 東京都感染症データ取得（手動実行）** | `fetch-data.yml`        | 汎用的なデータ取得（全期間対応）         | 手動実行のみ                      |
| **📅 毎日データ簡易チェック**             | `fetch-data-daily.yml`  | 最新2週間・2ヶ月分の更新確認（毎日実行） | 毎日17:00 JST または 手動実行     |
| **📆 毎週データ徹底チェック**             | `fetch-data-weekly.yml` | 全データ（週次・月次）の包括的チェック   | 毎週木曜17:30 JST または 手動実行 |

#### 開発・CI/CDワークフロー

| ワークフロー名               | ファイル                 | 用途                   | トリガー                           |
| ---------------------------- | ------------------------ | ---------------------- | ---------------------------------- |
| **🧪 テストスイート実行**    | `test.yml`               | 自動テスト実行         | プッシュ または PR または 手動実行 |
| **🔍 Claude コードレビュー** | `claude-code-review.yml` | AIによるコードレビュー | PR作成・更新時                     |
| **🤖 Claude Code 統合**      | `claude.yml`             | Claude AIとの統合      | Issue/PRコメント                   |

## ⚙️ 必要な設定

### GitHub Actions権限設定（重要）

このシステムが自動的にPull Requestを作成してデータを更新するためには、リポジトリ管理者による以下の設定が**必須**です：

1. **Settings → Actions → General → Workflow permissions** へ移動
2. **「Read and write permissions」** を選択
3. **「☑ Allow GitHub Actions to create and approve pull requests」** をチェック
4. **「Save」** をクリック

> ⚠️ この設定を行わないと、データ取得は成功してもPR作成でエラーになります

## 🚀 開発クイックスタート

### リポジトリのセットアップ

```bash
# フォークまたはクローン
git clone https://github.com/kambarakun/fetch-tokyo-idsc-github-actions.git
cd fetch-tokyo-idsc-github-actions
```

上記の**GitHub Actions権限設定**を行った後、自動実行ワークフローが動作を開始します。

### 🖥️ ローカル開発環境

開発やテスト用にローカル環境をセットアップする場合：

#### 前提条件

- Python 3.11以上
- [uv](https://github.com/astral-sh/uv) パッケージマネージャー

#### インストール

```bash
# uvのインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存関係のインストール
uv sync

# 開発用依存関係も含める場合
uv sync --all-extras
```

#### ローカルでのデータ取得

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

#### 開発者向けコマンド

```bash
# pre-commitフックのインストール
uv run pre-commit install

# コード品質チェック
uv run pre-commit run --all-files

# テスト実行
uv run pytest

# カバレッジレポート付きテスト
uv run pytest --cov=src --cov-report=html

# 特定のテストのみ
uv run pytest tests/test_enhanced_fetcher.py
```

### 🛠️ 設定ファイル

`config/config.yml` で以下の詳細設定が可能です：

#### 収集設定

- **batch_size**: 一度に処理するファイル数（デフォルト: 50）
- **start_year/end_year**: データ収集期間
- **data_types**: 収集対象のデータタイプリスト
- **incremental_mode**: 増分収集モード（既存データをスキップ）

#### ストレージ設定

- **base_directory**: 生データ保存先（デフォルト: `data/raw`）
- **keep_shift_jis**: Shift_JISエンコーディングの維持（デフォルト: true）
- **commit_message_template**: コミットメッセージのテンプレート

#### 品質管理

- **file_size_limits**: CSVファイルのサイズ制限（100B - 10MB）
- **anomaly_detection_enabled**: 異常検出の有効化
- **quarantine_directory**: 隔離ディレクトリ

#### 通知設定

- **github_issues_enabled**: エラー時のIssue自動作成
- **issue_labels**: 自動作成されるIssueのラベル
- **max_issues_per_day**: 1日あたりの最大Issue作成数

#### Pull Request設定

自動PR作成は `.github/workflows/fetch-data.yml` で制御されます。config.ymlでの直接設定はサポートしていませんが、ワークフロー内で以下の形式が使用されます：

```yaml
# PRタイトル形式（ワークフロー内で自動生成）
PR_TITLE: "データ更新: YYYY-MM-DD (N CSV files)"

# PR本文テンプレート（ワークフロー内で定義）
PR_BODY: |
  ## 🤖 自動データ更新
  ### 📊 更新内容
  - 実行日時: YYYY-MM-DD
  - 対象期間: START_YEAR - END_YEAR
  - データタイプ: [対象データ種別]
  - 変更CSVファイル数: N

# 自動付与されるラベル
PR_LABELS:
  - data-update # データ更新PR用
  - automated # 自動生成PR用
```

カスタマイズが必要な場合は、`.github/workflows/fetch-data.yml` の該当箇所を直接編集してください。

## 📝 ライセンスおよび利用規約

⚠️ **重要**: このプロジェクトは **ソフトウェア** と **データ** で異なる利用条件が適用されます。

### ソフトウェア部分

このプロジェクトの **ソースコードおよびスクリプト** については、作者（kambarakun）が著作権を保有し、非商用目的での利用を許可しています。

- 対象: `src/`, `scripts/`, `tests/`, `.github/`, 設定ファイル等
- 詳細: [LICENSE.md](LICENSE.md)
- **商用利用: 禁止**
- 非商用利用: 自由に使用・改変・再配布可能（著作権表示を保持すること）

### データ部分（data/ディレクトリ）

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
