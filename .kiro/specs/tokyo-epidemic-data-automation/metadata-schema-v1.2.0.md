# メタデータスキーマ v1.2.0 仕様

## バージョン履歴

- **v1.0.0**: 初期バージョン
- **v1.1.0**: プロファイルベース構造の導入
- **v1.2.0**: データ品質検証情報の追加 (2025-12-18)

## 概要

v1.2.0 では、元データの品質問題を記録し、データ利用者に警告を提供する機能を追加します。特に、東京都の公開データに存在する `male + female != total` の不整合を検出・記録します。

## 新規追加フィールド: `quality`

### 構造

```json
{
  "metadata_version": "1.2.0",
  "name": "...",
  "filename": "...",
  // ... 既存のフィールド (v1.1.0 から継承) ...

  "quality": {
    "validation_timestamp": "ISO 8601 形式のタイムスタンプ",
    "validation_status": "completed | skipped | failed",
    "issues": [
      {
        "check_type": "検証タイプ",
        "message": "不整合の説明",
        "details": {
          // 不整合の詳細情報
        }
      }
    ]
  }
}
```

### フィールド詳細

#### `quality.validation_timestamp`

- **型**: string (ISO 8601)
- **必須**: Yes
- **説明**: 品質検証を実行した日時

#### `quality.validation_status`

- **型**: string
- **必須**: Yes
- **値**:
  - `completed`: 検証が正常に完了
  - `skipped`: 検証がスキップされた (該当しない検証など)
  - `failed`: 検証処理自体が失敗 (例外、パースエラーなど)
- **説明**: 検証プロセスの実行ステータス (データの良し悪しではなく、検証実行の事実)

#### `quality.issues`

- **型**: array
- **必須**: Yes
- **説明**: 検出された品質問題のリスト
- **空配列の意味**: 検証が完了し、問題が検出されなかった

#### `quality.issues[].check_type`

- **型**: string
- **必須**: Yes
- **値**:
  - `gender_sum_consistency`: 性別合計の整合性 (male + female != total)

#### `quality.issues[].message`

- **型**: string
- **必須**: Yes
- **説明**: 不整合の概要説明

#### `quality.issues[].details`

- **型**: object
- **必須**: Yes
- **説明**: 不整合の詳細情報

## 検証タイプ別の `details` 構造

### `gender_sum_consistency`

性別分割されたデータで `male + female = total` が成立するかを検証します。

```json
{
  "check_type": "gender_sum_consistency",
  "message": "Observed mismatch between (male + female) and reported total in 1 record(s)",
  "details": {
    "source_file": "sentinel_weekly_medical_district_2024_06.csv",
    "affected_count": 1,
    "truncated": false,
    "affected_locations": [
      {
        "location": "島しょ",
        "column": "インフルエンザ",
        "row_index": 7,
        "male": 3,
        "female": 3,
        "total": 3,
        "expected": 6
      }
    ]
  }
}
```

#### フィールド説明

- `source_file`: 検証元の生データファイル名
- `affected_count`: 不整合が検出された総数
- `truncated`: `affected_locations` が打ち切られたかどうか (true = 10件超で打ち切り)
- `affected_locations`: 不整合が検出された場所のリスト (最大10件)
  - `location`: 場所名 (医療圏、保健所、年齢区分など)
  - `column`: カラム名 (疾患名など)
  - `row_index`: 生データCSVの行番号 (オプション、1始まり)
  - `male`: 男性の値
  - `female`: 女性の値
  - `total`: 生データの合計値
  - `expected`: 期待される合計値 (male + female)

## 性別分割データにおける検証結果の記録

### 原則

性別分割データ (male/female/total の3ファイル) では、検証は3ファイルをまとめて実行しますが、検証結果は**male と female の両方のメタデータに記録**します。

### 理由

