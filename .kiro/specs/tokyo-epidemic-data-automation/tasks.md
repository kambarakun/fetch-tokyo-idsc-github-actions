# Implementation Plan

## 実装ガイドライン

**重要**: このプロジェクトでは既存のコードとスクリプトを最大限活用します:

- 既存のクラス・スクリプト(EnhancedEpidemicDataFetcher, StorageManager, ConfigurationManager, DataProcessor, src/cli/validate_data.py 等)を優先的に再利用すること
- 新規クラスや大規模リファクタリングは人間の指示がある場合のみ行うこと
- ディレクトリ構造(data/raw のフラット構造)やファイル命名規則を変更する作業は、人間のレビューを必須とすること
- すべてのタスクは必須です(包括的な実装を目指します)

- [x] 1. プロジェクト構造とコア設定の設定
  - リポジトリのディレクトリ構造を作成(src/, config/, data/, tests/, .github/workflows/)
  - pyproject.toml で依存関係を定義 (requests, PyYAML, pytest, black, ruff, isort, mypy 等。パッケージ管理は uv、requirements.txt は不使用)
  - 設定ファイル(config/config.yml)のテンプレートを作成 (データタイプ定義は config.yml の data_types: セクションに統合)
  - .gitignoreファイルを作成(データファイル、キャッシュ、一時ファイルを除外)
  - pyproject.toml でプロジェクト設定・[project.scripts] を定義
  - _Requirements: 4.1, 4.6_

- [x] 2. 既存フェッチャーの統合と拡張
- [x] 2.1 既存TokyoEpidemicSurveillanceFetcherクラスの配置
  - 既存のフェッチャークラスをsrc/fetchers/base_fetcher.pyに配置
  - 必要に応じてimport文とクラス構造を調整
  - _Requirements: 1.2_

- [x] 2.2 EnhancedEpidemicDataFetcherクラスの実装
  - 既存クラスを継承した拡張フェッチャーを作成
  - リトライ機能とレート制限機能を実装
  - 日付範囲での一括取得機能を実装
  - _Requirements: 1.6, 5.1, 5.2_

- [x] 2.3 週次データ取得タイミングの修正
  - 現在週の2週前のデータを取得するロジックを実装(木曜更新を考慮)
  - 週番号計算の修正(48週→47週のバグ修正)
  - _Requirements: 1.3_

- [x] 2.4 フェッチャーのユニットテスト作成
  - リトライ機能のテストケースを作成
  - レート制限機能のテストケースを作成
  - モックを使用した外部API呼び出しテストを作成
  - _Requirements: 1.6, 5.1_

- [x] 3. 設定管理システムの実装
- [x] 3.1 ConfigurationManagerクラスの実装
  - YAML設定ファイルの読み込み機能を実装
  - 設定の妥当性検証機能を実装
  - 設定データクラス(DataCollectionConfig等)を定義
  - _Requirements: 4.1, 4.6_

- [x] 3.2 設定ファイルテンプレートの作成
  - config/config.ymlのデフォルト設定を作成
  - config.ymlの data_types: セクションでデータタイプ定義を作成 (data_types.yml は作成せず config.yml に統合)
  - 設定ファイルのドキュメントを作成
  - _Requirements: 4.1_

- [x] 3.3 設定管理のユニットテスト作成
  - 設定ファイル読み込みのテストケースを作成
  - 設定検証機能のテストケースを作成
  - 不正な設定に対するエラーハンドリングテストを作成
  - _Requirements: 4.6_

- [ ] 4. 実行管理システムの実装(将来の大規模拡張 - 人間レビュー必須)
- [ ] 4.1 ExecutionManagerクラスの実装
  - **注: これは大規模な新規アーキテクチャ追加です。人間の設計レビューが必須**
  - 実行時間制限監視機能を実装
  - 実行継続可否判定機能を実装
  - GitHub Actions制約を考慮した実行制御を実装
  - _Requirements: 4.4_
  - _Priority: Low - 現状の実装で十分動作している_

- [ ] 4.2 CheckpointManagerクラスの実装
  - 実行状態の保存・復元機能を実装
  - チェックポイントファイルの管理機能を実装
  - 実行再開時の状態復元機能を実装
  - _Requirements: 4.4_

- [ ] 4.3 実行管理のユニットテスト作成
  - 実行時間制限のテストケースを作成
  - チェックポイント機能のテストケースを作成
  - 実行再開機能のテストケースを作成
  - _Requirements: 4.4_

