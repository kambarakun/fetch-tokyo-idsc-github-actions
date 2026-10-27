# data/ ディレクトリ構造設計

## 設計方針

1. **生データの保持**: Shift_JIS原本は必ず保持 (再処理可能性)
2. **2段構造**: `raw/` (Shift_JIS原本) と `processed/` (UTF-8正規化済み) の2段階
3. **中間ファイルなし**: UTF-8変換のみの中間ファイルは作らない (メモリ上で直接処理)
4. **メタデータ管理**: 各段階でファイルごとにメタデータを保持

## ディレクトリ構造

```text
data/
├── raw/                                                               # 生データ (Shift_JIS、GitHub管理対象)
│   ├── .metadata/                                                     # 生データの個別メタデータ
│   │   ├── hash_index.json                                            # 旧ハッシュインデックス (残存ファイル。現行コードは読み書きしない)
│   │   └── *.json                                                     # 各生データファイルの個別メタデータ
│   └── *.csv                                                          # 元データ (複雑な構造のまま)
├── processed/                                                         # 処理済み (UTF-8、フラット配置、GitHub管理対象)
│   ├── .metadata/                                                     # 処理済みファイルのメタデータ
│   │   ├── processing_log.json                                        # 旧処理ログ (残存ファイル。現行コードは書かない)
│   │   └── normalized_*.json                                          # 各出力ファイルの個別メタデータ
│   ├── stats.json                                                     # 直近の処理統計 (process-data が出力)
│   ├── normalized_notifiable_weekly_2000_01.csv                       # 全数報告 (UTF-8、メタデータ除去)
│   ├── normalized_sentinel_weekly_age_male_2000_01.csv                # 定点・年齢群・男性 (UTF-8)
│   ├── normalized_sentinel_weekly_age_female_2000_01.csv              # 定点・年齢群・女性 (UTF-8)
│   ├── normalized_sentinel_weekly_age_total_2000_01.csv               # 定点・年齢群・合計 (UTF-8、元データの値を検証済み)
│   ├── normalized_sentinel_weekly_medical_district_total_2007_01.csv  # 定点・医療圏・合計 (男女合計にデータ行がある期間のみ)
│   └── normalized_sentinel_weekly_gender_2000_01.csv                  # 定点・性別 (UTF-8、性別列形式のため分割なし)
└── logs/                                                              # 処理ログ
```

**設計方針:**

- **MECE**: `raw/` (Shift_JIS原本) と `processed/` (UTF-8正規化済み) で明確に分離
- **超シンプル**: トップレベルは `raw/` と `processed/` の2つ (および `logs/`) のみ
- **フラット**: 処理済みファイルは全て `processed/` 直下にフラット配置
- **ファイル名で分類**: ディレクトリ階層ではなくファイル名で種類を識別
- **中間ファイルなし**: UTF-8変換と正規化・分割を `process-data` が一度に実行し、中間の `utf8/` ディレクトリは作らない (メモリ上で直接処理)

## データフロー

UTF-8変換と正規化・分割は `uv run process-data` (`src/cli/process_data.py` → `src/processors/data_processor.py`) が1つの処理として実行します。

```mermaid
flowchart TD
    IDSC[東京都IDSC] -->|定期取得| FetchData[1. データ取得<br/>uv run fetch-data]
    FetchData -->|Shift_JIS| RawDir[data/raw/*.csv<br/>Shift_JIS エンコーディング]

    RawDir -->|uv run process-data| Process[2. 処理<br/>UTF-8変換 + 正規化・分割]
    Process -->|メモリ上でShift_JIS→UTF-8変換<br/>メタデータ除去・性別ごとに分割| ProcessedDir[data/processed/<br/>normalized_*.csv<br/>UTF-8 エンコーディング]

    ProcessedDir --> Analysis[3. 分析・可視化]
    Analysis -->|可視化スクリプト| Charts[scripts/generate_charts.py]
    Analysis -->|分析| Notebook[Jupyter Notebook 等]

    style RawDir fill:#f9f,stroke:#333,stroke-width:2px
    style ProcessedDir fill:#bfb,stroke:#333,stroke-width:2px
```

## ファイル命名規則

### raw/ (生データ)

```text
{data_type}_{year}_{period:02d}.csv

例:
- sentinel_weekly_gender_2025_01.csv
- sentinel_weekly_age_2025_01.csv
- notifiable_weekly_2025_01.csv
- sentinel_monthly_age_2025_01.csv
```

> 注: `data_type` は `category` と `frequency` と集計軸を連結したもの。実装上は `category` (`notifiable` / `sentinel`) と `frequency` (`weekly` / `monthly`)、集計軸 (`gender` / `age` / `health_center` / `medical_district`) を組み合わせます。