1. **独立性**: 各ファイルのメタデータは独立して読み取り可能であるべき
2. **利便性**: male ファイルだけ、または female ファイルだけを扱うユーザーも品質情報を確認できる
3. **一貫性**: どちらのファイルを参照しても同じ品質情報が得られる

### 記録方法

- **male ファイル**: 完全な検証結果を記録
- **female ファイル**: male と同一の検証結果を記録
- **total ファイル**:
  - `medical_district`: total ファイルは生成されない (元データに不整合があるため)
  - `age`, `health_center`: total ファイルは生成されるが、検証結果は記録しない (male/female を参照)

### 例

`sentinel_weekly_medical_district_2024_06` の場合：

- `normalized_sentinel_weekly_medical_district_male_2024_06.csv` → メタデータに検証結果あり ✅
- `normalized_sentinel_weekly_medical_district_female_2024_06.csv` → メタデータに検証結果あり ✅ (male と同一)
- `normalized_sentinel_weekly_medical_district_total_2024_06.csv` → ファイル自体が存在しない

## 適用対象

### v1.2.0 を適用するデータタイプ

- ✅ `sentinel_weekly_medical_district` (male/female)
- ✅ `sentinel_weekly_health_center` (male/female/total)
- ✅ `sentinel_weekly_age` (male/female/total)
- ✅ `sentinel_monthly_medical_district` (male/female)
- ✅ `sentinel_monthly_health_center` (male/female/total)
- ✅ `sentinel_monthly_age` (male/female/total)
- ❌ `sentinel_weekly_gender` (性別分割なし)
- ❌ `sentinel_monthly_gender` (性別分割なし)
- ❌ `notifiable_weekly` (性別分割なし)

### 検証の実行タイミング

1. **データ処理時**: 新規データ処理時に自動実行
2. **バッチ検証**: 既存データに対して一括実行
3. **マイグレーション**: v1.1.0 → v1.2.0 移行時

## マイグレーション戦略

### ステップ1: バージョン番号のみ更新

既存のv1.1.0メタデータに対して、まず `metadata_version` のみを `1.2.0` に更新：

```json
{
  "metadata_version": "1.2.0"
  // ... 既存フィールド (v1.1.0 と同じ) ...
  // quality フィールドはまだ追加しない
}
```

### ステップ2: 検証実行とquality追加

性別分割データに対して検証を実行し、`quality` フィールドを追加：

```json
{
  "metadata_version": "1.2.0",
  // ... 既存フィールド ...
  "quality": {
    "validation_timestamp": "2025-12-18T...",
    "checks": [...],
    "overall_status": "passed"
  }
}
```

### ステップ3: 新規データ

新規データ処理時は、処理と同時に検証を実行し、最初から `quality` フィールドを含める。

### quality フィールドの有無

- **性別分割データ** (age, health_center, medical_district): `quality` フィールドあり
- **性別分割なしデータ** (gender, notifiable): `quality` フィールドなし (v1.1.0 と同じ構造)

## 後方互換性

### バージョニングポリシー

**セマンティックバージョニング**: `MAJOR.MINOR.PATCH`

- **MAJOR (1.x.y)**: メジャーバージョン内は後方互換を保証

  - `1.0.0`, `1.1.0`, `1.2.0` は全て互換
  - 既存フィールドの削除・変更・意味変更は行わない
  - 新規フィールドの追加のみ

- **MAJOR変更 (2.0.0以降)**: 破壊的変更を含む
  - 既存フィールドの削除・変更が発生し得る
  - マイグレーションが必要

### 実装ガイドライン

**メタデータリーダー実装者へ**:

- `metadata_version` のメジャー番号をチェック
  - メジャーが `1` なら読み込み可能 (例: `1.0.0`, `1.1.0`, `1.2.0`, `1.9.9`)
  - メジャーが `2` 以上なら互換性なし
- 未知のフィールドは無視する (forward compatibility)

### v1.1.0 と v1.2.0 の互換性