- [x] 5. ストレージ管理システムの実装
- [x] 5.1 StorageManagerクラスの実装
  - data/raw配下のフラットなディレクトリ構造でのファイル整理機能を実装
  - メタデータ付きファイル保存機能を実装
  - 重複ファイル検出機能を実装
  - _Requirements: 3.1, 3.4, 3.8_

- [x] 5.2 Git操作機能の実装
  - 自動コミット機能を実装
  - コミットメッセージテンプレート機能を実装
  - Git操作のエラーハンドリングを実装
  - _Requirements: 3.7_

- [x] 5.3 UTF-8変換・整形機能の実装
  - Shift_JIS CSVを読み込んでUTF-8に変換する機能を実装
  - 整形済みCSVを別ファイルとして保存する機能を実装
  - エンコーディングエラーのハンドリングを実装
  - _Requirements: 3.2, 3.3_
  - _注: src/processors/data_processor.py (DataProcessor) に実装済み。`uv run process-data` から実行_

- [x] 5.4 データ構造解析機能の実装
  - 全数報告CSV解析機能を実装(メタデータ抽出、疾病名・報告数の特定)
  - 定点監視CSV解析機能を実装(性別セクション分割、年齢別データ処理)
  - 感染症列の動的抽出機能を実装(ヘッダー行からの自動検出)
  - 引用符で囲まれたCSVフィールドの正しい解析を実装
  - 地域・年齢区分列と集計行の識別機能を実装
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_
  - _注: src/processors/data_processor.py の \_process_notifiable / \_process_sentinel / \_detect_gender_sections 等に実装済み (標準ライブラリ csv ベース、pandas 非依存)_

- [ ] 5.5 データ移行とバックアップ機能の実装
  - 既存データの移行スクリプトを作成
  - データバックアップ機能を実装
  - データ復元機能を実装
  - データ整合性チェック機能を実装
  - _Requirements: 3.1, 6.5_
  - _注: メタデータ移行 (migrate-metadata / scripts/migrate_metadata_v1_2_0.py) は実装済みだが、データ全体のバックアップ・復元機能は未実装_

- [x] 5.6 ストレージ管理のユニットテスト作成
  - ファイル整理機能のテストケースを作成
  - 重複検出機能のテストケースを作成
  - Git操作のテストケースを作成
  - データ移行機能のテストケースを作成
  - _Requirements: 3.1, 3.5, 3.6_

- [x] 6. メインアプリケーションの実装
- [x] 6.1 メインエントリーポイントの作成
  - src/cli/ 配下に CLI エントリーポイントを作成 ([project.scripts] で fetch-data 等を登録)
  - コマンドライン引数処理を実装
  - 各コンポーネントの初期化と連携を実装
  - _Requirements: 1.1, 4.5_

- [x] 6.2 データ収集ワークフローの実装
  - 完全なデータ収集フローを実装
  - エラー回復フローを実装
  - 進捗レポート機能を実装
  - _Requirements: 1.1, 1.3, 1.4_

- [x] 6.3 メインアプリケーションの統合テスト作成
  - エンドツーエンドワークフローのテストケースを作成
  - エラー回復ワークフローのテストケースを作成
  - 設定変更時の動作テストケースを作成
  - _Requirements: 1.1, 2.1_

- [x] 7. GitHub Actionsワークフローの実装
- [x] 7.1 メインデータ収集ワークフローの作成
  - .github/workflows/fetch-data.ymlを作成
  - cronスケジュール設定を実装
  - 手動トリガー(workflow_dispatch)を実装
  - _Requirements: 1.1, 4.2, 4.5_

- [x] 7.2 CI/CDワークフローの作成
  - .github/workflows/test.yml と actionlint.yml を作成 (uv ベース、ci.yml は不使用)
  - テスト実行、リンティング (ruff/black/isort)、型チェック (mypy) を設定
  - カバレッジレポート生成 (Codecov 連携、--cov-fail-under=100) を設定
  - ワークフロー静的検証 (actionlint + shellcheck) を設定
  - _Requirements: 8.5_

- [x] 7.3 テスト環境とモック機能の実装
  - 外部API呼び出し用のモックサーバーを作成
  - テスト用のサンプルデータを作成
  - テスト環境用の設定ファイルを作成
  - DRY_RUNモードでの動作確認機能を実装
  - _Requirements: 4.4_

