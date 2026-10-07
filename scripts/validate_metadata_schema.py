#!/usr/bin/env python3
"""メタデータ JSON を JSON Schema (v1.3.0) で検証する.

型定義 (src/models/metadata.py) とは独立した第2の検証層として、生成済みの
全メタデータが schemas/metadata-v1.3.schema.json に適合するかを検証する。
CI で実行し、schema と実データの drift / 構造崩れ (例: quality の格納位置ずれ) を
早期検出することを目的とする。

検査内容と既定の対象:
- 構造と format (date-time): 引数のディレクトリ (既定は data/raw/.metadata と
  data/processed/.metadata) の全件。uri は従来どおり注釈扱いで検査しない
- metadata_version == METADATA_VERSION: --version-profiles に含まれる profile のファイルだけ
  (既定は tokyo-idsc-raw)。対象外 profile の不一致は警告として件数だけ出す。
  profile を持たないファイルは version 検査しない。processed は #738 で 1.3.0 に移行した後、
  DEFAULT_VERSION_PROFILES に tokyo-idsc-processed を足して既定の対象にする
- タイムゾーン (created / modified / verification.verified_at / quality.validation_timestamp):
  既定は警告だけ。--require-timezone で不適合にする

METADATA_VERSION を上げる PR では、raw の移行が済むまで version 検査が不適合になる。
bump PR に `uv run migrate-metadata` (raw と processed) の結果を同梱するか、
移行ワークフローの PR がマージされるまで赤を許容するかはオーナーが判断する。

実行例:
    uv run python scripts/validate_metadata_schema.py
    uv run python scripts/validate_metadata_schema.py --schema schemas/metadata-v1.3.schema.json data/raw/.metadata
    uv run python scripts/validate_metadata_schema.py --version-profiles tokyo-idsc-raw,tokyo-idsc-processed
    uv run python scripts/validate_metadata_schema.py --require-timezone data/raw/.metadata
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from src.cli.migrate_metadata import NON_METADATA_FILES
from src.models.metadata import METADATA_VERSION

__all__ = [
    "DEFAULT_SCHEMA",
    "DEFAULT_VERSION_PROFILES",
    "NON_METADATA_FILES",
    "ValidationResult",
    "iter_metadata_files",
    "main",
    "validate",
]

DEFAULT_SCHEMA = Path("schemas/metadata-v1.3.schema.json")
DEFAULT_METADATA_DIRS = (Path("data/raw/.metadata"), Path("data/processed/.metadata"))

KNOWN_PROFILES = frozenset({"tokyo-idsc-raw", "tokyo-idsc-processed"})
# version 検査の既定対象。processed は #738 の移行後にここへ足して有効化する
DEFAULT_VERSION_PROFILES = frozenset({"tokyo-idsc-raw"})

# タイムゾーン検査の対象 (schema で format: date-time のフィールド)
TIMESTAMP_FIELDS: tuple[tuple[str, ...], ...] = (
    ("created",),
    ("modified",),
    ("verification", "verified_at"),
    ("quality", "validation_timestamp"),
)

# RFC 3339 の date-time。タイムゾーンは任意にして、欠落は別途警告/不適合として扱う。
# オフセットの範囲は正規表現で縛る (fromisoformat は +00:60 を +01:00 に正規化して受け付けるため)
_DATE_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?")

# 不適合が大量に出た場合に表示を打ち切る上限
_MAX_REPORTED = 50


@dataclass
class ValidationResult:
    """検証結果. violations は (ファイルパス, 最初の不適合の要約) のリスト."""

    total: int = 0
    violations: list[tuple[Path, str]] = field(default_factory=list)
    # version 検査対象外の profile ごとの、metadata_version 不一致のファイル数
    version_warnings: Counter[str] = field(default_factory=Counter)
    # タイムゾーンなしの時刻を含むファイル数 (require_timezone=False のときだけ数える)
    timezone_warnings: int = 0


def _parse_date_time(value: str) -> datetime | None:
    if _DATE_TIME_RE.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _build_format_checker() -> FormatChecker:
    # jsonschema 同梱の date-time checker は rfc3339-validator が無いと登録されないため自前で持つ。
    # uri は登録しない (processed の sources[].path は相対参照 raw/<file>.csv のため)
    checker = FormatChecker(formats=())

    @checker.checks("date-time")
    def _is_date_time(instance: object) -> bool:
        # 文字列以外は schema の type が検査する
        if not isinstance(instance, str):
            return True
        return _parse_date_time(instance) is not None

    return checker


def _naive_timestamp_fields(data: dict) -> list[str]:
    naive: list[str] = []
    for keys in TIMESTAMP_FIELDS:
        value: object = data
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if not isinstance(value, str):
            continue
        parsed = _parse_date_time(value)
        if parsed is not None and parsed.tzinfo is None:
            naive.append("/".join(keys))
    return naive


def iter_metadata_files(dirs: Iterable[Path]) -> Iterator[Path]:
    """検証対象の個別メタデータ JSON を列挙する (非メタデータファイルは除外)."""
    for directory in dirs:
        if not directory.is_dir():
            continue
        for json_file in sorted(directory.glob("*.json")):
            if json_file.name in NON_METADATA_FILES:
                continue
            yield json_file


def validate(
    schema_path: Path,
    dirs: Iterable[Path],
    *,
    version_profiles: frozenset[str] = DEFAULT_VERSION_PROFILES,
    require_timezone: bool = False,
) -> ValidationResult:
    """全メタデータを検証し、件数・不適合・警告件数を返す.

    Validator はループ前に一度だけ生成し、再コンパイルによる性能劣化を避ける。
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    # schema 自体が有効な JSON Schema (Draft 2020-12) かを先に検証する (メタスキーマ検証)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=_build_format_checker())

    result = ValidationResult()
    for json_file in iter_metadata_files(dirs):
        result.total += 1
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result.violations.append((json_file, f"JSON 読み込み失敗: {exc}"))
            continue

        problems: list[str] = []
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            location = "/".join(str(p) for p in first.path) or "(root)"
            problems.append(f"{location}: {first.message}")

        if isinstance(data, dict):
            profile = data.get("profile")
            version = data.get("metadata_version")
            # 型の崩れた profile (配列など) は schema が報告済み。集合の検索で TypeError にしない
            if isinstance(profile, str) and profile in KNOWN_PROFILES and version != METADATA_VERSION:
                if profile in version_profiles:
                    problems.append(
                        f"metadata_version: {version!r} が METADATA_VERSION {METADATA_VERSION!r} と一致しません"
                        f" (profile: {profile})"
                    )
                else:
                    result.version_warnings[profile] += 1

            naive = _naive_timestamp_fields(data)
            if naive and require_timezone:
                problems.append(f"{naive[0]}: タイムゾーンがありません")
            elif naive:
                result.timezone_warnings += 1

        if problems:
            result.violations.append((json_file, problems[0]))

    return result