### processed/ (正規化版)

実際の出力ファイル名は `normalized_` 接頭辞を付与します (実装: `src/processors/data_processor.py`)。

```text
# 全数報告 (シンプル)
normalized_{category}_{frequency}_{year}_{period}.csv
例: normalized_notifiable_weekly_2000_01.csv

# 定点監視 (性別分割あり)
normalized_{category}_{frequency}_{aggregation}_{gender}_{year}_{period}.csv
例:
- normalized_sentinel_weekly_age_male_2000_01.csv
- normalized_sentinel_weekly_age_female_2000_01.csv
- normalized_sentinel_weekly_age_total_2000_01.csv

# 定点監視 (分割なし: 性別が列形式の場合)
normalized_{category}_{frequency}_{aggregation}_{year}_{period}.csv
例: normalized_sentinel_weekly_gender_2000_01.csv
```

**gender パラメータ:** `male` (男性)、`female` (女性)、`total` (男女合計)。
`medical_district` の `total` は、男女合計セクションにデータ行がある期間だけ出力します。2000年第14週〜2006年第52週の週報は男女合計セクションがヘッダーのみのため `total` を出力しません (実装: `src/processors/data_processor.py::section_has_data_rows`。`check-data-status` も同じ判定で出力の有無を期待します)。

## メタデータ構造

メタデータは「ログファイル1本」ではなく、**ファイルごとの個別JSON** として `.metadata/` 配下に保存されます。スキーマの完全な定義は CI が検証に使う [`schemas/metadata-v1.3.schema.json`](../schemas/metadata-v1.3.schema.json) を参照してください (版の定数: `src/models/metadata.py::METADATA_VERSION`、現在 `1.3.0`)。検証コマンドは `uv run python scripts/validate_metadata_schema.py` です。

### raw/.metadata/hash_index.json (残存ファイル)

以前の重複検出で使っていた SHA256 → パスの索引です。現行コードは読み書きせず (再保存の判定は、同じパスの既存 CSV との SHA256 比較で行います: `src/managers/storage_manager.py`)、`verify-metadata` / `migrate-metadata` のメタデータ走査と README 統計の集計 (`scripts/update_readme_stats.py`) は対象から除外します。ファイル自体はリポジトリに残っており、整理は issue #738 が扱います。内容が実データと一致している保証はないので、参照しないでください。

### raw/.metadata/{filename}.json (生データの個別メタデータ)

生データファイルごとに、ファイル名の `.csv` を `.json` に置き換えた個別メタデータが保存されます。
(例: `sentinel_weekly_age_2025_01.json`)

```json
{
  "metadata_version": "1.3.0",
  "name": "sentinel_weekly_age_2025_01",
  "filename": "sentinel_weekly_age_2025_01.csv",
  "path": "sentinel_weekly_age_2025_01.csv",
  "profile": "tokyo-idsc-raw",
  "data_type": "sentinel_weekly_age",
  "temporal": { "year": 2025, "period": 1, "period_type": "weekly" },
  "bytes": 12345,
  "lines": 30,
  "hash": { "algorithm": "sha256", "value": "<sha256>" },
  "encoding": "shift_jis",
  "created": "2025-12-02T10:00:00+00:00",
  "modified": "2025-12-02T10:00:00+00:00",
  "sources": [{ "title": "Tokyo IDSC", "path": "<source_url>" }],
  "verification": { "status": "verified", "checks": {}, "errors": [], "warnings": [], "details": {} },
  "quality": { "validation_timestamp": "...", "validation_status": "completed", "issues": [] },
  "_fetch": {
    "source_url": "<source_url>",
    "fetch_time_seconds": 1.23,
    "force_overwrite": false,
    "save_all_zero": false
  }
}
```

> 既存ファイルとの差 (2026-10-27 時点): raw メタデータ 8,245 件はすべて `metadata_version` が 1.3.0 ですが、`sources` が空で `_fetch.source_url` が `null` です。取得元 URL の記録は現行コードで直っており、既存分の補完は issue #738 が扱います。

### processed/.metadata/{output_stem}.json (処理済みファイルの個別メタデータ)

正規化後の出力ファイルごとに、`{出力ファイル名(拡張子なし)}.json` のメタデータが保存されます。
(例: `normalized_sentinel_weekly_age_male_2000_01.json`)