- [x] 7.4 補助ワークフローの作成
  - データ処理ワークフロー(process-data.yml)を作成
  - メタデータ移行ワークフロー(migrate-metadata.yml)を作成
  - 毎日/週次データ収集ワークフロー(fetch-data-daily.yml / fetch-data-weekly.yml)を作成
  - ワークフロー間の依存関係を設定
  - _Requirements: 6.1, 7.2_

- [x] 8. ドキュメントとREADMEの作成
- [x] 8.1 README.mdの更新
  - プロジェクト概要と使用方法を記述
  - セットアップ手順を記述
  - 設定ファイルの説明を記述
  - _Requirements: 4.1_

- [ ] 8.2 API ドキュメントの作成
  - 各クラスとメソッドのdocstringを充実
  - 設定ファイルのスキーマドキュメントを作成
  - トラブルシューティングガイドを作成
  - _Requirements: 4.4_

- [ ] 8.3 運用ガイドの作成
  - GitHub Actionsの設定手順を記述
  - 監視とアラートの設定手順を記述
  - データ品質管理の運用手順を記述
  - 障害対応手順書を作成
  - データ復旧手順書を作成
  - 定期メンテナンス手順書を作成
  - _Requirements: 2.2, 6.1, 6.5_

- [ ] 8.4 運用監視ダッシュボードの作成
  - GitHub Actionsの実行状況を可視化するREADMEバッジを作成
  - データ収集状況のサマリーレポート生成機能を実装
  - システム健全性チェックリストを作成
  - 定期レポート自動生成機能を実装
  - _Requirements: 7.2_

- [x] 9. 統合テストとデプロイメント準備
- [x] 9.1 エンドツーエンド統合テストの作成
  - 完全なデータ収集ワークフローの統合テストを作成
  - GitHub Actions環境での動作テストを作成
  - エラーシナリオの統合テストを作成
  - _Requirements: 1.1, 2.1_

- [ ] 9.2 パフォーマンステストの作成
  - 大量データ処理のパフォーマンステストを作成
  - メモリ使用量監視テストを作成
  - 並列処理のパフォーマンステストを作成
  - _Requirements: 7.1, 7.4, 7.5_

- [ ] 9.3 初期データ収集戦略の実装
  - 履歴データの段階的収集スクリプトを作成
  - データ収集優先度管理機能を実装
  - 初回実行時の進捗監視機能を実装
  - 大量データ収集時のリソース管理機能を実装
  - _Requirements: 7.1, 7.4_

- [x] 9.4 本番環境デプロイメント準備
  - 本番用設定ファイルの作成
  - GitHub Secretsの設定手順書作成
  - 初回実行時の手順書作成
  - 段階的ロールアウト計画の作成
  - 緊急停止手順の作成
  - _Requirements: 8.1, 8.2_

- [ ] 10. データ品質管理システムの実装(クラス化は未実装、機能は src/validators/ と scripts/validate\_\*.py で提供済み)
- [ ] 10.1 QualityControllerクラスの実装
  - **既存の src/validators/ と scripts/validate\_\*.py をラップするだけ。機能自体は既に存在**
  - src/cli/validate_data.py, scripts/validate_continuity.py, scripts/validate_raw_quality.py, src/cli/check_missing.py をラップするクラスを作成
  - ファイル品質検証機能を統合
  - データ異常検出機能を統合
  - 問題ファイル隔離機能を実装
  - _Requirements: 6.1, 6.2, 6.4_
  - _注: 新しい品質管理機能を設計するのではなく、既存スクリプト/バリデーターをPythonクラスから呼べるようにするだけ_

- [ ] 10.2 各種バリデーターの実装
  - FileSizeValidatorクラスを実装
  - EncodingValidatorクラスを実装
  - CSVStructureValidatorクラスを実装
  - DataAnomalyDetectorクラスを実装
  - _Requirements: 6.1, 6.2, 6.3_
  - _注: 性別合計検証 (gender_sum_validator.py) と品質検証 orchestrator (quality_validator.py) は src/validators/ に実装済み_

- [ ] 10.3 品質管理のユニットテスト作成
  - 各バリデーターのテストケースを作成
  - 異常検出機能のテストケースを作成
  - ファイル隔離機能のテストケースを作成
  - _Requirements: 6.1, 6.2, 6.4_

