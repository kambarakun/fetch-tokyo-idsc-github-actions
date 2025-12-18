# メタデータスキーマ v1.2.0 設計レビュー依頼

## 評価依頼の概要

東京都感染症発生動向データの自動化プロジェクトにおいて、メタデータスキーマをv1.1.0からv1.2.0にアップグレードする設計を作成しました。設計の妥当性について評価をお願いします。

## 背景

### プロジェクト概要

- **目的**: 東京都が公開する感染症データを自動取得・処理し、GitHub上で管理
- **データ形式**: CSV (元データは Shift_JIS、処理済みは UTF-8)
- **メタデータ**: 各CSVファイルに対応するJSONメタデータを生成
- **現行バージョン**: v1.1.0 (プロファイルベース構造)

### 発見された問題

データ処理中に、**東京都の公開している元データに系統的な不整合**を発見しました：

1. **問題の種類**: 性別分割データで `male + female != total`
2. **影響範囲**:
   - 対象: sentinel_weekly_medical_district など性別分割データ
   - 規模: 約1,340ファイル中400ファイル (30%) に不整合
   - 総エラー箇所: 689箇所
3. **エラーパターン**: ほぼ100%が `total = male` (女性の値が反映されていない)

### 具体例

`sentinel_weekly_medical_district_2024_06.csv` の場合：

| 医療圏 | 疾患           | 男性 | 女性 | 合計 (元データ) | 期待される合計 |
| ------ | -------------- | ---- | ---- | --------------- | -------------- |
| 島しょ | インフルエンザ | 3    | 3    | **3**           | 6              |

→ 合計が男性の値と同じになっており、女性が反映されていない

### 現在の対応

- **処理済みデータ**: male と female ファイルのみ生成 (不正確な total は生成しない)
- **問題**: 元データに不整合があることをデータ利用者が知る手段がない

## 解決すべき課題

1. **透明性**: 元データの不整合をデータ利用者に明示する
2. **トレーサビリティ**: どのファイルのどこに不整合があるか記録する
3. **判断の委譲**: システムがデータの良し悪しを評価せず、事実のみを記録
4. **後方互換性**: 既存のv1.1.0を読むシステムが影響を受けない
5. **拡張性**: 将来的に他の品質チェックを追加できる

## 提案する設計: v1.2.0

### コア構造

```json
{
  "metadata_version": "1.2.0",
  // ... v1.1.0 の全フィールド (変更なし) ...

  "quality": {
    "validation_timestamp": "ISO 8601 タイムスタンプ",
    "issues": [
      {
        "check_type": "gender_sum_consistency",
        "message": "不整合の概要説明",
        "details": {
          "source_file": "元データファイル名",
          "affected_locations": [
            {
              "location": "場所名",
              "column": "カラム名",
              "male": 男性の値,
              "female": 女性の値,
              "total": 元データの合計値,
              "expected": 期待される合計値
            }
          ]
        }
      }
    ]
  }
}
```

### フィールド仕様

#### `quality.validation_timestamp`

- **型**: string (ISO 8601)
- **必須**: Yes
- **説明**: 検証実行日時

#### `quality.issues`

- **型**: array
- **必須**: Yes
- **説明**: 検出された品質問題のリスト
- **空配列の場合**: 問題なし (正常)

#### `quality.issues[].check_type`

- **型**: string
- **必須**: Yes
- **値**: `gender_sum_consistency` (将来的に他の検証タイプを追加可能)

#### `quality.issues[].message`

- **型**: string
- **必須**: Yes
- **説明**: 不整合の概要 (人間が読める形式)

#### `quality.issues[].details`

- **型**: object
- **必須**: Yes
- **内容**:
  - `source_file`: 検証元ファイル名
  - `affected_locations`: 不整合箇所のリスト (最大10件)

### サンプルメタデータ

