# Design Document

## Overview

東京都感染症発生動向情報の自動データ収集システムは、既存のTokyoEpidemicSurveillanceFetcherクラスを中核として、GitHub Actionsによるスケジュール実行、エラーハンドリング、データ品質管理、自動Git管理を統合したシステムです。

**現在の実装状況**: 基本的なフェッチャー機能とEnhancedEpidemicDataFetcherクラスが実装済み。設定ファイル（config.yml）、GitHub Actionsワークフロー（fetch-data.yml）、およびプロジェクト構造も整備済み。

**実装方針**: 既存のコードとスクリプトを最大限活用し、新規クラスや大規模リファクタリングは必要最小限に留めます。ディレクトリ構造（data/raw のフラット構造）やファイル命名規則（{data*type}*{year}\_{period:02d}.csv）は変更しません。

システムは以下の主要コンポーネントで構成されます：

- **Data Collector (Enhanced Fetcher)**: ✅ 実装済み - 既存のTokyoEpidemicSurveillanceFetcherを拡張したデータ取得エンジン
- **Configuration Manager**: ✅ 実装済み - YAML設定ファイルによる設定管理（config.yml）
- **Storage Manager**: ✅ 実装済み - ファイル管理とGit操作を担当
- **Automation System**: ✅ 実装済み - GitHub Actionsベースのスケジューリングと実行制御システム（fetch-data.yml）
- **Quality Controller**: 🔄 部分実装済み - データ検証スクリプト（validate_data.py, validate_continuity.py, check_missing.py）
- **Notification System**: ✅ 実装済み - GitHub Issues連携によるエラー通知
- **Execution Manager**: 🔄 実装予定 - 実行時間制限とチェックポイント管理
- **Security Validator**: 🔄 実装予定 - セキュリティ検証と機密情報保護

### Key Design Decisions

1. **既存コードの活用**: TokyoEpidemicSurveillanceFetcherクラスを継承・拡張し、既存の実装を最大限活用（Requirements 1.2）
2. **GitHub Actions中心設計**: CI/CDプラットフォームの制約（実行時間制限、リソース制限）を考慮した設計（Requirements 1.1, 4.2, 4.5）
3. **ファイルベース状態管理**: データベース不要で、ファイルシステムとGitによる状態管理（Requirements 3.5）
4. **段階的データ収集**: 大量の履歴データを効率的に処理するための分割実行戦略（Requirements 7.1, 7.4）
5. **包括的エラーハンドリング**: 指数バックオフリトライとGitHub Issues連携による通知システム（Requirements 1.5, 2.2）
6. **データ品質重視**: ファイル検証、異常検出、隔離機能による信頼性確保（Requirements 6.1-6.5）
7. **セキュリティファースト**: 最小権限、HTTPS通信、機密情報保護の徹底（Requirements 8.1-8.5）
8. **設定駆動アーキテクチャ**: YAMLベース設定による柔軟性とメンテナンス性（Requirements 4.1, 4.6）

## Architecture

### System Architecture

````mermaid
graph TB
    subgraph "GitHub Actions Environment"
        A[Scheduler] --> B[Configuration Manager]
        B --> C[Data Fetcher]
        C --> D[Quality Controller]
        D --> E[Storage Manager]
        E --> F[Notification System]
    end

    subgraph "External Systems"
        G[Tokyo Metropolitan Government API]
        H[GitHub Repository]
        I[GitHub Issues API]
    end

    C --> G
    E --> H
    F --> I

    subgraph "Data Flow"
        J[CSV Files] --> K[Metadata Logs]
        K --> L[Git Commits]
    end

    E --> J
    E --> K
    E --> L

### GitHub Actions Workflow Design

要件に基づくワークフロー設計（✅ 実装済み: .github/workflows/fetch-data.yml）：