def _parse_version_profiles(value: str) -> frozenset[str]:
    profiles = frozenset(name.strip() for name in value.split(",") if name.strip())
    unknown = profiles - KNOWN_PROFILES
    if not profiles or unknown:
        msg = f"未知の profile です: {sorted(unknown) or value!r} (指定できる profile: {', '.join(sorted(KNOWN_PROFILES))})"
        raise ValueError(msg)
    return profiles


def _print_warnings(result: ValidationResult) -> None:
    if result.version_warnings:
        total = sum(result.version_warnings.values())
        by_profile = ", ".join(f"{p}: {n}件" for p, n in sorted(result.version_warnings.items()))
        print(
            f"警告: metadata_version が METADATA_VERSION ({METADATA_VERSION}) と異なるファイル {total}件"
            f" (version 検査対象外の profile のため不適合にしない。{by_profile})"
        )
    if result.timezone_warnings:
        print(
            f"警告: タイムゾーンなしの時刻を含むファイル {result.timezone_warnings}件"
            " (--require-timezone で不適合にする)"
        )


def main(argv: list[str] | None = None) -> int:
    """CLI エントリポイント. 不適合があれば 1、スキーマ不在・不正や未知の profile は 2、成功は 0 を返す."""
    parser = argparse.ArgumentParser(
        description=(
            "メタデータ JSON を JSON Schema (v1.3.0) で検証する。構造と date-time は対象ディレクトリの全件、"
            "metadata_version は --version-profiles の profile だけを検査する"
        )
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="スキーマファイルのパス")
    parser.add_argument(
        "--version-profiles",
        default=",".join(sorted(DEFAULT_VERSION_PROFILES)),
        help=(
            "metadata_version == METADATA_VERSION を検査する profile (カンマ区切り。"
            f"既定: {','.join(sorted(DEFAULT_VERSION_PROFILES))}。対象外の profile の不一致は警告。"
            "processed は #738 の移行後に既定へ追加する)"
        ),
    )
    parser.add_argument(
        "--require-timezone",
        action="store_true",
        help="タイムゾーンなしの時刻を不適合にする (既定は警告)",
    )
    parser.add_argument(
        "metadata_dirs",
        nargs="*",
        type=Path,
        help="検証対象の .metadata ディレクトリ (省略時は data/raw/.metadata と data/processed/.metadata)",
    )
    args = parser.parse_args(argv)

    dirs = args.metadata_dirs or list(DEFAULT_METADATA_DIRS)

    try:
        version_profiles = _parse_version_profiles(args.version_profiles)
    except ValueError as exc:
        print(f"エラー: --version-profiles: {exc}", file=sys.stderr)
        return 2

    if not args.schema.is_file():
        print(f"エラー: スキーマが見つかりません: {args.schema}", file=sys.stderr)
        return 2

    try:
        result = validate(
            args.schema,
            dirs,
            version_profiles=version_profiles,
            require_timezone=args.require_timezone,
        )
    except (SchemaError, json.JSONDecodeError, OSError) as exc:
        print(f"エラー: スキーマの読み込み/検証に失敗しました: {exc}", file=sys.stderr)
        return 2

    if result.violations:
        print(
            f"メタデータ schema 検証: {result.total}件中 {len(result.violations)}件が不適合 (schema: {args.schema.name})"
        )
        for path, message in result.violations[:_MAX_REPORTED]:
            print(f"  {path}: {message}")
        if len(result.violations) > _MAX_REPORTED:
            print(f"  ... 他 {len(result.violations) - _MAX_REPORTED}件")
        _print_warnings(result)
        return 1

    print(f"メタデータ schema 検証: {result.total}件すべて適合 (schema: {args.schema.name})")
    _print_warnings(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