#### ケース1: 問題なしのデータ

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
    "issues": []
  }
}
```

#### ケース2: 問題ありのデータ

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
    "issues": [
      {
        "check_type": "gender_sum_consistency",
        "message": "Source data inconsistency: male + female != total in 1 location(s)",
        "details": {
          "source_file": "sentinel_weekly_medical_district_2024_06.csv",
          "affected_locations": [
            {
              "location": "島しょ",
              "column": "インフルエンザ",
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

## 設計の制約条件

### 1. 生データ至上主義

- **原則**: 元データが正しいという前提
- **対応**: システムが「良い/悪い」を評価しない、事実のみを記録
- **理由**: 公式データの品質をシステムが判断すべきではない

### 2. 後方互換性

- **要件**: v1.1.0を読むシステムが動作し続ける
- **対応**: `quality` フィールドは追加のみ、既存フィールドは変更なし
- **確認**: v1.1.0を読むシステムは `quality` を無視すればOK

### 3. 性別分割データの扱い

- **特性**: male, female, total の3ファイルが1セット
- **対応**:
  - male と female の両方のメタデータに同じ検証結果を記録
  - total ファイルには記録しない (または total ファイル自体を生成しない)
- **理由**:
  - 各ファイルのメタデータは独立して参照可能であるべき
  - どちらのファイルを見ても品質情報が得られる

### 4. 適用範囲

- **適用**: 性別分割データのみ

  - sentinel_weekly_age
  - sentinel_weekly_health_center
  - sentinel_weekly_medical_district
  - sentinel_monthly_age
  - sentinel_monthly_health_center
  - sentinel_monthly_medical_district

- **非適用**: 性別分割されていないデータ
  - sentinel_weekly_gender
  - sentinel_monthly_gender
  - notifiable_weekly

### 5. パフォーマンス

- **制約**: メタデータファイルサイズを抑える
- **対応**: `affected_locations` は最大10件まで

## 代替案との比較

### 代替案A: ステータス・スコア方式 (却下)

```json
{
  "quality": {
    "validation_timestamp": "...",
    "overall_status": "passed | warning | failed",
    "reliability_score": 0.923,
    "checks": [...]
  }
}
```

**却下理由**:

- システムがデータの良し悪しを評価することになる
- スコア計算基準が恣意的
- 生データ至上主義に反する

### 代替案B: 問題検出時のみメタデータ (却下)

```json
// 問題なし → quality フィールドなし
// 問題あり → quality フィールドあり
```

**却下理由**:

- フィールドの有無で状態を表現するのは不明確
- 「検証したが問題なし」と「未検証」を区別できない

### 採用案C: issues配列方式 (採用)

```json
{
  "quality": {
    "validation_timestamp": "...",
    "issues": [] // 空配列 = 検証済み・問題なし
  }
}
```

**採用理由**:

- 事実のみを記録
- 問題なし = `issues: []`、問題あり = 詳細を記録
- シンプルで明確

## 評価ポイント

以下の観点で評価をお願いします：

### 1. 設計の妥当性

- [ ] 「生データ至上」の原則に沿っているか
- [ ] 事実のみを記録し、評価をしていないか
- [ ] 必要十分な情報が含まれているか

### 2. 後方互換性

- [ ] v1.1.0を読むシステムが影響を受けないか
- [ ] 既存フィールドの意味が変わっていないか

### 3. 拡張性

- [ ] 将来的に他の品質チェックを追加できるか
- [ ] `check_type` による拡張方法は適切か

### 4. 利用者体験

- [ ] データ利用者が不整合を理解できるか
- [ ] 詳細情報 (location, male, female, total, expected) は十分か
- [ ] メッセージは分かりやすいか

### 5. パフォーマンス

- [ ] メタデータファイルサイズは適切か
- [ ] `affected_locations` の上限 (10件) は妥当か

### 6. データモデル

- [ ] JSON構造は適切か
- [ ] フィールド名は明確か
- [ ] 必須/オプショナルの区別は妥当か

### 7. 実装の容易性

- [ ] バリデーター実装が複雑すぎないか
- [ ] マイグレーションは実現可能か

### 8. 代替案との比較

- [ ] ステータス・スコア方式より優れているか
- [ ] 他により良い設計があるか

## 追加検討事項

### 1. フィールド名

- `issues` vs `problems` vs `inconsistencies` - どれが適切か？
- `affected_locations` vs `errors` vs `discrepancies` - どれが適切か？

### 2. affected_locations の上限

- 現在: 最大10件
- 全件記録すべきか？
- それとも件数のみ記録して詳細は省略すべきか？

### 3. validation_timestamp の扱い

- 常に記録すべきか？
- 検証できなかった場合は null にすべきか？

### 4. 性別分割データでの重複記録

- male と female の両方に記録する現在の方針は適切か？
- total ファイルにも記録すべきか？

### 5. エラーメッセージの言語

- 英語で統一すべきか？
- 日本語を含めるべきか？

## 参考情報

### プロジェクト構造

```text
data/
├── raw/                           # 生データ (Shift_JIS)
│   ├── .metadata/
│   │   └── *.json                 # 生データのメタデータ
│   └── *.csv
├── processed/                     # 処理済みデータ (UTF-8)
│   ├── .metadata/
│   │   └── *.json                 # ← v1.2.0 を適用
│   └── *.csv
└── logs/
```

### データフロー

```text
1. 東京都サイトからデータ取得 (Shift_JIS CSV)
2. raw/ に保存
3. 処理: Shift_JIS → UTF-8 変換、性別分割
4. 検証: male + female = total チェック ← NEW!
5. processed/ に保存 + メタデータ生成 (v1.2.0)
```

### 関連ドキュメント

- `metadata-schema-v1.2.0.md`: 詳細仕様書
- `CLAUDE.md`: プロジェクト全体のガイドライン

## 評価方法

以下のいずれかの方法で評価をお願いします：

1. **評価ポイントのチェック**: 各項目に対して ✅/⚠️/❌ で評価
2. **改善提案**: 具体的な改善案の提示
3. **代替設計**: より良い設計案の提案
4. **質問**: 不明点や懸念事項の指摘

## 期待する成果物

- 評価結果サマリー
- 指摘事項リスト
- 改善提案 (あれば)
- 承認/修正要求の判断

---

**レビュー期限**: なし (じっくり評価してください)

**レビュー担当**: [評価者名]

**連絡先**: このファイルにコメント、または別ファイルでレポート作成