```yaml
# .github/workflows/fetch-data.yml の概要
name: Fetch Tokyo Epidemic Data
on:
  schedule:
    - cron: '0 10 * * 1'  # cronベースのスケジューリング (Requirement 4.2)
  workflow_dispatch:      # 手動トリガーサポート (Requirement 4.5)
    inputs:
      start_year:
        description: 'Start year (YYYY)'
        required: false
      end_year:
        description: 'End year (YYYY)'
        required: false

jobs:
  fetch-data:
    runs-on: ubuntu-latest
    timeout-minutes: 360  # 6時間制限 (Requirement 7.4)
    permissions:
      contents: write      # 最小権限の原則 (Requirement 8.1)
      issues: write        # Issue作成用
    steps:
      - name: Setup and Execute
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}  # GitHub Secrets管理 (Requirement 8.2)
        # 実行時間制限を考慮した分割実行戦略 (Requirement 7.4)
        # HTTPS接続のみ使用 (Requirement 8.3)
````

````

### Component Interaction Flow

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant CM as Config Manager
    participant DF as Data Fetcher
    participant QC as Quality Controller
    participant SM as Storage Manager
    participant NS as Notification System

    S->>CM: Load configuration
    CM->>DF: Initialize with parameters
    DF->>DF: Fetch epidemic data
    DF->>QC: Validate downloaded data
    QC->>SM: Store validated files
    SM->>SM: Commit to Git
    alt Success
        SM->>S: Report completion
    else Error
        QC->>NS: Send error notification
        NS->>NS: Create GitHub Issue
    end
````

## Components and Interfaces

### 0. Execution Manager

GitHub Actionsの実行時間制限（6時間）を考慮した実行管理：

```python
class ExecutionManager:
    def __init__(self, max_execution_time: timedelta = timedelta(hours=5.5)):
        self.max_execution_time = max_execution_time
        self.start_time = datetime.now()
        self.checkpoint_manager = CheckpointManager()

    def should_continue(self) -> bool:
        """実行継続可否の判定"""
        elapsed = datetime.now() - self.start_time
        return elapsed < self.max_execution_time

    def create_checkpoint(self, state: ExecutionState) -> None:
        """実行状態のチェックポイント作成"""

    def resume_from_checkpoint(self) -> Optional[ExecutionState]:
        """チェックポイントからの実行再開"""

class CheckpointManager:
    def save_state(self, state: ExecutionState, checkpoint_file: Path) -> None:
        """実行状態の保存"""

    def load_state(self, checkpoint_file: Path) -> Optional[ExecutionState]:
        """実行状態の復元"""
```

### 1. Data Collector (Enhanced Fetcher) - ✅ 実装済み

既存のTokyoEpidemicSurveillanceFetcherクラスを拡張し、要件に基づく機能を実装済み：

**実装済み機能**:

- ✅ RetryHandler: 指数バックオフによるリトライ機能 (Requirements 1.5, 5.2)
- ✅ RateLimiter: レート制限管理 (Requirement 5.1)
- ✅ User-Agent設定: 自動化システム識別 (Requirement 5.4)
- ✅ fetch_with_retry: 非同期・同期両対応のリトライ機能
- ✅ fetch_date_range: 日付範囲での一括取得 (Requirement 5.3)
- ✅ get_missing_data: 欠損データの特定と重複回避 (Requirement 3.6)
- ✅ メタデータ生成: SHA256ハッシュ、タイムスタンプ付き (Requirements 3.3, 3.4)

**現在の実装**:

```python
class EnhancedEpidemicDataFetcher(TokyoEpidemicSurveillanceFetcher):
    def __init__(self, config: DataFetcherConfig | None = None):
        super().__init__()
        self.config = config or DataFetcherConfig()
        self.retry_handler = RetryHandler(self.config)
        self.rate_limiter = RateLimiter(self.config.rate_limit_delay)

        # User-Agent設定 (Requirement 5.4)
        self.session.headers.update({"User-Agent": self.config.user_agent})

    async def fetch_with_retry_async(self, fetch_method, **params) -> FetchResult:
        """指数バックオフによるリトライ機能付きデータ取得 (Requirements 1.5, 5.2)"""

    def fetch_date_range(self, data_type: str, start_date: tuple, end_date: tuple) -> list[FetchResult]:
        """日付範囲での一括取得、レート制限考慮 (Requirements 5.1, 5.3)"""

    def get_missing_data(self, data_type: str, existing_files: list[Path]) -> list[FetchParams]:
        """欠損データの特定と重複回避 (Requirement 3.6)"""