```json
{
  "metadata_version": "1.3.0",
  "name": "normalized_sentinel_weekly_age_male_2000_01",
  "filename": "normalized_sentinel_weekly_age_male_2000_01.csv",
  "path": "processed/normalized_sentinel_weekly_age_male_2000_01.csv",
  "profile": "tokyo-idsc-processed",
  "data_type": "sentinel_weekly_age",
  "temporal": { "year": 2000, "period": 1, "period_type": "weekly" },
  "bytes": 6789,
  "lines": 12,
  "hash": { "algorithm": "sha256", "value": "<sha256>" },
  "encoding": "utf-8",
  "created": "2025-12-02T10:05:00+00:00",
  "modified": "2025-12-02T10:05:00+00:00",
  "sources": [{ "title": "sentinel_weekly_age_2000_01.csv", "path": "raw/sentinel_weekly_age_2000_01.csv" }],
  "_process": {
    "source_name": "sentinel_weekly_age_2000_01",
    "source_hash": "<source_sha256>",
    "processing_time_seconds": 0.05,
    "gender": "male"
  },
  "quality": { "validation_timestamp": "...", "validation_status": "completed", "issues": [] }
}
```

> 既存ファイルとの差 (2026-10-27 時点): processed メタデータ 18,181 件のうち 13,665 件は `metadata_version` 1.1.0 のままで `quality` を持ちません (残り 4,516 件は 1.3.0 で `quality` あり)。再生成は issue #738 が扱います。

### processed/stats.json (処理統計)

`process-data` の本実行 (`--dry-run` なし) のたびに、直近の処理結果サマリーが上書き保存されます (実装: `src/cli/process_data.py`)。`--files` では `data/raw/` 配下にないためスキップしたファイル数 `skipped` も書きます。`--all` は `skipped` を書きません。

`--files` の場合:

```json
{
  "total": 3,
  "succeeded": 3,
  "failed": 0,
  "skipped": 0,
  "errors": []
}
```

`--all` の場合:

```json
{
  "total": 100,
  "succeeded": 100,
  "failed": 0,
  "errors": []
}
```

## .gitignore設定

`data/raw/` と `data/processed/` は **どちらもGitHub管理対象** です (再生成可能ですが、クローン直後に分析を始められるよう処理済みデータもコミットします)。`.gitignore` ではこれらを除外していません。

データ関連で `.gitignore` が除外しているのは、プロジェクト用の作業ディレクトリのみです (実装: リポジトリ直下の `.gitignore`)。

```gitignore
# Project specific
# Data artifacts
data/dummy/
data/output/
```

## 処理スクリプト

UTF-8変換と正規化は専用スクリプトに分かれておらず、`process-data` コマンドが両方を一度に実行します。
(エントリポイント定義: `pyproject.toml` の `[project.scripts]`、実装: `src/cli/process_data.py`)

通常は変更した raw だけを `--files` で処理します (定期ワークフローも、そのジョブで追加・変更された raw だけを `process-data --files` に渡します)。`--files` のパスは現在のディレクトリからの相対パスで、`<data-dir>/raw/` 配下にないファイルはスキップされます。

```bash
# 特定ファイルのみ処理 (1個以上、スペース区切り)
uv run process-data --files data/raw/sentinel_weekly_gender_2025_01.csv

# 詳細ログ付き
uv run process-data --verbose --files data/raw/sentinel_weekly_gender_2025_01.csv data/raw/sentinel_weekly_age_2025_01.csv
```

ドライランは引数と data ディレクトリの存在だけを確認し、対象ファイルの存在確認・変換・品質検証・`stats.json` の更新はしません:

```bash
uv run process-data --dry-run --files data/raw/sentinel_weekly_gender_2025_01.csv
```

