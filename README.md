# 東京都感染症発生動向データ自動収集システム

[![📊 東京都感染症データ取得(手動実行)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data.yml)
[![📅 毎日データ簡易チェック](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-daily.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-daily.yml)
[![📆 毎週データ徹底チェック](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-weekly.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/fetch-data-weekly.yml)
[![🧪 テストスイート実行](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/test.yml/badge.svg)](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/kambarakun/fetch-tokyo-idsc-github-actions/graph/badge.svg)](https://codecov.io/gh/kambarakun/fetch-tokyo-idsc-github-actions)
[![License](https://img.shields.io/badge/License-Non--Commercial-orange.svg)](LICENSE.md)

東京都感染症発生動向情報システムから定期的にデータを自動取得・保存するGitHub Actionsベースのシステムです。

<!-- start data-statistics -->
<!-- prettier-ignore-start -->
## 📊 データ収集状況 (自動更新)

> 💡 このセクションは `scripts/update_readme_stats.py` により自動生成されています。

### 📅 最新データ

| 項目 | 値 |
|------|-----|
| **最新週次データ** | 2026年第43週 |
| **最新月次データ** | 2026年9月 |
| **最新データ取得日時** | 2026-10-29 17:45 JST |
| **最終データ更新日時** | 2026-10-29 17:44 JST |

> 📝 **日時の説明**
> - **最新データ取得日時**: データ取得処理が最後に実行された日時 (毎日自動実行)
> - **最終データ更新日時**: 東京都IDSCから実際にデータを取得できた日時

### 📊 感染動向の可視化

> 💡 季節性ベースライン (同週/同月の過去5年のうち公表値がある年の平均。本リポジトリで集計) からの乖離率で流行を検知。過去の公表値が無い期間や比較元が0の期間は率を算出せず空白にする。乖離率の順位付けには、実測 5 例未満またはベースライン 1 例未満の期間を使わない。詳細は [docs/charts.md](docs/charts.md)

#### 週次定点 (Sentinel Surveillance - Weekly)

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/sentinel_weekly_absolute.png" alt="週次定点・絶対数" width="100%">
      <p align="center"><sub>定点週次・絶対数 (直近52週・1年間)</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/sentinel_weekly_deviation.png" alt="週次定点・季節性乖離率" width="100%">
      <p align="center"><sub>定点週次・季節性乖離率 (%)</sub></p>
    </td>
  </tr>
</table>

#### 週次全数 (Notifiable Diseases - Weekly)

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/notifiable_weekly_absolute.png" alt="週次全数・絶対数" width="100%">
      <p align="center"><sub>全数報告週次・絶対数 (直近52週・1年間、最新週は速報値)</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/notifiable_weekly_deviation.png" alt="週次全数・季節性乖離率" width="100%">
      <p align="center"><sub>全数報告週次・季節性乖離率 (%、最新週は速報値)</sub></p>
    </td>
  </tr>
</table>

#### 月次定点 (Sentinel Surveillance - Monthly)

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/sentinel_monthly_absolute.png" alt="月次定点・絶対数" width="100%">
      <p align="center"><sub>定点月次・絶対数 (直近12ヶ月)</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/sentinel_monthly_deviation.png" alt="月次定点・季節性乖離率" width="100%">
      <p align="center"><sub>定点月次・季節性乖離率 (%)</sub></p>
    </td>
  </tr>
</table>

### 📈 収集統計

| 項目 | 値 |
|------|-----|
| **総データ件数** | 8,250件 |
| **週次データ期間** | 2000年第1週 - 2026年第43週 |
| **月次データ期間** | 2000年1月 - 2026年9月 |
| **収集週数** | 1,399週 |
| **収集月数** | 321ヶ月 |
| **データ種別数** | 9種類 |

### 📋 データ種別内訳

| データ種別 | 件数 | データ期間 | 欠損 |
|-----------|------|-----------|------|
| 全数週次 | 1,399件 | 2000年第1週-2026年第43週 | なし |
| 定点週次・年齢群 | 1,399件 | 2000年第1週-2026年第43週 | なし |
| 定点週次・保健所別 | 1,399件 | 2000年第1週-2026年第43週 | なし |
| 定点週次・性別 | 1,386件 | 2000年第14週-2026年第43週 | なし |
| 定点週次・医療圏別 | 1,386件 | 2000年第14週-2026年第43週 | なし |
| 定点月次・年齢群 | 321件 | 2000年1月-2026年9月 | なし |
| 定点月次・保健所別 | 321件 | 2000年1月-2026年9月 | なし |
| 定点月次・医療圏別 | 321件 | 2000年1月-2026年9月 | なし |
| 定点月次・性別 | 318件 | 2000年4月-2026年9月 | なし |

### 🔍 データ品質チェック

#### 📁 生データ (raw) の検証

##### ⚠️ 警告 (8250件)

> 💡 CSVファイル内で行ごとにカラム数が異なります。東京都IDSCの元データには注釈行や集計期間情報が含まれているため発生します。

<details>
<summary><strong>[csv_format] Inconsistent column count</strong> (8250件)</summary>

```text
notifiable_weekly_2026_43.csv
sentinel_weekly_age_2026_43.csv
sentinel_weekly_gender_2026_43.csv
sentinel_weekly_health_center_2026_43.csv
sentinel_weekly_medical_district_2026_43.csv
notifiable_weekly_2026_42.csv
sentinel_weekly_age_2026_42.csv
sentinel_weekly_gender_2026_42.csv
sentinel_weekly_health_center_2026_42.csv
sentinel_weekly_medical_district_2026_42.csv
notifiable_weekly_2026_41.csv
sentinel_weekly_age_2026_41.csv
sentinel_weekly_gender_2026_41.csv
sentinel_weekly_health_center_2026_41.csv
sentinel_weekly_medical_district_2026_41.csv
notifiable_weekly_2026_40.csv
sentinel_weekly_age_2026_40.csv
sentinel_weekly_gender_2026_40.csv
sentinel_weekly_health_center_2026_40.csv
sentinel_weekly_medical_district_2026_40.csv
notifiable_weekly_2026_39.csv
sentinel_weekly_age_2026_39.csv
sentinel_weekly_gender_2026_39.csv
sentinel_weekly_health_center_2026_39.csv
sentinel_weekly_medical_district_2026_39.csv
notifiable_weekly_2026_38.csv
sentinel_weekly_age_2026_38.csv
sentinel_weekly_gender_2026_38.csv
sentinel_weekly_health_center_2026_38.csv
sentinel_weekly_medical_district_2026_38.csv
notifiable_weekly_2026_37.csv
sentinel_weekly_age_2026_37.csv
sentinel_weekly_gender_2026_37.csv
sentinel_weekly_health_center_2026_37.csv
sentinel_weekly_medical_district_2026_37.csv
notifiable_weekly_2026_36.csv
sentinel_weekly_age_2026_36.csv
sentinel_weekly_gender_2026_36.csv
sentinel_weekly_health_center_2026_36.csv
sentinel_weekly_medical_district_2026_36.csv
notifiable_weekly_2026_35.csv
sentinel_weekly_age_2026_35.csv
sentinel_weekly_gender_2026_35.csv
sentinel_weekly_health_center_2026_35.csv
sentinel_weekly_medical_district_2026_35.csv
notifiable_weekly_2026_34.csv
sentinel_weekly_age_2026_34.csv
sentinel_weekly_gender_2026_34.csv
sentinel_weekly_health_center_2026_34.csv
sentinel_weekly_medical_district_2026_34.csv
... 他8200件
```

</details>


#### 📊 処理済みデータ (processed) の品質チェック

##### 🔍 データ品質の問題 (1034件)

> 🔍 性別データの合計値検証で不整合が検出されました。男性+女性の合計が、元データの男女合計値と一致しません。

<details>
<summary><strong>gender_sum_consistency</strong> (1034ファイル, 不整合: 11959件)</summary>

```text
sentinel_weekly_medical_district_2026_43.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_42.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_41.csv (不整合: 10件)
sentinel_weekly_medical_district_2026_40.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_39.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_38.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_37.csv (不整合: 14件)
sentinel_weekly_medical_district_2026_36.csv (不整合: 14件)
sentinel_weekly_medical_district_2026_35.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_34.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_33.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_32.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_31.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_30.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_29.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_28.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_27.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_26.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_25.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_24.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_23.csv (不整合: 10件)
sentinel_weekly_medical_district_2026_22.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_21.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_20.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_19.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_18.csv (不整合: 10件)
sentinel_weekly_medical_district_2026_17.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_16.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_15.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_14.csv (不整合: 10件)
sentinel_weekly_medical_district_2026_13.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_12.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_11.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_10.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_09.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_08.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_07.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_06.csv (不整合: 13件)
sentinel_weekly_medical_district_2026_05.csv (不整合: 14件)
sentinel_weekly_medical_district_2026_04.csv (不整合: 12件)
sentinel_weekly_medical_district_2026_03.csv (不整合: 14件)
sentinel_weekly_medical_district_2026_02.csv (不整合: 11件)
sentinel_weekly_medical_district_2026_01.csv (不整合: 12件)
sentinel_weekly_medical_district_2025_52.csv (不整合: 13件)
sentinel_weekly_medical_district_2025_51.csv (不整合: 13件)
sentinel_weekly_medical_district_2025_50.csv (不整合: 12件)
sentinel_weekly_medical_district_2025_49.csv (不整合: 12件)
sentinel_weekly_medical_district_2025_48.csv (不整合: 13件)
sentinel_weekly_medical_district_2025_47.csv (不整合: 14件)
sentinel_weekly_medical_district_2025_46.csv (不整合: 13件)
... 他984ファイル
```

</details>


<!-- prettier-ignore-end -->
<!-- end data-statistics -->

## 📋 主な機能

- 🔄 **自動収集**: GitHub Actionsによる2種類の自動実行
  - **📅 毎日データ簡易チェック**: 毎日17:00 JST - 2週前・前週・当週の週次データと、前月・当月の月次データのうち、未取得の期間を取得 (incremental。年始は前年と当年の両方にこの週・月番号を当てはめるため、前年の同じ番号の期間も対象になる)
  - **📆 毎週データ徹底チェック**: 毎週木曜17:30 JST - 当年の全データ(週次・月次)を強制再取得 (force) して改訂を反映(1月は前年分も含む)
- 🔍 **内容不変の判定**: 同じパスの既存ファイルと SHA256 が一致する再取得は書き直さず、変更として数えない
- 🔁 **リトライ機能**: エラー時の自動リトライ(最大3回)
- 📝 **メタデータ管理**: 各データファイルの収集情報を記録
- 🚨 **エラー通知**: GitHub Issuesによる自動通知
- 📊 **増分更新**: 既存データをスキップして未取得の期間のみ取得 (incremental モード)
- 🎯 **未発表データ自動除外**: 全て0の未発表データを自動検出してスキップ(可視化・分析の品質確保)
- 🔀 **自動PR作成**: `data/raw/` に変更があるときだけ Pull Request を作成 (変更が無ければ PR は作らない)
- ✨ **自動マージ**: 日次・週次の PR は、取得・処理・処理カバレッジ・連続性・メタデータスキーマ・データ検証のゲートをすべて通過したときだけ自動マージ (手動実行のワークフローは既定で自動マージしない) (条件は [GitHub Actionsワークフロー](#-github-actionsワークフロー) を参照)

## 📊 データ構造とダウンロード内容

### 📁 データ構造

#### 収集データタイプ(9種類)

本システムは以下の9種類のデータを自動収集します:

| データタイプ                          | 報告形式 | 期間 | 分類             | 説明                                       |
| ------------------------------------- | -------- | ---- | ---------------- | ------------------------------------------ |
| **sentinel_weekly_gender**            | 定点     | 週次 | 性別             | 定点あたり患者報告数(性別)                 |
| **sentinel_weekly_age**               | 定点     | 週次 | 年齢群           | 定点あたり患者報告数(年齢群)               |
| **sentinel_weekly_health_center**     | 定点     | 週次 | 保健所別         | 定点あたり患者報告数(保健所別)             |
| **sentinel_weekly_medical_district**  | 定点     | 週次 | 二次保健医療圏別 | 定点あたり患者報告数(二次保健医療圏別)     |
| **notifiable_weekly**                 | 全数     | 週次 | 全数把握疾患     | 感染症患者報告数(全数把握疾患)             |
| **sentinel_monthly_gender**           | 定点     | 月次 | 性別             | 月別定点あたり患者報告数(性別)             |
| **sentinel_monthly_age**              | 定点     | 月次 | 年齢群           | 月別定点あたり患者報告数(年齢群)           |
| **sentinel_monthly_health_center**    | 定点     | 月次 | 保健所別         | 月別定点あたり患者報告数(保健所別)         |
| **sentinel_monthly_medical_district** | 定点     | 月次 | 二次保健医療圏別 | 月別定点あたり患者報告数(二次保健医療圏別) |

<details>
<summary>📁 ディレクトリ構造 / ファイル命名規則 / メタデータスキーマ v1.3.0 (クリックして展開)</summary>

#### データディレクトリ構造

```text
data/
├── raw/                                                                 # 生データ (Shift_JIS エンコーディング)
│   ├── .metadata/                                                       # メタデータファイル保存用
│   │   ├── hash_index.json                                              # 旧ハッシュインデックス (残存ファイル。現行コードは読み書きしない)
│   │   └── *.json                                                       # 各データファイルのメタデータ
│   ├── sentinel_weekly_gender_2025_01.csv                               # 2025年第1週の性別データ
│   ├── sentinel_weekly_age_2025_01.csv                                  # 2025年第1週の年齢群データ
│   ├── notifiable_weekly_2025_01.csv                                    # 2025年第1週の全数把握データ
│   └── sentinel_monthly_age_2025_01.csv                                 # 2025年1月の月次年齢群データ
├── processed/                                                           # 処理済みデータ (UTF-8、性別分割済み)
│   ├── .metadata/                                                       # 処理済みファイルのメタデータ
│   │   └── normalized_*.json                                            # 各ファイルの個別メタデータ
│   ├── normalized_notifiable_weekly_2000_01.csv                         # 全数報告 (UTF-8、メタデータ除去)
│   ├── normalized_sentinel_weekly_age_male_2000_01.csv                  # 定点・年齢群・男性 (UTF-8)
│   ├── normalized_sentinel_weekly_age_female_2000_01.csv                # 定点・年齢群・女性 (UTF-8)
│   ├── normalized_sentinel_weekly_age_total_2000_01.csv                 # 定点・年齢群・合計 (UTF-8、元データの値を検証済み)
│   ├── normalized_sentinel_weekly_medical_district_male_2000_14.csv     # 定点・医療圏・男性 (UTF-8)
│   ├── normalized_sentinel_weekly_medical_district_female_2000_14.csv   # 定点・医療圏・女性 (UTF-8)
│   ├── normalized_sentinel_weekly_medical_district_total_2007_01.csv    # 定点・医療圏・合計 (男女合計にデータ行がある期間のみ)
│   └── normalized_sentinel_weekly_gender_2000_01.csv                    # 定点・性別 (UTF-8、性別列形式のため分割なし)
└── logs/                                                                # ログファイル
```

#### ファイル命名規則

**生データ(data/raw/):**

- **共通パターン**: `{data_type}_{year}_{period:02d}.csv`
  - 週次データ例: `sentinel_weekly_gender_2025_01.csv` (2025年第1週)
  - 月次データ例: `sentinel_monthly_age_2025_01.csv` (2025年1月)
  - 全数把握例: `notifiable_weekly_2025_01.csv` (2025年第1週)

**処理済みデータ(data/processed/):**

- **全数報告**: `normalized_{data_type}_{year}_{period}.csv`
  - 例: `normalized_notifiable_weekly_2000_01.csv`
- **定点監視(性別分割あり)**: `normalized_{data_type}_{gender}_{year}_{period}.csv`
  - 例: `normalized_sentinel_weekly_age_male_2000_01.csv`
  - **gender パラメータ**: `male` (男性)、`female` (女性)、`total` (男女合計。`medical_district` は男女合計セクションにデータ行がある期間だけ出力し、セクションがヘッダーのみの 2000年第14週〜2006年第52週の週報では出力しない)
- **定点監視(分割なし)**: `normalized_{data_type}_{year}_{period}.csv`
  - 例: `normalized_sentinel_weekly_gender_2000_01.csv`

#### 📝 メタデータ管理 (v1.3.0)

本システムは各データファイルに対して詳細なメタデータを自動生成・管理しています。
メタデータは `data/raw/.metadata/` (生データ用) と `data/processed/.metadata/` (処理済みデータ用) にファイルごとの JSON として保存されます。
現行のスキーマ版は `src/models/metadata.py` の `METADATA_VERSION` (`1.3.0`)、スキーマ本体は CI が検証に使う [`schemas/metadata-v1.3.schema.json`](schemas/metadata-v1.3.schema.json) です。
既存の processed メタデータには v1.1.0 のまま `quality` を持たないファイルが残っています (2026-10-27 時点で 18,181 件中 13,665 件。再生成は issue #738)。

**v1.3.0の主な変更点:**

- 警告メッセージを統一形式化 (例: `[csv_format] Inconsistent column count`)
- 詳細情報を `verification.details` フィールドに構造化 (例: `details.column_counts`)
- 検索性と集計性の向上

> **💡 既存ユーザー向け注意**: v1.2.0以前のメタデータをv1.3.0に移行する場合は、`migrate-metadata`を使用してください。使い方は `uv run migrate-metadata --help`、スキーマ自体を変更する手順は[docs/development.md](docs/development.md#メタデータスキーマの変更手順)を参照。

**主要フィールドの概要:**

ここでは v1.3.0 の主要フィールドを示します。各フィールドの詳細な定義・仕様は以下を参照してください:

- 完全なスキーマ定義: [`schemas/metadata-v1.3.schema.json`](schemas/metadata-v1.3.schema.json)
- 実装例: [`docs/data_structure_design.md`](docs/data_structure_design.md#メタデータ構造)

**代表的なメタデータファイル:**

- `data/raw/.metadata/sentinel_weekly_age_2025_01.json`: 各データファイルの個別メタデータ
- `data/processed/.metadata/normalized_notifiable_weekly_2000_01.json`: 処理済みファイルの個別メタデータ

##### v1.3.0 メタデータフィールド一覧

**基本情報:**

- `metadata_version`: スキーマバージョン (例: `1.3.0`)
- `name` / `filename` / `path`: 識別子・ファイル名・相対パス
- `profile`: プロファイル種別 (`tokyo-idsc-raw` / `tokyo-idsc-processed`)

**データ特性:**

- `data_type`: データタイプ (例: `sentinel_weekly_gender`)
- `temporal`: 対象期間 (`year` / `period` (週番号または月) / `period_type`)

**ファイル属性:**

- `bytes` / `lines`: ファイルサイズ・行数
- `hash`: SHA256ハッシュ情報
- `encoding`: 文字エンコーディング (Shift_JIS / UTF-8)
- `created` / `modified`: 作成・更新日時 (ISO 8601形式)

**検証・品質:**

- `verification`: ファイル形式の検証結果 (CSV構造、エンコーディング等)
- `quality`: データ内容の品質検証結果 (性別合計の一致等)

**ソース・処理履歴:**

- `sources`: データソース情報 (raw は取得元 URL。2026-10-27 時点の既存 raw メタデータ 8,245 件はすべて空で、既存分の補完は issue #738)
- `_fetch`: データ取得情報 (raw用: `source_url`、`fetch_time_seconds`、`force_overwrite`、`save_all_zero`)
- `_process`: データ処理情報 (processed用: `source_name`、`source_hash`、`processing_time_seconds`、`gender`)

##### メタデータの2つの検証フィールド

| フィールド       | 用途                 | 例                                          |
| ---------------- | -------------------- | ------------------------------------------- |
| **verification** | ファイル形式の検証   | CSVカラム数の不整合                         |
| **quality**      | データ内容の品質検証 | 性別合計値の不整合 (male + female != total) |

##### verification (ファイル形式検証)

- **目的**: ファイル自体の構造的な問題を検出
- **検証項目**: ファイルサイズ、エンコーディング、CSV形式、パス安全性
- **v1.3.0の改善**: 警告メッセージを統一形式化し、詳細情報を`details`フィールドに構造化
  - 例: カラム数不整合の場合、`details.column_counts`に観測された全カラム数を記録

##### quality (データ品質検証)

- **目的**: データ内容の品質問題を検出
- **検証項目**: 性別データの合計値検証 (`male + female = total`)
- **実装**: `src/validators/gender_sum_validator.py`

検証スキーマの詳細は [`schemas/metadata-v1.3.schema.json`](schemas/metadata-v1.3.schema.json) を参照してください。

</details>

### 🔄 データ処理の詳細

`data/processed/` ディレクトリには、`data/raw/` の生データを以下の処理フローで変換したファイルが格納されます。

<details>
<summary>🔄 処理フロー図 (Mermaid) / ステップ詳細 / 注意事項 (クリックして展開)</summary>

#### 処理フロー図

```mermaid
flowchart TD
    Start[data/raw/<br/>生データ] --> Input[入力データ<br/>Shift_JIS<br/>メタデータ含む<br/>性別混在]
    Input --> Step1[1. エンコーディング変換<br/>Shift_JIS → UTF-8]
    Step1 --> Step2[2. メタデータ除去<br/>ヘッダー・注釈行を削除]
    Step2 --> Step3{3. 性別分割<br/>必要?}

    Step3 -->|Yes| Split[性別セクション分割<br/>male/female/total]
    Step3 -->|No| NoSplit[分割なし]

    Split --> TotalRows{男女合計セクションに<br/>データ行がある?}
    TotalRows -->|Yes| Validate[4. 品質検証<br/>male + female = total]
    TotalRows -->|No: medical_district の<br/>2000〜2006年の週報| NoTotal[total を出力しない<br/>male / female のみ]
    NoTotal --> Output
    NoSplit --> Output

    Validate --> CheckResult{検証結果}
    CheckResult -->|一致| Output[data/processed/<br/>UTF-8<br/>純粋データ<br/>性別分割済み]
    CheckResult -->|不一致| RecordIssue[quality フィールドに記録]
    RecordIssue --> Output

    style Start fill:#f9f,stroke:#333,stroke-width:2px
    style Input fill:#f9f,stroke:#333,stroke-width:2px
    style Step1 fill:#ddf,stroke:#333,stroke-width:2px
    style Step2 fill:#ddf,stroke:#333,stroke-width:2px
    style Split fill:#ddf,stroke:#333,stroke-width:2px
    style TotalRows fill:#ffd,stroke:#333,stroke-width:2px
    style Validate fill:#bbf,stroke:#333,stroke-width:2px
    style CheckResult fill:#ffd,stroke:#333,stroke-width:2px
    style RecordIssue fill:#ffb,stroke:#333,stroke-width:2px
    style Output fill:#bfb,stroke:#333,stroke-width:2px
```

#### 処理ステップの詳細

**入力(data/raw/):**

- Shift_JIS エンコーディング(東京都IDSCの元データ形式)
- ヘッダー情報・注釈行を含む完全な生データ
- 性別セクション(男性・女性・男女合計)が混在した単一ファイル

**処理ステップ:**

1. **エンコーディング変換**: Shift_JIS → UTF-8
   - Python、R、Excel等の解析ツールで扱いやすい形式に変換
2. **メタデータ除去**: ヘッダー情報(集計期間等)や注釈行(`*` で始まる行)を除去
   - 純粋なデータ部分(ヘッダー行 + データ行)のみを抽出
3. **性別データ分割**: 性別セクション(男性・女性・男女合計)がある場合、最大3つのファイルに分割
   - 例: `sentinel_weekly_age_2000_01.csv` →
     - `normalized_sentinel_weekly_age_male_2000_01.csv`
     - `normalized_sentinel_weekly_age_female_2000_01.csv`
     - `normalized_sentinel_weekly_age_total_2000_01.csv`
   - **例外**: `medical_district` の男女合計セクションは 2000年第14週〜2006年第52週の週報ではヘッダーのみのため、その期間は male と female のみ出力 (2007年以降の週報と全期間の月報は total も出力)
4. **データ品質検証**:
   - total を出力した場合: male + female = total の一致を検証
   - 不整合がある場合、メタデータの `quality` フィールドに記録
   - **生データ至上主義**: totalの推定計算は行わず、元データをそのまま保存

**出力(data/processed/):**

- UTF-8 エンコーディング
- CSV形式(ヘッダー行 + データ行)
- メタデータ・注釈なし(純粋なデータのみ)
- 性別ごとに分割されたファイル(該当する場合)
- 入力・出力・品質検証結果が変わらない再処理では、processed のメタデータを書き直さない

#### 重要な注意事項

- **生データ至上主義**: 元データに存在しないデータは推定計算で生成しません
- **検証の範囲**: 定点数の列など加算が意味をなさない列は検証対象外です
- **medical_districtの特殊性**: 2000年第14週〜2006年第52週の週報は男女合計セクションがヘッダーのみのため、その期間の **totalファイルは出力されません**(male, female のみ)

**年齢別データの注釈(2000年~現在、全期間共通):**

年齢別データ(`sentinel_*_age`)には、東京都による以下の注釈があります。データ利用時はこれらに従って解釈してください。

| 注釈                                                                                                        | 対象疾患               | 内容                                                            |
| ----------------------------------------------------------------------------------------------------------- | ---------------------- | --------------------------------------------------------------- |
| `*急性呼吸器感染症(ARI)の「~5ヶ月」は「0歳」、「1歳」は「1~4歳」、「5歳」は「5~9歳」と読み替えてください。` | 急性呼吸器感染症(ARI)  | 年齢グループ化されており、該当しない年齢帯には`*`(非該当)が入る |
| `*小児科定点把握対象疾患のうち「20-29歳」は「20歳以上」と読み替えてください。`                              | 小児科定点把握対象疾患 | 20歳以上は単一グループとして報告                                |
| `*眼科疾患のうち、「70-79歳」は「70歳以上」と読み替えてください。`                                          | 眼科疾患               | 70歳以上は単一グループとして報告                                |

**`*`(アスタリスク)マークについて:**

- `*`は**データなし・非該当**を意味します(「0件」ではありません)
- 主にARIカラムで、年齢グループ化により該当しない年齢帯に出現
- 生データ至上主義に基づき、`*`はそのまま保持されます

**データ分析時の`*`の扱い:**

データ分析時は以下のいずれかの方法で`*`を処理してください:

1. **欠損値として扱う**: `*`をNaN/NAに変換し、集計から除外
2. **該当年齢帯を参照**: 注釈に従い、グループ化された年齢帯のデータを使用
   - 例: ARIの「2歳」データが必要な場合 → 「1歳 (=1~4歳)」のデータを参照
3. **行をフィルタリング**: `*`を含む行を除外して分析

</details>

## 🔄 GitHub Actionsワークフロー

### 🤖 自動実行ワークフロー

本システムは2種類の自動実行ワークフローを備えています。どちらも共通処理 `_fetch-data-common.yml` (reusable workflow) を呼び出し、取得 → 変更された raw だけを `process-data --files` で処理 → 検証 → PR 作成 → 自動マージ判定の順に進みます。

| ワークフロー                  | 実行タイミング       | 内容                                                                                                                                                                | 自動マージ       |
| ----------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| **📅 毎日データ簡易チェック** | 毎日 17:00 JST       | 2週前・前週・当週の週次データと、前月・当月の月次データのうち未取得の期間を取得 (incremental)<br>年始は前年の同じ番号の週・月も対象<br>データの定期更新を迅速に検出 | ゲート通過時のみ |
| **📆 毎週データ徹底チェック** | 毎週木曜日 17:30 JST | 当年の全データ(週次・月次)を強制再取得 (force)<br>(1月は前年分も含む)<br>上流の改訂を反映し、欠落データを補完                                                       | ゲート通過時のみ |

- **PR の作成**: `data/raw/` に変更があるときだけ `scripts/create_pr.sh` がブランチを push して PR を作ります。タイトルは `データ更新: YYYY-MM-DD - N件 (新規X件/更新Y件)`、ラベルは `data-update` / `automated` と種別ラベル (`daily-update` / `weekly-update` / `data-processing` / `metadata-migration`、手動の `fetch-data.yml` には種別ラベルなし) です。本文の書式は `scripts/create_pr.sh` を参照してください。
- **PR の認証**: 既定では `GITHUB_TOKEN` で push と PR 作成を行うため、データ PR の CI は承認待ちになります。GitHub App への切り替え (既定は無効) は [データ PR の認証](docs/data-pr-authentication.md) を参照してください。
- **自動マージの条件**: `scripts/auto_merge_gate.sh` が次のゲートをすべて通過したと判定したときだけ、`gh pr merge --auto --squash` を設定します。main のルールセットにはマージ前の必須ステータスチェックが無いため (2026-10-27 時点)、設定すると CI を待たずにマージされます。
  - 取得ワークフロー共通 (`fetch-data.yml` を `auto_merge=true` で手動実行した場合も同じ): 取得の成功、処理の成功 (raw の変更が 0 件のときだけ skipped も可)、処理カバレッジ (`check-data-status --fail-on-incomplete`)、メタデータの JSON Schema 適合、連続性検証 (`check-missing`、猶予 1 期間。日次・週次は常に、手動は `verify_continuity=true` のとき)
  - 日次: 取得前のデータ検証 (`validate-data`) の成功
  - 週次: 取得前と取得後の両方のデータ検証の成功
- ゲートが不合格のときは PR を作っても自動マージせず、レビューと手動マージを待ちます (手動実行で `force_merge_on_failure=true` を指定したときだけ、不合格を上書きして自動マージします。データ品質は保証されません)。raw の変更が無いのにゲートが不合格のときは、PR を作らずに run を失敗させます。

### 📊 手動実行

必要に応じて手動でもワークフローを実行できます:

1. GitHub リポジトリの **Actions** タブを開く
2. 実行したいワークフローを選択:
   - **📊 東京都感染症データ取得(手動実行)** (`fetch-data.yml`) - 期間・データタイプ・対象週/月・モードを指定して取得 (既定は当年・full モード、自動マージなし)
   - **📅 毎日データ簡易チェック** - 2週前・前週・当週、前月・当月
   - **📆 毎週データ徹底チェック** - 当年の全データ(週次・月次、1月は前年分も含む)
   - **⚙️ データ処理(raw→processed変換・手動実行)** (`process-data.yml`) - 指定した raw を processed に変換して PR を作る (入力と結果の読み方は [docs/manual-data-processing.md](docs/manual-data-processing.md))
   - **🔀 メタデータマイグレーション** (`migrate-metadata.yml`) - メタデータを目標バージョンへ移行して PR を作る (自動マージなし)
3. **"Run workflow"** をクリック
4. 必要に応じてパラメータを設定して実行

> ⚠️ データ取得系のワークフローはすべてデータ提供元 (`survey.tmiph.metro.tokyo.lg.jp`) に HTTP リクエストを送ります。`process-data.yml` の `target_files` を空欄にすると全 raw を処理する (`--all`) ため、通常は対象ファイルを指定してください。単一期間の修復・processed の差分 backfill・データ PR の revert の手順は [docs/runbook-data-repair.md](docs/runbook-data-repair.md) を参照してください。

<details>
<summary>📋 ワークフロー一覧 (データ収集 / 開発・CI/CD) (クリックして展開)</summary>

### ワークフロー一覧

トリガーの時刻は JST です (cron は UTC で記述)。正確な値は各ワークフローファイルを参照してください。

#### データ収集ワークフロー

| ワークフロー名                                 | ファイル                | 用途                                                    | トリガー                                                         |
| ---------------------------------------------- | ----------------------- | ------------------------------------------------------- | ---------------------------------------------------------------- |
| **📊 東京都感染症データ取得(手動実行)**        | `fetch-data.yml`        | 期間・対象を指定したデータ取得、単一期間の修復          | 手動実行のみ                                                     |
| **📅 毎日データ簡易チェック**                  | `fetch-data-daily.yml`  | 直近3週・2か月の未取得分を取得 (年始は前年の同じ番号も) | 毎日17:00 JST または 手動実行                                    |
| **📆 毎週データ徹底チェック**                  | `fetch-data-weekly.yml` | 当年 (1月は前年も) の全データを強制再取得               | 毎週木曜17:30 JST または 手動実行                                |
| **⚙️ データ処理(raw→processed変換・手動実行)** | `process-data.yml`      | 指定した raw の processed への変換                      | 手動実行のみ                                                     |
| **🔀 メタデータマイグレーション**              | `migrate-metadata.yml`  | メタデータのバージョン移行                              | `src/models/metadata.py` を変える main への push または 手動実行 |

共通処理 `_fetch-data-common.yml` は上の3つの取得ワークフローから呼ばれる reusable workflow で、単独では実行されません。

#### 開発・CI/CDワークフロー

| ワークフロー名                           | ファイル                           | 用途                                          | トリガー                                                                                        |
| ---------------------------------------- | ---------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| **🧪 テストスイート実行**                | `test.yml`                         | 自動テスト実行                                | push (main / develop / feature/\*) または main への PR または 毎週火曜09:17 JST または 手動実行 |
| **🔍 ワークフロー静的検証 (actionlint)** | `actionlint.yml`                   | ワークフローの静的検証                        | `.github/workflows/` を変える PR・main への push または 手動実行                                |
| **🔍 Claude コードレビュー**             | `claude-code-review.yml`           | AIによるコードレビュー (Dependabot PR は除く) | PR作成・更新時                                                                                  |
| **🤖 Claude Code 統合**                  | `claude.yml`                       | Claude AIとの統合                             | `@claude` を含む Issue/PR のコメント・レビュー・Issue 作成                                      |
| **🔎 Dependabot PR の事前検証**          | `dependabot-pr-vetting.yml`        | Dependabot PR の事前検証                      | Dependabot の PR の作成・更新時                                                                 |
| **🔍 依存更新パイプラインの生存確認**    | `dependency-pipeline-watchdog.yml` | 依存更新パイプラインの停止検知                | 毎週水曜09:23 JST または 手動実行                                                               |

</details>

## ⚙️ 必要な設定

### GitHub Actions権限設定(重要)

このシステムが自動的にPull Requestを作成してデータを更新するためには、リポジトリ管理者による以下の設定が**必須**です:

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

開発やテスト用にローカル環境をセットアップする場合:

#### 前提条件

- Python 3.11 (`pyproject.toml` の `requires-python` は `>=3.11,<3.12`)
- [uv](https://github.com/astral-sh/uv) パッケージマネージャー (版は `.tool-versions` で固定)

#### インストール

```bash
# uvのインストール(未インストールの場合)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存関係のインストール (uv.lock どおり)
uv sync --locked

# 開発用依存関係も含める場合
uv sync --all-extras --locked
```

#### ローカルでのデータ取得

> ⚠️ `uv run fetch-data` はデータ提供元 (`survey.tmiph.metro.tokyo.lg.jp`) に HTTP リクエストを送ります。`--dry-run` を付けても取得は行い、保存だけを省きます。接続しないのは `--plan-only` だけです。CLI は引数なしでも動きますが、そのときは下に書く広い範囲を要求するので、期間とデータタイプは必ず明示し、取得した raw はブランチを切って PR で反映してください (`fetch-data` はコミットしません)。

取得計画 (オフライン。データ提供元に接続せず、ファイルもディレクトリも作らない):

```bash
# 取得候補を JSON で標準出力に表示する
uv run fetch-data --data-types sentinel_weekly_gender --start-year 2026 --end-year 2026 --plan-only
```

計画は本実行と同じ規則 (mode・期間・対象週/月・JST の今日までの ISO 週と月・`data/raw/` の既存ファイル) で候補を選び、`candidates` に `data_type`・`year`・`week` または `month`・保存先のファイル名 (`file`)・ローカルに既にあるか (`exists_locally`) を、`counts` / `candidate_count` に件数を出します。データ提供元が公開済みか、内容が全て0か、接続や応答検証が成功するかは確かめられないため、その範囲を `not_verified` に列挙します (取得結果の統計とは別物で、統計ファイルも作りません)。`--plan-only` は `--dry-run` とも `--log-file` とも併用できません。

ドライラン (保存しない。データ提供元への HTTP リクエストは発生する):

```bash
# データ提供元に接続する (保存はしない)
uv run fetch-data --data-types sentinel_weekly_gender --start-year 2026 --end-year 2026 --dry-run
```

本実行 (データ提供元に接続し、指定した期間・データタイプのうち未取得の期間だけを `data/raw/` に保存する。既定の incremental モード):

```bash
# データ提供元に接続し、data/raw/ に保存する
uv run fetch-data --data-types sentinel_weekly_gender --start-year 2026 --end-year 2026
```

引数なしの `uv run fetch-data` は、`config/config.yml` の `collection.start_year` (現在 `2024`) から当年 (JST) までの全データタイプについて、`collection.mode` (現在 `incremental`) に従い未取得の期間をすべて要求します。全て0の未公開データは保存されないため、未公開の期間は実行のたびに要求し直されます。単一期間の修復と processed の更新は [docs/runbook-data-repair.md](docs/runbook-data-repair.md) の手順に従ってください。

```bash
# 欠番チェック (data/raw のファイルだけを読む。データ提供元には接続しない)
uv run check-missing data/raw
```

#### 旧 `scripts/*.py` 実行経路は削除済み(issue #312)

- 対象7コマンドは互換シムを削除済 (2026年Q2): `fetch-data`, `process-data`, `validate-data`, `verify-metadata`, `migrate-metadata`, `check-data-status`, `cleanup-all-zero-data`
- 実行方法: `uv run <command>` (例: `uv run fetch-data`, `uv run validate-data`)
- 詳細: `https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/issues/312`

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

`config/config.yml` は `uv run fetch-data` が読む取得設定です。CLI 引数 (`--mode` / `--start-year` / `--end-year` / `--data-types`) を渡すとそちらが優先されます。

<details>
<summary>⚙️ 設定項目の詳細 (収集 / ストレージ / Pull Request) (クリックして展開)</summary>

#### 収集設定 (`collection`)

- **mode**: 収集モード `incremental` (未取得の期間のみ) / `full` (全期間、既存も更新チェック) / `force` (全データを強制再取得)。現在値は `incremental`。旧キー `incremental_mode` は非推奨で、`mode` が未設定のときだけ後方互換として読まれます
- **start_year / end_year**: 収集期間。`end_year` が `null` なら JST の当年
- **data_types**: 収集対象のデータタイプリスト
- **batch_size**: 一度に処理するリクエスト数 (現在値: 50)
- **max_execution_time_hours**: 実行時間の上限 (GitHub Actions の6時間制限対策、現在値: 5.5)

#### ストレージ設定 (`storage`)

- **base_directory**: 生データ保存先 (現在値: `data/raw`)
- ファイルサイズの検証範囲 (100B - 50MB) は config ではなく `src/managers/storage_manager.py` の `VALIDATION_*` 定数が定義します

> 定期実行の時刻は config ではなく `.github/workflows/fetch-data-daily.yml` / `fetch-data-weekly.yml` の cron が決めます。

#### Pull Request設定

PR の作成・タイトル・ラベル・本文は config では設定できず、`scripts/create_pr.sh` が決めます (タイトル `データ更新: YYYY-MM-DD - N件 (新規X件/更新Y件)`、ラベル `data-update` / `automated` + 種別ラベル)。自動マージの条件は `scripts/auto_merge_gate.sh` が判定します。概要は [GitHub Actionsワークフロー](#-github-actionsワークフロー) を参照してください。

</details>

## 📝 ライセンスおよび利用規約

⚠️ **重要**: このプロジェクトは **ソフトウェア** と **データ** で異なる利用条件が適用されます。

### ソフトウェア部分

このプロジェクトの **ソースコードおよびスクリプト** については、作者(kambarakun)が著作権を保有し、非商用目的での利用を許可しています。

- 対象: `src/`, `scripts/`, `tests/`, `.github/`, 設定ファイル等
- 詳細: [LICENSE.md](LICENSE.md)
- **商用利用: 禁止**
- 非商用利用: 自由に使用・改変・再配布可能(著作権表示を保持すること)

### データ部分(data/ディレクトリ)

⚠️ **重要**: 本システムで収集されるデータの著作権は **東京都** および **東京都健康安全研究センター** に帰属します。

#### データ提供元

- **機関**: 東京都健康安全研究センター(Tokyo Metropolitan Institute of Public Health)
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

データ利用に関する詳細なお問い合わせは、直接東京都健康安全研究センターへご連絡ください:

- 住所: 〒169-0073 東京都新宿区百人町三丁目24番1号
- 電話: 03-3363-3231(代表)

## 🔗 関連情報

- **データソース**: [東京都感染症発生動向情報](https://survey.tmiph.metro.tokyo.lg.jp/epidinfo/epimenu.do)
- **データ修復・backfill・ロールバック手順**: [docs/runbook-data-repair.md](docs/runbook-data-repair.md)
- **data/ の構造設計**: [docs/data_structure_design.md](docs/data_structure_design.md)
- **当初設計の歴史的資料**: `.kiro/specs/tokyo-epidemic-data-automation/` (現行実装とは同期していない。現行の仕様は README.md / AGENTS.md / docs/ / schemas/ を参照)
- **GitHub リポジトリ**: [fetch-tokyo-idsc-github-actions](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions)

## 🤝 貢献

Issues や Pull Requests を歓迎します。大きな変更を行う場合は、まず Issue を開いて変更内容について議論してください。

## 📧 連絡先

問題や質問がある場合は、[GitHub Issues](https://github.com/kambarakun/fetch-tokyo-idsc-github-actions/issues) でお知らせください。