```

**データ構造処理機能**:

- ✅ 全数報告CSV解析: メタデータ抽出、疾病名・報告数の特定
- ✅ 定点監視CSV解析: 性別セクション分割、年齢別データ処理
- ✅ 感染症列の動的抽出: ヘッダー行からの自動検出
- ✅ 引用符処理: CSVフィールドの正しい解析

**追加実装予定**:

- 🔄 並列処理機能の強化 (Requirement 7.1)
- 🔄 HTTPS接続の強制確認 (Requirement 8.3)
- 🔄 動的レート制限調整 (Requirement 5.5)

````

### 2. Configuration Manager - ✅ 部分実装済み

YAML設定ファイルによる柔軟な設定管理、要件に基づく設定機能：

**実装済み機能**:
- ✅ config.yml: 包括的な設定ファイル (Requirements 4.1, 4.2, 4.5)
- ✅ スケジュール設定: cronベース、手動トリガー対応
- ✅ データタイプ設定: 9種類のデータタイプ定義済み
- ✅ ストレージ設定: ディレクトリ構造、自動コミット設定
- ✅ 品質管理設定: ファイルサイズ制限、異常検出設定
- ✅ 通知設定: GitHub Issues連携設定

**現在の設定構造**:
```yaml
schedule:
  cron: "0 10 * * 1"  # 毎週月曜日実行 (Requirement 4.2)
  manual_trigger_enabled: true  # 手動トリガー (Requirement 4.5)

collection:
  incremental_mode: true  # 増分収集
  start_year: 2024
  data_types: [9種類のデータタイプ]  # (Requirement 4.1)

storage:
  base_directory: "data/raw"
  auto_commit: true  # Git自動コミット (Requirement 3.5)
  keep_shift_jis: true  # エンコーディング維持 (Requirement 3.2)

quality:
  file_size_limits: [100, 10485760]  # (Requirement 6.1)
  anomaly_detection_enabled: true  # (Requirement 6.3)

notifications:
  github_issues_enabled: true  # (Requirement 2.2)
````

**追加実装予定**:

- 🔄 ConfigurationManagerクラスの実装
- 🔄 設定検証機能 (Requirement 4.6)
- 🔄 セキュリティ設定セクション (Requirements 8.1, 8.2)
- 🔄 ログ設定セクション (Requirement 4.4)

# config.yml の例

"""
schedule:
cron: "0 2 \* \* 1"
timezone: "Asia/Tokyo"
manual_trigger_enabled: true

data_collection:
incremental_mode: true # 増分収集モード
batch_size: 50 # 一度に処理するファイル数
date_ranges: - start: "2024-01-01"
end: "2024-12-31"
priority: "high" - start: "2000-01-01"
end: "2023-12-31"
priority: "low"

data_types:

- name: "sentinel_weekly_gender"
  enabled: true
  fetch_method: "fetch_csv_sentinel_weekly_gender"
  parameters:
  epid_code: "00"
- name: "sentinel_weekly_age"
  enabled: true
  fetch_method: "fetch_csv_sentinel_weekly_age"

storage:
base_directory: "data/raw"
directory_structure: "" # フラット構造（現状未使用）
auto_commit: true
commit_message_template: "Add {data_type} data for {date_range}"

quality:
file_size_limits:
csv: [100, 10485760] # 100B - 10MB
anomaly_detection_enabled: true
quarantine_enabled: true
"""

`````

### 3. Storage Manager - ✅ 実装済み

ファイル管理とGit操作の統合、要件に基づく機能実装：

**実装済み機能**:
- ✅ save_csv: CSVファイルの保存（Shift_JISエンコーディング維持）
- ✅ save_metadata: メタデータログの保存（JSON形式）
- ✅ commit_and_push: Git自動コミット・プッシュ
- ✅ get_existing_files: 既存ファイルの取得
- ✅ calculate_file_hash: SHA256ハッシュ計算

**現在の実装**:

```python
class StorageManager:
    def __init__(self, base_dir: Path = Path("data/raw")):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_csv(self, data: bytes, filename: str) -> Path:
        """Shift_JISエンコーディング維持でCSVファイル保存 (Requirements 3.1, 3.2, 3.3)"""

    def save_metadata(self, metadata: FileMetadata) -> Path:
        """メタデータログの保存 (Requirement 3.4)"""

    def commit_and_push(self, message: str) -> None:
        """Git自動コミット・プッシュ (Requirement 3.5)"""

    def get_existing_files(self, pattern: str = "*.csv") -> list[Path]:
        """既存ファイルの取得 (Requirement 3.6)"""

    def calculate_file_hash(self, file_path: Path) -> str:
        """SHA256ハッシュ計算 (Requirements 3.4, 3.6, 7.3)"""