- **v1.1.0を読むシステム**: `quality` フィールドを無視して動作可能 ✅
- **v1.2.0を読むシステム**: v1.1.0も読み込み可能 (`quality` フィールドがない = 未検証) ✅

## 実装ファイル

- `src/validators/quality_validator.py`: 品質検証ロジック
- `src/validators/gender_sum_validator.py`: 性別合計整合性検証
- `scripts/migrate_metadata_v1.2.py`: マイグレーションスクリプト
- `scripts/validate_data_quality.py`: 既存データの検証スクリプト

## サンプル

### 正常なデータのメタデータ

```json
{
  "metadata_version": "1.2.0",
  "name": "normalized_sentinel_weekly_age_male_2024_01",
  "filename": "normalized_sentinel_weekly_age_male_2024_01.csv",
  "path": "processed/normalized_sentinel_weekly_age_male_2024_01.csv",
  "profile": "tokyo-idsc-processed",
  "data_type": "sentinel_weekly_age",
  "temporal": {
    "year": 2024,
    "period": 1,
    "period_type": "weekly"
  },
  "bytes": 2796,
  "lines": 22,
  "hash": {
    "algorithm": "sha256",
    "value": "abc123..."
  },
  "encoding": "utf-8",
  "created": "2025-12-18T07:33:02.713186+00:00",
  "modified": "2025-12-18T07:33:02.713186+00:00",
  "sources": [
    {
      "title": "sentinel_weekly_age_2024_01.csv",
      "path": "raw/sentinel_weekly_age_2024_01.csv"
    }
  ],
  "_process": {
    "source_name": "sentinel_weekly_age_2024_01",
    "source_hash": "def456...",
    "processing_time_seconds": 0.001,
    "gender": "male"
  },
  "quality": {
    "validation_timestamp": "2025-12-18T07:33:02.720000+00:00",
    "validation_status": "completed",
    "issues": []
  }
}
```

### 問題のあるデータのメタデータ

```json
{
  "metadata_version": "1.2.0",
  "name": "normalized_sentinel_weekly_medical_district_male_2024_06",
  "filename": "normalized_sentinel_weekly_medical_district_male_2024_06.csv",
  "path": "processed/normalized_sentinel_weekly_medical_district_male_2024_06.csv",
  "profile": "tokyo-idsc-processed",
  "data_type": "sentinel_weekly_medical_district",
  "temporal": {
    "year": 2024,
    "period": 6,
    "period_type": "weekly"
  },
  "bytes": 2564,
  "lines": 15,
  "hash": {
    "algorithm": "sha256",
    "value": "ca6f26..."
  },
  "encoding": "utf-8",
  "created": "2025-12-18T07:33:04.119213+00:00",
  "modified": "2025-12-18T07:33:04.119213+00:00",
  "sources": [
    {
      "title": "sentinel_weekly_medical_district_2024_06.csv",
      "path": "raw/sentinel_weekly_medical_district_2024_06.csv"
    }
  ],
  "_process": {
    "source_name": "sentinel_weekly_medical_district_2024_06",
    "source_hash": "d1b3c3...",
    "processing_time_seconds": 0.000268,
    "gender": "male"
  },
  "quality": {
    "validation_timestamp": "2025-12-18T07:33:04.120000+00:00",
    "validation_status": "completed",
    "issues": [
      {
        "check_type": "gender_sum_consistency",
        "message": "Observed mismatch between (male + female) and reported total in 1 record(s)",
        "details": {
          "source_file": "sentinel_weekly_medical_district_2024_06.csv",
          "affected_count": 1,
          "truncated": false,
          "affected_locations": [
            {
              "location": "島しょ",
              "column": "インフルエンザ",
              "row_index": 14,
              "male": 3,
              "female": 3,
              "total": 3,
              "expected": 6
            }
          ]
        }
      }
    ]
  }
}
```

## 利用例

### データ利用者向けの警告表示

