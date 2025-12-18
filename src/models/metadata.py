"""メタデータモデル

東京都感染症データのメタデータを管理するための共通モデル。
Frictionless Data Package仕様を参考に設計。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

logger = logging.getLogger(__name__)

# メタデータスキーマバージョン (Semantic Versioning)
METADATA_VERSION = "1.2.0"

# プロファイル定義
PROFILE_RAW = "tokyo-idsc-raw"
PROFILE_PROCESSED = "tokyo-idsc-processed"


@dataclass
class TemporalInfo:
    """時間情報

    Attributes:
        year: データの年
        period: 期間番号 (週: 1-53, 月: 1-12)
        period_type: 期間タイプ ("weekly" or "monthly")
    """

    year: int
    period: int
    period_type: Literal["weekly", "monthly"]

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemporalInfo:
        """辞書から作成"""
        return cls(
            year=data["year"],
            period=data["period"],
            period_type=data["period_type"],
        )


@dataclass
class HashInfo:
    """ハッシュ情報 (SPDX/DCAT v3準拠)

    Attributes:
        algorithm: ハッシュアルゴリズム
        value: ハッシュ値 (16進数文字列)
    """

    algorithm: Literal["sha256", "sha512", "md5"]
    value: str

    def to_dict(self) -> dict[str, str]:
        """辞書形式に変換"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> HashInfo:
        """辞書から作成"""
        return cls(
            algorithm=cast(Literal["sha256", "sha512", "md5"], data["algorithm"]),
            value=data["value"],
        )


@dataclass
class Verification:
    """検証情報

    Attributes:
        status: 検証ステータス
        verified_at: 検証日時 (ISO 8601)
        method: 検証方法
        checks: 各検証項目の結果
        errors: エラーメッセージリスト
        warnings: 警告メッセージリスト
    """

    status: Literal["verified", "failed", "pending"]
    verified_at: str | None = None
    method: Literal["automated", "manual"] = "automated"
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Verification:
        """辞書から作成"""
        return cls(
            status=data.get("status", "pending"),
            verified_at=data.get("verified_at"),
            method=data.get("method", "automated"),
            checks=data.get("checks", {}),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )


@dataclass
class FetchInfo:
    """フェッチ情報 (raw データ用プライベートプロパティ)

    Attributes:
        source_url: データ取得元URL
        fetch_time_seconds: 取得にかかった時間 (秒)
        force_overwrite: 強制上書きフラグ
        save_all_zero: 全て0のデータも保存するフラグ
    """

    source_url: str | None = None
    fetch_time_seconds: float = 0.0
    force_overwrite: bool = False
    save_all_zero: bool = False

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FetchInfo:
        """辞書から作成"""
        return cls(
            source_url=data.get("source_url"),
            fetch_time_seconds=data.get("fetch_time_seconds", 0.0),
            force_overwrite=data.get("force_overwrite", False),
            save_all_zero=data.get("save_all_zero", False),
        )


@dataclass
class ProcessInfo:
    """処理情報 (processed データ用プライベートプロパティ)

    Attributes:
        source_name: 元ファイル名 (拡張子なし)
        source_hash: 元ファイルのハッシュ値
        processing_time_seconds: 処理にかかった時間 (秒)
        gender: 性別カテゴリ (sentinel データの場合)
    """

    source_name: str
    source_hash: str
    processing_time_seconds: float = 0.0
    gender: Literal["male", "female", "total"] | None = None

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessInfo:
        """辞書から作成"""
        return cls(
            source_name=data["source_name"],
            source_hash=data["source_hash"],
            processing_time_seconds=data.get("processing_time_seconds", 0.0),
            gender=data.get("gender"),
        )