```

**追加実装予定**:
- 🔄 archive_old_data: 古いデータのアーカイブ機能 (Requirement 7.2)
- 🔄 stream_large_files: 大容量ファイルのストリーミング処理 (Requirement 7.5)`

### 4. Quality Controller

データ品質管理と検証、要件に基づく包括的な品質保証：

```python
class QualityController:
    def __init__(self, quality_config: QualityConfig):
        self.validators = [
            FileSizeValidator(),      # Requirement 6.1
            EncodingValidator(),      # Requirement 6.2
            CSVStructureValidator(),  # Requirement 6.2
            DataAnomalyDetector()     # Requirement 6.3
        ]

    def validate_file(self, file_path: Path, metadata: FileMetadata) -> ValidationResult:
        """ファイルサイズと構造の品質検証 (Requirements 6.1, 6.2)"""

    def validate_file_size(self, file_size: int, expected_range: Tuple[int, int]) -> bool:
        """ファイルサイズが期待範囲内であることを検証 (Requirement 6.1)"""

    def validate_csv_structure(self, file_path: Path) -> ValidationResult:
        """基本的なCSV構造とエンコーディングを検証 (Requirement 6.2)"""

    def detect_anomalies(self, current_data: DataFrame, historical_data: List[DataFrame]) -> AnomalyReport:
        """過去データとの比較による重大な異常検出 (Requirement 6.3)"""

    def quarantine_file(self, file_path: Path, reason: str) -> None:
        """疑わしいファイルの隔離と管理者アラート (Requirement 6.4)"""

    def trigger_redownload(self, corrupted_file: Path, fetch_params: FetchParams) -> bool:
        """データ破損検出時の影響ファイル再ダウンロード (Requirement 6.5)"""

    def generate_quality_report(self) -> QualityReport:
        """データ品質レポートの生成"""
```

### 5. Notification System

GitHub Issues APIを使用した通知システム、要件に基づく通知機能：

````python
class NotificationSystem:
    def __init__(self, github_token: str, repo_name: str):
        self.github = Github(github_token)
        self.repo = self.github.get_repo(repo_name)

    def create_error_issue(self, error: Exception, context: Dict) -> Issue:
        """最大リトライ回数超過時のGitHub Issue作成 (Requirement 2.2)"""

    def create_anomaly_alert(self, anomaly_report: AnomalyReport) -> Issue:
        """データ異常検出時のアラート作成 (Requirement 6.4)"""

    def create_critical_error_alert(self, error: Exception, troubleshooting_info: Dict) -> Issue:
        """重大エラー継続時のトラブルシューティング情報付きアラート (Requirement 2.5)"""

    def create_security_alert(self, security_issue: SecurityIssue) -> Issue:
        """セキュリティ脆弱性検出時の緊急アラート (Requirement 8.5)"""

    def update_status_issue(self, status: SystemStatus) -> None:
        """システム状態更新"""

    def mask_sensitive_info(self, message: str) -> str:
        """通知メッセージの機密情報マスキング (Requirement 8.4)"""

### 6. Security Validator

セキュリティ検証と機密情報保護、要件に基づくセキュリティ機能：

```python
class SecurityValidator:
    def __init__(self, security_config: SecurityConfig):
        self.security_config = security_config

    def validate_environment(self) -> SecurityReport:
        """実行環境のセキュリティ検証 (Requirement 8.5)"""

    def validate_token_permissions(self, token: str) -> bool:
        """最小権限の原則でトークン権限を検証 (Requirement 8.1)"""

    def check_dependencies(self) -> VulnerabilityReport:
        """依存関係の脆弱性チェック (Requirement 8.5)"""

    def sanitize_logs(self, log_message: str) -> str:
        """ログメッセージの機密情報マスキング (Requirement 8.4)"""

    def validate_https_only(self, url: str) -> bool:
        """HTTPS接続のみの使用を検証 (Requirement 8.3)"""

    def manage_secrets(self, secret_key: str) -> str:
        """GitHub Secretsでの機密情報管理 (Requirement 8.2)"""

    def stop_on_vulnerability(self, vulnerability: SecurityVulnerability) -> None:
        """脆弱性検出時の実行停止と管理者通知 (Requirement 8.5)"""

### 7. Monitoring System

システム監視とメトリクス収集：

```python
class MonitoringSystem:
    def __init__(self, metrics_file: Path):
        self.metrics_file = metrics_file
        self.metrics = SystemMetrics()

    def record_execution_metrics(self, execution_result: ExecutionResult) -> None:
        """実行メトリクスの記録"""

    def generate_health_report(self) -> HealthReport:
        """システム健全性レポート生成"""

    def check_disk_usage(self) -> DiskUsageReport:
        """ストレージ容量制限監視とアーカイブ提案 (Requirement 7.2)"""

    def analyze_download_trends(self) -> TrendAnalysis:
        """ダウンロード傾向分析"""

    def monitor_memory_usage(self) -> MemoryReport:
        """メモリ使用量監視と閾値チェック (Requirement 7.5)"""

