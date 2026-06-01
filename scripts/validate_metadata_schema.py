#!/usr/bin/env python3
"""メタデータ JSON を JSON Schema (v1.3.0) で検証する.

型定義 (src/models/metadata.py) とは独立した第2の検証層として、生成済みの
全メタデータが schemas/metadata-v1.3.schema.json に適合するかを検証する。
CI で実行し、schema と実データの drift / 構造崩れ (例: quality の格納位置ずれ) を
早期検出することを目的とする。

実行例:
    uv run python scripts/validate_metadata_schema.py
    uv run python scripts/validate_metadata_schema.py --schema schemas/metadata-v1.3.schema.json data/raw/.metadata
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

# 個別メタデータではない (= schema 検証の対象外) 集約/インデックスファイル
NON_METADATA_FILES = frozenset({"hash_index.json", "processing_log.json"})

DEFAULT_SCHEMA = Path("schemas/metadata-v1.3.schema.json")
DEFAULT_METADATA_DIRS = (Path("data/raw/.metadata"), Path("data/processed/.metadata"))

# 不適合が大量に出た場合に表示を打ち切る上限
_MAX_REPORTED = 50


def iter_metadata_files(dirs: Iterable[Path]) -> Iterator[Path]:
    """検証対象の個別メタデータ JSON を列挙する (非メタデータファイルは除外)."""
    for directory in dirs:
        if not directory.is_dir():
            continue
        for json_file in sorted(directory.glob("*.json")):
            if json_file.name in NON_METADATA_FILES:
                continue
            yield json_file


def validate(schema_path: Path, dirs: Iterable[Path]) -> tuple[int, list[tuple[Path, str]]]:
    """全メタデータを検証し (検証件数, 違反リスト) を返す.

    違反リストの各要素は (ファイルパス, 最初のエラーの要約) のタプル。
    Validator はループ前に一度だけ生成し、再コンパイルによる性能劣化を避ける。
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    # schema 自体が有効な JSON Schema (Draft 2020-12) かを先に検証する (メタスキーマ検証)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    total = 0
    violations: list[tuple[Path, str]] = []
    for json_file in iter_metadata_files(dirs):
        total += 1
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            violations.append((json_file, f"JSON 読み込み失敗: {exc}"))
            continue

        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            location = "/".join(str(p) for p in first.path) or "(root)"
            violations.append((json_file, f"{location}: {first.message}"))

    return total, violations


def main(argv: list[str] | None = None) -> int:
    """CLI エントリポイント. 不適合があれば 1、スキーマ未検出は 2、成功は 0 を返す."""
    parser = argparse.ArgumentParser(description="メタデータ JSON を JSON Schema (v1.3.0) で検証")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="スキーマファイルのパス")
    parser.add_argument(
        "metadata_dirs",
        nargs="*",
        type=Path,
        help="検証対象の .metadata ディレクトリ (省略時は data/raw/.metadata と data/processed/.metadata)",
    )
    args = parser.parse_args(argv)

    dirs = args.metadata_dirs or list(DEFAULT_METADATA_DIRS)

    if not args.schema.is_file():
        print(f"エラー: スキーマが見つかりません: {args.schema}", file=sys.stderr)
        return 2

    try:
        total, violations = validate(args.schema, dirs)
    except (SchemaError, json.JSONDecodeError, OSError) as exc:
        print(f"エラー: スキーマの読み込み/検証に失敗しました: {exc}", file=sys.stderr)
        return 2

    if violations:
        print(f"メタデータ schema 検証: {total}件中 {len(violations)}件が不適合 (schema: {args.schema.name})")
        for path, message in violations[:_MAX_REPORTED]:
            print(f"  {path}: {message}")
        if len(violations) > _MAX_REPORTED:
            print(f"  ... 他 {len(violations) - _MAX_REPORTED}件")
        return 1

    print(f"メタデータ schema 検証: {total}件すべて適合 (schema: {args.schema.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