```python
import json

with open('metadata.json', 'r') as f:
    metadata = json.load(f)

# 検証ステータスと品質問題をチェック
if 'quality' in metadata:
    status = metadata['quality']['validation_status']

    if status == 'completed' and metadata['quality']['issues']:
        print(f"⚠️  Data quality issues found: {metadata['filename']}")

        for issue in metadata['quality']['issues']:
            print(f"\n  {issue['message']}")

            # 詳細情報の表示
            details = issue['details']
            total = details.get('affected_count', len(details.get('affected_locations', [])))
            truncated = details.get('truncated', False)

            print(f"  Total affected: {total} record(s)" + (" (showing first 10)" if truncated else ""))

            # サンプルを表示
            for loc in details.get('affected_locations', [])[:3]:
                row_info = f" [row {loc['row_index']}]" if 'row_index' in loc else ""
                print(f"    - {loc['location']}{row_info}: {loc['male']} + {loc['female']} = {loc['expected']} (actual: {loc['total']})")

            if len(details.get('affected_locations', [])) > 3:
                print(f"    ... and {len(details['affected_locations']) - 3} more in this file")

    elif status == 'failed':
        print(f"❌  Validation failed: {metadata['filename']}")
```

### データフィルタリング

```python
# 検証が完了して品質問題がないデータのみ使用
clean_files = [
    m for m in metadata_list
    if m.get('quality', {}).get('validation_status') == 'completed'
    and not m.get('quality', {}).get('issues', [])
]

# 品質情報を表示
for m in metadata_list:
    if 'quality' in m:
        status = m['quality']['validation_status']
        issues = m['quality']['issues']

        if status == 'completed':
            if issues:
                # 不整合の件数を表示
                total_affected = sum(
                    issue['details'].get('affected_count', 0)
                    for issue in issues
                )
                print(f"⚠️  Issues: {m['filename']} ({total_affected} records)")
            else:
                print(f"✓  Clean: {m['filename']}")
        elif status == 'failed':
            print(f"❌  Validation failed: {m['filename']}")
        elif status == 'skipped':
            print(f"–  Skipped: {m['filename']}")
```

## まとめ

v1.2.0 では、データ品質情報を記録することで：

1. ✅ **事実の記録**: 不整合があった場所と値を記録
2. ✅ **判断は利用者**: ステータスやスコアで評価せず、生データの不整合を提示
3. ✅ **完全な透明性**: 総件数 (`affected_count`)、打ち切り (`truncated`)、行番号 (`row_index`) で再現性を確保
4. ✅ **検証プロセスの明確化**: `validation_status` で検証実行の事実を記録
5. ✅ **拡張性**: check_type で新しい検証を追加可能
6. ✅ **堅牢な互換性**: セマンティックバージョニングで後方互換を保証

これにより、東京都の公開データに存在する不整合 (`male + female != total`) を明示的に記録し、データ利用者が自分で判断できるようにします。

## 設計の原則

- **生データ至上**: 不整合があっても事実を記録するのみ、評価はしない
- **シンプル**: 問題がなければ `issues: []`、あれば詳細を記録
- **透明性**: 総件数・打ち切り・行番号で完全なトレーサビリティ
- **明確**: `quality` フィールドの有無でデータ検証の適用可否が分かる
- **実用的**: 判断に必要な情報 (location, column, row_index, male, female, total, expected) を提供
- **非侵入的**: 品質評価をシステムが行わず、利用者に委ねる
- **堅牢**: セマンティックバージョニングで将来の拡張に対応

## レビュー対応履歴

v1.2.0の設計は以下の指摘を反映しています：

1. **打ち切り時の総件数** → `affected_count` と `truncated` を追加 ✅
2. **後方互換性の契約** → セマンティックバージョニングポリシーを明文化 ✅
3. **検証不能時の表現** → `validation_status` を追加 ✅
4. **CSVの追跡性向上** → `row_index` を追加 (オプション) ✅
5. **メッセージの中立化** → "Observed mismatch" に統一 ✅