@dataclass
class SystemMetrics:
    execution_count: int = 0
    success_rate: float = 0.0
    average_execution_time: timedelta = timedelta()
    total_files_downloaded: int = 0
    total_data_size: int = 0
    last_successful_run: Optional[datetime] = None
    error_counts: Dict[str, int] = field(default_factory=dict)
`````

````

## Data Models

### Core Data Structures - ✅ 実装済み

**実装済みデータモデル**:

```python
@dataclass
class FetchParams:
    """データ取得パラメータ - ✅ 実装済み"""
    start_year: str
    start_sub_period: str
    end_year: str
    end_sub_period: str
    data_type: str
    report_type: str
    pref_code: str = "13"
    hc_code: str = "00"
    epid_code: str = "00"
    total_mode: str = "0"

@dataclass
class FileMetadata:
    """ファイルメタデータ - ✅ 実装済み"""
    filename: str
    data_type: str
    date_range: str
    timestamp: datetime
    file_size: int
    sha256_hash: str
    encoding: str = "shift_jis"  # (Requirement 3.2)
    fetch_params: FetchParams | None = None

@dataclass
class FetchResult:
    """データ取得結果 - ✅ 実装済み"""
    success: bool
    data: bytes | None = None
    metadata: FileMetadata | None = None
    error: Exception | None = None
    retry_count: int = 0
    fetch_time: float | None = None

@dataclass
class DataFetcherConfig:
    """フェッチャー設定 - ✅ 実装済み"""
    max_retries: int = 3  # (Requirement 1.6)
    base_delay: float = 1.0
    max_delay: float = 60.0
    timeout: int = 30
    rate_limit_delay: float = 1.0  # (Requirement 5.1)
    enable_jitter: bool = True
    user_agent: str = "TokyoEpidemicDataFetcher/1.0 (GitHub Actions Automation)"
```

### Processed Data Structures - ✅ 実装済み

**Shift_JIS CSV処理後のデータ構造**:

```python
# 全数報告データの処理後構造（pandas DataFrame）
# 列: ['疾病名', '報告数', 'year', 'period', 'data_type', 'category',
#      'report_frequency', 'aggregation', 'start_week', 'end_week']
notifiable_df = pd.DataFrame({
    '疾病名': ['インフルエンザ', '結核', ...],
    '報告数': [123, 45, ...],
    'year': [2024, 2024, ...],
    'period': [1, 1, ...],
    'data_type': ['notifiable_weekly', ...],
    'category': ['全数報告', ...],
    'report_frequency': ['週次', ...],
    'aggregation': ['全体集計', ...],
    'start_week': ['2024年第1週', ...],
    'end_week': ['2024年第1週', ...]
})

# 定点監視データの処理後構造（pandas DataFrame）
# 列: ['地域・年齢区分', 'インフルエンザ', 'RSウイルス感染症', ...,
#      'gender', 'year', 'period', 'data_type', 'category',
#      'report_frequency', 'aggregation']
sentinel_df = pd.DataFrame({
    '地域・年齢区分': ['千代田', '中央', '港', ...],
    'インフルエンザ': [10.5, 8.3, 12.1, ...],
    'RSウイルス感染症': [2.1, 1.5, 3.2, ...],
    # ... 他の感染症列 ...
    'gender': ['男女合計', '男女合計', ...],
    'year': [2024, 2024, ...],
    'period': [1, 1, ...],
    'data_type': ['sentinel_weekly_gender', ...],
    'category': ['定点監視', ...],
    'report_frequency': ['週次', ...],
    'aggregation': ['男女別', ...]
})