- [ ] 11. 通知システムの実装(クラス化は未実装、GitHub Actions + Issuesで実現済み)
- [ ] 11.1 NotificationSystemクラスの実装
  - **現状の GitHub Actions + Issues 連携を Python からも呼べるようにするだけ**
  - 既存のGitHub Actions Issue連携をラップするクラスを作成
  - GitHub Issues API連携機能を統合
  - エラー用Issue作成機能を統合
  - データ異常アラート作成機能を実装
  - _Requirements: 2.2_
  - _注: 新しい通知システムを設計するのではなく、既存の仕組みをPythonから使えるようにするだけ_

- [ ] 11.2 通知テンプレートの作成
  - エラー通知用Issueテンプレートを作成
  - データ異常アラート用テンプレートを作成
  - システム状態レポート用テンプレートを作成
  - _Requirements: 2.2_

- [ ] 11.3 通知システムのユニットテスト作成
  - GitHub API連携のテストケースを作成
  - Issue作成機能のテストケースを作成
  - 通知テンプレート機能のテストケースを作成
  - _Requirements: 2.2_

- [ ] 12. セキュリティ機能の実装(将来の大規模拡張 - 人間レビュー必須)
- [ ] 12.1 SecurityValidatorクラスの実装
  - **注: これは大規模な新規アーキテクチャ追加です。人間の設計レビューが必須**
  - 実行環境のセキュリティ検証機能を実装
  - 依存関係の脆弱性チェック機能を実装
  - ログメッセージの機密情報マスキング機能を実装
  - _Requirements: 8.1, 8.3, 8.4_
  - _Priority: Low - 基本的なセキュリティは既に実装済み_

- [ ] 12.2 セキュリティ設定の実装
  - GitHub Secretsの適切な使用を実装
  - 最小権限トークンの設定を実装
  - HTTPS通信の強制を実装
  - _Requirements: 8.1, 8.2, 8.3_

- [ ] 12.3 セキュリティ機能のテスト作成
  - セキュリティ検証機能のテストケースを作成
  - 機密情報マスキング機能のテストケースを作成
  - セキュリティ設定のテストケースを作成
  - _Requirements: 8.1, 8.4_

- [ ] 13. 監視システムの実装(未実装)
- [ ] 13.1 MonitoringSystemクラスの実装
  - 実行メトリクス記録機能を実装
  - システム健全性レポート生成機能を実装
  - ディスク使用量チェック機能を実装
  - _Requirements: 7.2_

- [ ] 13.2 メトリクス管理機能の実装
  - SystemMetricsデータクラスを実装
  - メトリクスファイル管理機能を実装
  - 傾向分析機能を実装
  - _Requirements: 7.2_

- [ ] 13.3 監視システムのユニットテスト作成
  - メトリクス記録機能のテストケースを作成
  - レポート生成機能のテストケースを作成
  - 傾向分析機能のテストケースを作成
  - _Requirements: 7.2_

- [ ] 14. エラーハンドリングシステムの強化(部分実装済み)
- [ ] 14.1 ErrorClassifierとErrorHandlerの実装
  - エラータイプ分類機能を実装
  - エラータイプ別処理機能を実装
  - エラーログ記録機能を実装
  - _Requirements: 2.1, 2.4_

- [ ] 14.2 ログ管理システムの実装
  - 構造化ログ機能を実装(JSON形式でのログ出力)
  - ログレベル別の出力制御を実装
  - ログローテーション機能を実装
  - デバッグモード用の詳細ログ機能を実装
  - _Requirements: 2.1, 4.4, 8.4_

- [ ] 14.3 エラーハンドリングのユニットテスト作成
  - エラー分類機能のテストケースを作成
  - エラー処理機能のテストケースを作成
  - ログ機能のテストケースを作成
  - _Requirements: 2.1, 4.4_

- [ ] 15. パフォーマンス最適化の実装(未実装)
- [ ] 15.1 並列処理機能の実装
  - ParallelDataFetcherクラスを実装
  - 並列ダウンロード機能を実装
  - セマフォによる同時実行数制御を実装
  - _Requirements: 7.1_

- [ ] 15.2 メモリ管理機能の実装
  - StreamingProcessorクラスを実装
  - 大容量データのストリーミング処理を実装
  - メモリ使用量監視機能を実装
  - _Requirements: 7.5_

- [ ] 15.3 キャッシュシステムの実装
  - DataCacheクラスを実装
  - キャッシュデータの管理機能を実装
  - TTLベースのキャッシュ無効化を実装
  - _Requirements: 7.3_