@dataclass
class Metadata:
    """共通メタデータクラス

    raw/processed両方に対応した統一メタデータモデル。
    Frictionless Data Package仕様を参考に設計。

    Attributes:
        metadata_version: メタデータスキーマバージョン
        name: URL-safe識別子
        filename: ファイル名
        path: 相対パス
        profile: プロファイル種別
        data_type: データタイプ
        temporal: 時間情報
        bytes: ファイルサイズ
        lines: 行数
        hash: ハッシュ情報
        encoding: 文字エンコーディング
        created: 作成日時 (ISO 8601)
        modified: 更新日時 (ISO 8601)
        sources: データソース情報
        verification: 検証情報
        _fetch: フェッチ情報 (raw用)
        _process: 処理情報 (processed用)
    """

    # 必須フィールド
    name: str
    filename: str
    path: str
    profile: Literal["tokyo-idsc-raw", "tokyo-idsc-processed"]
    data_type: str
    temporal: TemporalInfo
    bytes: int
    hash: HashInfo
    encoding: Literal["shift_jis", "utf-8"]
    created: str
    modified: str

    # オプションフィールド
    metadata_version: str = METADATA_VERSION
    lines: int | None = None
    sources: list[dict[str, str]] = field(default_factory=list)
    verification: Verification | None = None

    # プライベートプロパティ (プロファイルに応じて使用)
    _fetch: FetchInfo | None = None
    _process: ProcessInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        """辞書形式に変換 (JSON出力用)"""
        result: dict[str, Any] = {
            "metadata_version": self.metadata_version,
            "name": self.name,
            "filename": self.filename,
            "path": self.path,
            "profile": self.profile,
            "data_type": self.data_type,
            "temporal": self.temporal.to_dict(),
            "bytes": self.bytes,
            "lines": self.lines,
            "hash": self.hash.to_dict(),
            "encoding": self.encoding,
            "created": self.created,
            "modified": self.modified,
        }

        if self.sources:
            result["sources"] = self.sources

        if self.verification:
            result["verification"] = self.verification.to_dict()

        # プライベートプロパティ
        if self._fetch:
            result["_fetch"] = self._fetch.to_dict()
        if self._process:
            result["_process"] = self._process.to_dict()

        return result

    def to_json(self, indent: int = 2) -> str:
        """JSON文字列に変換"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: Path) -> None:
        """ファイルに保存"""
        with path.open("w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Metadata:
        """辞書から作成"""
        # ネストされたオブジェクトの変換
        temporal = TemporalInfo.from_dict(data["temporal"])
        hash_info = HashInfo.from_dict(data["hash"])
        verification = Verification.from_dict(data["verification"]) if data.get("verification") else None

        fetch_info = FetchInfo.from_dict(data["_fetch"]) if data.get("_fetch") else None
        process_info = ProcessInfo.from_dict(data["_process"]) if data.get("_process") else None

        return cls(
            metadata_version=data.get("metadata_version", METADATA_VERSION),
            name=data["name"],
            filename=data["filename"],
            path=data["path"],
            profile=data["profile"],
            data_type=data["data_type"],
            temporal=temporal,
            bytes=data["bytes"],
            lines=data.get("lines"),
            hash=hash_info,
            encoding=data["encoding"],
            created=data["created"],
            modified=data["modified"],
            sources=data.get("sources", []),
            verification=verification,
            _fetch=fetch_info,
            _process=process_info,
        )

    @classmethod
    def load(cls, path: Path) -> Metadata:
        """ファイルから読み込み"""
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_legacy_raw(cls, legacy: dict[str, Any]) -> Metadata:
        """旧形式 (v1.0) のrawメタデータから変換

        Args:
            legacy: 旧形式のメタデータ辞書

        Returns:
            新形式のMetadataオブジェクト
        """
        # 名前を生成 (ファイル名から拡張子を除く)
        filename = legacy.get("filename", "")
        name = filename.replace(".csv", "")

        # 時間情報
        temporal = TemporalInfo(
            year=legacy.get("year", 0),
            period=legacy.get("period", 0),
            period_type=legacy.get("period_type", "weekly"),
        )

        # ハッシュ情報
        hash_info = HashInfo(
            algorithm=legacy.get("checksum_algorithm", "sha256"),
            value=legacy.get("sha256_hash", ""),
        )

        # 日時の変換 (created_at/updated_at または timestamp)
        created = legacy.get("created_at") or legacy.get("timestamp") or _now_iso()
        modified = legacy.get("updated_at") or legacy.get("timestamp") or _now_iso()

        # 検証情報
        verification = None
        if legacy.get("verification"):
            verification = Verification.from_dict(legacy["verification"])

        # フェッチ情報
        fetch_info = FetchInfo(
            source_url=legacy.get("source_url"),
            fetch_time_seconds=legacy.get("fetch_time", 0.0),
            force_overwrite=legacy.get("force_overwrite", False),
            save_all_zero=legacy.get("save_all_zero", False),
        )

        # ソース情報
        sources = []
        if legacy.get("source_url"):
            sources.append({"title": "Tokyo IDSC", "path": legacy["source_url"]})

        return cls(
            metadata_version=METADATA_VERSION,
            name=name,
            filename=filename,
            path=legacy.get("file_path", filename),
            profile=cast(Literal["tokyo-idsc-raw", "tokyo-idsc-processed"], PROFILE_RAW),
            data_type=legacy.get("data_type", ""),
            temporal=temporal,
            bytes=legacy.get("file_size", 0),
            lines=legacy.get("line_count"),
            hash=hash_info,
            encoding=cast(Literal["shift_jis", "utf-8"], legacy.get("encoding", "shift_jis")),
            created=created,
            modified=modified,
            sources=sources,
            verification=verification,
            _fetch=fetch_info,
        )

    @classmethod
    def from_legacy_processed(cls, legacy: dict[str, Any], source_metadata: Metadata | None = None) -> Metadata:
        """旧形式の処理ログエントリから変換

        Args:
            legacy: 旧形式の処理ログエントリ
            source_metadata: 元ファイルのメタデータ (トレーサビリティ用)

        Returns:
            新形式のMetadataオブジェクト
        """
        # 出力ファイル情報から抽出 (最初の出力を使用)
        output = legacy.get("outputs", [{}])[0]
        output_path = output.get("path", "")
        filename = Path(output_path).name if output_path else ""
        name = filename.replace(".csv", "")

        # メタデータから時間情報を抽出
        meta = legacy.get("metadata", {})
        temporal = TemporalInfo(
            year=int(meta.get("year", 0)),
            period=int(meta.get("period", 0)),
            period_type="monthly" if meta.get("frequency") == "monthly" else "weekly",
        )

        # ソースファイル情報
        source_path = legacy.get("source", "")
        source_name = Path(source_path).stem if source_path else ""
        source_hash = source_metadata.hash.value if source_metadata else ""

        # 処理情報
        gender: Literal["male", "female", "total"] | None = None
        if "_male_" in filename:
            gender = "male"
        elif "_female_" in filename:
            gender = "female"
        elif "_total_" in filename:
            gender = "total"

        process_info = ProcessInfo(
            source_name=source_name,
            source_hash=source_hash,
            processing_time_seconds=0.0,
            gender=gender,
        )

        # ハッシュは空 (processed用に再計算が必要)
        hash_info = HashInfo(algorithm="sha256", value="")

        timestamp = legacy.get("timestamp", _now_iso())

        return cls(
            metadata_version=METADATA_VERSION,
            name=name,
            filename=filename,
            path=output_path,
            profile=cast(Literal["tokyo-idsc-raw", "tokyo-idsc-processed"], PROFILE_PROCESSED),
            data_type=f"{meta.get('category', '')}_{meta.get('frequency', '')}_{meta.get('aggregation', '')}".strip(
                "_"
            ),
            temporal=temporal,
            bytes=output.get("size_bytes", 0),
            lines=None,
            hash=hash_info,
            encoding=cast(Literal["shift_jis", "utf-8"], "utf-8"),
            created=timestamp,
            modified=timestamp,
            sources=[],
            verification=None,
            _process=process_info,
        )

    @classmethod
    def create_raw(
        cls,
        *,
        filename: str,
        data_type: str,
        year: int,
        period: int,
        period_type: Literal["weekly", "monthly"],
        file_size: int,
        line_count: int | None,
        sha256_hash: str,
        source_url: str | None = None,
        fetch_time: float = 0.0,
        force_overwrite: bool = False,
        save_all_zero: bool = False,
        verification: Verification | None = None,
        created_at: str | None = None,
    ) -> Metadata:
        """rawメタデータを作成するファクトリメソッド

        Args:
            filename: ファイル名
            data_type: データタイプ
            year: 年
            period: 期間番号
            period_type: 期間タイプ
            file_size: ファイルサイズ
            line_count: 行数
            sha256_hash: SHA256ハッシュ
            source_url: 取得元URL
            fetch_time: 取得時間
            force_overwrite: 強制上書きフラグ
            save_all_zero: 全て0保存フラグ
            verification: 検証結果
            created_at: 作成日時 (省略時は現在時刻)

        Returns:
            Metadataオブジェクト
        """
        name = filename.replace(".csv", "")
        now = _now_iso()

        sources = []
        if source_url:
            sources.append({"title": "Tokyo IDSC", "path": source_url})

        return cls(
            metadata_version=METADATA_VERSION,
            name=name,
            filename=filename,
            path=filename,
            profile=cast(Literal["tokyo-idsc-raw", "tokyo-idsc-processed"], PROFILE_RAW),
            data_type=data_type,
            temporal=TemporalInfo(year=year, period=period, period_type=period_type),
            bytes=file_size,
            lines=line_count,
            hash=HashInfo(algorithm="sha256", value=sha256_hash),
            encoding=cast(Literal["shift_jis", "utf-8"], "shift_jis"),
            created=created_at or now,
            modified=now,
            sources=sources,
            verification=verification,
            _fetch=FetchInfo(
                source_url=source_url,
                fetch_time_seconds=fetch_time,
                force_overwrite=force_overwrite,
                save_all_zero=save_all_zero,
            ),
        )

    @classmethod
    def create_processed(
        cls,
        *,
        filename: str,
        data_type: str,
        year: int,
        period: int,
        period_type: Literal["weekly", "monthly"],
        file_size: int,
        line_count: int | None,
        sha256_hash: str,
        source_name: str,
        source_hash: str,
        processing_time: float = 0.0,
        gender: Literal["male", "female", "total"] | None = None,
        verification: Verification | None = None,
    ) -> Metadata:
        """processedメタデータを作成するファクトリメソッド

        Args:
            filename: ファイル名
            data_type: データタイプ
            year: 年
            period: 期間番号
            period_type: 期間タイプ
            file_size: ファイルサイズ
            line_count: 行数
            sha256_hash: SHA256ハッシュ
            source_name: 元ファイル名
            source_hash: 元ファイルのハッシュ
            processing_time: 処理時間
            gender: 性別カテゴリ
            verification: 検証結果

        Returns:
            Metadataオブジェクト
        """
        name = filename.replace(".csv", "")
        now = _now_iso()

        return cls(
            metadata_version=METADATA_VERSION,
            name=name,
            filename=filename,
            path=f"processed/{filename}",
            profile=cast(Literal["tokyo-idsc-raw", "tokyo-idsc-processed"], PROFILE_PROCESSED),
            data_type=data_type,
            temporal=TemporalInfo(year=year, period=period, period_type=period_type),
            bytes=file_size,
            lines=line_count,
            hash=HashInfo(algorithm="sha256", value=sha256_hash),
            encoding=cast(Literal["shift_jis", "utf-8"], "utf-8"),
            created=now,
            modified=now,
            sources=[],
            verification=verification,
            _process=ProcessInfo(
                source_name=source_name,
                source_hash=source_hash,
                processing_time_seconds=processing_time,
                gender=gender,
            ),
        )


def _now_iso() -> str:
    """現在時刻をISO 8601形式で取得 (UTC)"""
    return datetime.now(UTC).isoformat()