# 年齢別データの処理後構造（pandas DataFrame）
# 列: ['地域・年齢区分', 'インフルエンザ', 'RSウイルス感染症', ...,
#      'gender', 'year', 'period', 'data_type', 'category',
#      'report_frequency', 'aggregation']
age_df = pd.DataFrame({
    '地域・年齢区分': ['0歳', '1-4歳', '5-9歳', ...],
    'インフルエンザ': [5.2, 15.3, 20.1, ...],
    'RSウイルス感染症': [8.5, 3.2, 1.1, ...],
    # ... 他の感染症列 ...
    'gender': ['男', '男', '男', ...],
    'year': [2024, 2024, ...],
    'period': [1, 1, ...],
    'data_type': ['sentinel_weekly_age', ...],
    'category': ['定点監視', ...],
    'report_frequency': ['週次', ...],
    'aggregation': ['年齢別', ...]
})
```

**データ構造の特徴**:

1. **全数報告データ**: 疾病名と報告数の2列構造 + メタデータ列
2. **定点監視データ**: 地域・年齢区分 + 複数の感染症列（動的） + メタデータ列
3. **性別セクション**: 男、女、男女合計の3セクションに分割
4. **年齢別データ**: 年齢区分（0歳、1-4歳等）+ 感染症列 + 性別情報
5. **メタデータ列**: year, period, data_type, category, report_frequency, aggregation
6. **数値型**: 報告数・感染症列は全てfloat型（欠損値は0.0）

**追加実装予定のデータモデル**:

```python
@dataclass
class ValidationResult:
    """データ検証結果 - 🔄 実装予定"""
    is_valid: bool
    warnings: List[str]
    errors: List[str]
    quality_score: float

@dataclass
class ExecutionState:
    """実行状態 - 🔄 実装予定"""
    current_year: int
    current_month: int
    current_week: int
    completed_data_types: List[str]
    failed_attempts: Dict[str, int]
    checkpoint_time: datetime
    total_progress: float  # 0.0 - 1.0

@dataclass
class SecurityReport:
    """セキュリティレポート - 🔄 実装予定"""
    is_secure: bool
    vulnerabilities: List[str]
    recommendations: List[str]
```
````

### Configuration Models

```python
@dataclass
class ScheduleConfig:
    cron_expression: str
    timezone: str
    manual_trigger_enabled: bool

@dataclass
class DataTypeConfig:
    name: str
    enabled: bool
    fetch_method: str
    parameters: Dict[str, Any]

@dataclass
class StorageConfig:
    base_directory: str
    directory_structure: str  # "{year}/{month}/{week}"
    auto_commit: bool
    commit_message_template: str

@dataclass
class QualityConfig:
    file_size_limits: Dict[str, Tuple[int, int]]  # min, max bytes
    anomaly_detection_enabled: bool
    anomaly_threshold: float
    quarantine_enabled: bool
```

## Error Handling

### Retry Strategy

要件に基づく包括的なリトライ戦略：

```python
class RetryHandler:
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries  # Requirement 1.5
        self.base_delay = base_delay

    async def execute_with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """指数バックオフによるリトライ実行 (Requirements 1.5, 5.2)"""
        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries:
                    # 最大リトライ回数超過時の通知 (Requirement 2.2)
                    raise MaxRetriesExceededException(e, attempt)

                # エラータイプに応じた遅延調整
                delay = self.calculate_backoff_delay(e, attempt)
                await asyncio.sleep(delay)

    def calculate_backoff_delay(self, error: Exception, attempt: int) -> float:
        """エラータイプに応じた指数バックオフ計算 (Requirements 5.2, 5.5)"""
        base_delay = self.base_delay * (2 ** attempt)

        if isinstance(error, RateLimitError):
            return base_delay * 2  # レート制限時は長めの遅延
        elif isinstance(error, NetworkTimeoutError):
            return base_delay  # ネットワークタイムアウト時は標準遅延
        else:
            return base_delay
```

### Error Classification

```python
class ErrorClassifier:
    @staticmethod
    def classify_error(error: Exception) -> ErrorType:
        """エラータイプの分類"""
        if isinstance(error, requests.exceptions.Timeout):
            return ErrorType.NETWORK_TIMEOUT
        elif isinstance(error, requests.exceptions.HTTPError):
            if error.response.status_code == 429:
                return ErrorType.RATE_LIMIT
            elif error.response.status_code >= 500:
                return ErrorType.SERVER_ERROR
        return ErrorType.UNKNOWN