`--all` は `data/raw/` の全 CSV を処理します。入力・出力・品質検証結果が変わらない processed メタデータは書き直しませんが、v1.1.0 のまま残っている既存メタデータ (2026-10-27 時点で 13,665 件) は 1.3.0 の形に書き換わり、`modified` なども更新されます。全件の再生成 (issue #738) のように意図して全件を扱う場合以外は使わないでください。

```bash
# 全件の再処理。通常の運用では使わない (既存の v1.1.0 メタデータを全件書き換える)
uv run process-data --all
```

## データ容量の見積もり

| ディレクトリ    | GitHub管理 | 説明                                 |
| --------------- | ---------- | ------------------------------------ |
| data/raw/       | ✅ Yes     | Shift_JIS原本                        |
| data/processed/ | ✅ Yes     | UTF-8正規化版 (rawから再生成可)      |
| data/logs/      | 一部       | 処理ログ (`*.log` 等はgitignore対象) |

## 運用フロー

### 定期データ取得時 (GitHub Actions)

日次・週次・手動の取得ワークフローは、共通処理 `.github/workflows/_fetch-data-common.yml` を呼び出します。図は主なステップの順序です (グラフ・README 統計の更新と通知・ログ保存は省略)。`dry_run` のときは取得だけを行い、処理以降のステップは実行しません。main へ直接 push することはなく、変更は必ず PR を経由します。

```mermaid
flowchart TD
    Daily["fetch-data-daily.yml<br/>毎日 17:00 JST<br/>incremental"] --> Params
    Weekly["fetch-data-weekly.yml<br/>毎週木曜 17:30 JST<br/>force"] --> Params
    Manual["fetch-data.yml<br/>手動実行"] --> Params
    Params["Prepare fetch parameters<br/>入力検証・対象年の決定"] --> ValBefore["Validate existing data before fetch<br/>uv run validate-data (日次・週次)"]
    ValBefore --> Fetch["Fetch epidemic data<br/>uv run fetch-data"]
    Fetch --> SaveRaw["data/raw/ に保存<br/>Shift_JIS"]
    SaveRaw --> Process["Process epidemic data<br/>uv run process-data --files 変更された raw"]
    Process --> Coverage["Check processing coverage<br/>uv run check-data-status --fail-on-incomplete"]
    Coverage --> ValAfter["Validate fetched data<br/>uv run validate-data (週次のみ)"]
    ValAfter --> Continuity["Verify data continuity<br/>uv run check-missing (日次・週次、手動は指定時)"]
    Continuity --> Changes["Check for changes<br/>data/raw の差分を判定"]
    Changes --> Schema["Validate metadata against JSON Schema"]
    Schema --> Gate["Evaluate auto-merge gate<br/>scripts/auto_merge_gate.sh"]
    Gate --> HasChanges{"data/raw に変更あり?"}
    HasChanges -->|Yes| CreatePR["Create Pull Request<br/>scripts/create_pr.sh<br/>ブランチ push・PR 作成"]
    HasChanges -->|No| Blocked{"ゲート不合格?"}
    Blocked -->|Yes| FailRun["run を失敗させる"]
    Blocked -->|No| NoPR["PR を作らずに終了"]
    CreatePR --> Merge{"自動マージ要求あり<br/>かつ (ゲート通過<br/>または強制上書き)?"}
    Merge -->|Yes| AutoMerge["gh pr merge --auto --squash"]
    Merge -->|No| Review["レビュー・手動マージ"]

    style SaveRaw fill:#f9f,stroke:#333,stroke-width:2px
    style AutoMerge fill:#bfb,stroke:#333,stroke-width:2px
```

自動マージは日次・週次で要求され、手動の `fetch-data.yml` では `auto_merge` 入力 (既定 `false`) で要求します。手動実行で `force_merge_on_failure=true` も指定すると、ゲートが不合格でも上書きして自動マージします (データ品質は保証されません)。ゲートの条件は README の「GitHub Actionsワークフロー」と `scripts/auto_merge_gate.sh` を参照してください。

### ローカル開発時

```mermaid
flowchart TD
    Clone[リポジトリクローン] --> CheckData{data/processed/<br/>が存在?}
    CheckData -->|No| ProcessLocal[データ処理<br/>uv run process-data --all]
    CheckData -->|Yes| Analysis[分析・開発開始]
    ProcessLocal --> Analysis

    Analysis --> UpdateRaw{data/raw/<br/>を更新?}
    UpdateRaw -->|Yes| Reprocess[再処理<br/>uv run process-data --files 変更した raw]
    UpdateRaw -->|No| Continue[開発継続]
    Reprocess --> Continue
```

`data/processed/` はリポジトリにコミット済みなので、通常のクローンでは `ProcessLocal` の経路は通りません。

### データ更新時

```mermaid
flowchart TD
    Update[data/raw/が更新] --> Reprocess[データ処理<br/>uv run process-data --files 変更した raw]
    Reprocess --> Convert[UTF-8変換<br/>メモリ上]
    Convert --> Norm[正規化・性別分割<br/>_process_notifiable / _process_sentinel]
    Norm --> Save[data/processed/に保存<br/>normalized_*.csv]

    style Update fill:#f9f,stroke:#333,stroke-width:2px
    style Save fill:#bfb,stroke:#333,stroke-width:2px
```

修復・backfill の手順は [runbook-data-repair.md](runbook-data-repair.md) を参照してください。

## 利点

1. **トレーサビリティ**: 各処理済みファイルのメタデータに元ファイル名・ハッシュ (`_process.source_name` / `source_hash`) を記録
2. **再現性**: rawさえあれば `process-data` で全データを再生成可能
3. **柔軟性**: 正規化ロジック変更時も簡単に再処理
4. **効率性**: 用途に応じて raw / processed を選択
5. **Git管理**: 原本と処理済みデータの両方をバージョン管理