class ErrorHandler:
    def handle_error(self, error: Exception, context: Dict) -> ErrorResponse:
        """エラータイプに応じた処理 (Requirements 2.1, 2.3, 2.4)"""
        error_type = ErrorClassifier.classify_error(error)

        if error_type == ErrorType.RATE_LIMIT:
            # レート制限時の適切な遅延実装 (Requirement 2.3)
            return ErrorResponse(action=Action.BACKOFF, delay=300)
        elif error_type == ErrorType.NETWORK_TIMEOUT:
            # ネットワーク接続問題の適切な処理 (Requirement 2.4)
            return ErrorResponse(action=Action.RETRY, delay=60)
        elif error_type == ErrorType.CRITICAL_ERROR:
            # 重大エラー継続時のトラブルシューティング情報付きアラート (Requirement 2.5)
            return ErrorResponse(action=Action.NOTIFY, create_issue=True, include_troubleshooting=True)
        else:
            return ErrorResponse(action=Action.NOTIFY, create_issue=True)

    def log_detailed_error(self, error: Exception, context: Dict) -> None:
        """詳細なエラー情報のログ記録 (Requirement 2.1)"""
        sanitized_context = self.sanitize_sensitive_data(context)
        logger.error(f"Error occurred: {error}", extra=sanitized_context)

    def sanitize_sensitive_data(self, data: Dict) -> Dict:
        """機密情報のマスクまたは除外 (Requirement 8.4)"""
        # 機密情報をマスクして返す
        return {k: "***MASKED***" if self.is_sensitive(k) else v for k, v in data.items()}
```

## Testing Strategy

### Unit Testing

各コンポーネントの単体テスト：

```python
class TestDataFetcher:
    def test_fetch_with_retry_success(self):
        """正常なリトライ処理のテスト"""

    def test_fetch_with_retry_max_exceeded(self):
        """最大リトライ回数超過のテスト"""

    def test_rate_limiting(self):
        """レート制限処理のテスト"""

class TestStorageManager:
    def test_file_organization(self):
        """ファイル整理機能のテスト"""

    def test_duplicate_detection(self):
        """重複検出機能のテスト"""

    def test_git_operations(self):
        """Git操作のテスト"""
```

### Integration Testing

```python
class TestEndToEndWorkflow:
    def test_complete_data_collection_workflow(self):
        """完全なデータ収集ワークフローのテスト"""

    def test_error_recovery_workflow(self):
        """エラー回復ワークフローのテスト"""

    def test_github_actions_integration(self):
        """GitHub Actions統合テスト"""
```

### Performance Testing

```python
class TestPerformance:
    def test_large_date_range_processing(self):
        """大量データ処理のパフォーマンステスト"""

    def test_memory_usage_monitoring(self):
        """メモリ使用量監視テスト"""

    def test_concurrent_downloads(self):
        """並列ダウンロードのテスト"""
```

## Logging System

要件に基づく包括的なログシステム：

### Multi-Level Logging

```python
class LoggingManager:
    def __init__(self, log_config: LoggingConfig):
        self.log_config = log_config
        self.setup_loggers()

    def setup_loggers(self) -> None:
        """複数の詳細レベルでの包括的ログ設定 (Requirement 4.4)"""
        # DEBUG, INFO, WARNING, ERROR, CRITICALレベルの設定

    def log_detailed_error(self, error: Exception, context: Dict) -> None:
        """詳細なエラー情報のログ記録 (Requirement 2.1)"""

    def log_execution_progress(self, progress: ExecutionProgress) -> None:
        """実行進捗の詳細ログ"""

    def sanitize_log_message(self, message: str) -> str:
        """機密情報のマスクまたは除外 (Requirement 8.4)"""

    def rotate_logs(self) -> None:
        """ログファイルのローテーション管理"""

@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: Optional[Path] = None
    max_file_size: int = 10485760  # 10MB
    backup_count: int = 5
    sanitize_sensitive_data: bool = True
```

## Security Considerations

### GitHub Actions Security

- **最小権限の原則**: 必要最小限のGitHub token権限を使用 (Requirement 8.1)
- **Secret管理**: 機密情報はGitHub Secretsで管理 (Requirement 8.2)
- **依存関係管理**: 定期的な依存関係の脆弱性スキャン (Requirement 8.5)

### Data Security

- **HTTPS通信**: 全ての外部API通信でHTTPS使用 (Requirement 8.3)
- **ログマスキング**: 機密情報のログ出力防止 (Requirement 8.4)
- **アクセス制御**: リポジトリアクセス権限の適切な設定

### Runtime Security

```python
class SecurityValidator:
    def validate_environment(self) -> SecurityReport:
        """実行環境のセキュリティ検証 (Requirement 8.5)"""

    def check_dependencies(self) -> VulnerabilityReport:
        """依存関係の脆弱性チェック (Requirement 8.5)"""

    def sanitize_logs(self, log_message: str) -> str:
        """ログメッセージの機密情報マスキング (Requirement 8.4)"""

    def stop_on_security_issue(self, vulnerability: SecurityVulnerability) -> None:
        """脆弱性検出時の実行停止と管理者通知 (Requirement 8.5)"""
```

## Performance Optimization

### Parallel Processing

```python
class ParallelDataFetcher:
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)

    async def fetch_multiple_dates(self, date_ranges: List[DateRange]) -> List[FetchResult]:
        """並列データ取得"""
        tasks = [self.fetch_date_range_with_semaphore(dr) for dr in date_ranges]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

### Memory Management

```python
class StreamingProcessor:
    def process_large_dataset(self, file_paths: List[Path]) -> Iterator[ProcessedData]:
        """大容量データのストリーミング処理"""
        for file_path in file_paths:
            with open(file_path, 'rb') as f:
                yield self.process_chunk(f.read(CHUNK_SIZE))
```

### Caching Strategy

```python
class DataCache:
    def __init__(self, cache_dir: Path, ttl_hours: int = 24):
        self.cache_dir = cache_dir
        self.ttl = timedelta(hours=ttl_hours)

    def get_cached_data(self, cache_key: str) -> Optional[bytes]:
        """キャッシュデータの取得"""

    def cache_data(self, cache_key: str, data: bytes) -> None:
        """データのキャッシュ保存"""

## Deployment Strategy

### Repository Structure - 現在の実装状況

**注意**: 以下は現在の実装状況を反映したディレクトリ構造です。実際の設定は config/config.yml をソース・オブ・トゥルースとしてください。

**実装済み構造**:
```

fetch-tokyo-idsc-github-actions/
├── .github/
│ └── workflows/
│ └── fetch-data.yml # ✅ メインワークフロー実装済み
├── src/
│ ├── fetchers/ # ✅ 実装済み
│ │ ├── **init**.py
│ │ ├── base*fetcher.py # ✅ TokyoEpidemicSurveillanceFetcher
│ │ └── enhanced_fetcher.py # ✅ 拡張版フェッチャー
│ └── managers/ # ✅ 実装済み
│ ├── **init**.py
│ ├── config_manager.py # ✅ 設定管理実装済み
│ └── storage_manager.py # ✅ ストレージ管理実装済み
├── config/
│ └── config.yml # ✅ 包括的設定ファイル（ソース・オブ・トゥルース）
├── scripts/ # ✅ 実装済み
│ ├── fetch_data.py # ✅ メインスクリプト
│ ├── validate_data.py # ✅ データ検証
│ ├── validate_continuity.py # ✅ 連続性検証
│ └── check_missing.py # ✅ 欠損チェック
├── data/
│ ├── raw/ # ✅ CSVファイル保存（フラット構造）
│ └── logs/ # ✅ メタデータログ
├── tests/ # ✅ テスト構造
│ ├── test*\*.py
│ └── fixtures/
├── pyproject.toml # ✅ プロジェクト設定
├── .gitignore # ✅ 設定済み
├── .pre-commit-config.yaml # ✅ 品質管理
├── README.md # ✅ 基本ドキュメント
└── CLAUDE.md # ✅ 開発ドキュメント

````

**将来の拡張予定（人間レビュー必須）**:
- 🔄 src/quality/ - データ品質管理クラスの統合
- 🔄 src/notifications/ - 通知システムクラスの統合
- 🔄 src/security/ - セキュリティ機能
- 🔄 src/execution/ - 実行管理システム

### Environment Variables

```bash
# GitHub Actions Secrets
GITHUB_TOKEN          # リポジトリアクセス用
NOTIFICATION_TOKEN    # Issue作成用（必要に応じて）

# Optional Configuration
DATA_COLLECTION_CONFIG # 設定ファイルパスのオーバーライド
LOG_LEVEL             # ログレベル設定
DRY_RUN               # テスト実行モード
````

### Continuous Integration

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest tests/ -v
      - name: Run linting
        run: |
          flake8 src/
          black --check src/
          mypy src/
```
