"""メタデータのスキーマ版

このモジュールが持つのはスキーマ版の定数だけである。メタデータの構造の正本は
schemas/metadata-v1.3.schema.json で、生成済みファイルは scripts/validate_metadata_schema.py
が検証する。writer (StorageManager / DataProcessor / verify-metadata / migrate-metadata) は
dict を直接組み立てる。
"""

# .github/workflows/migrate-metadata.yml がこの行を正規表現
# ^METADATA_VERSION\s*=\s*["']([^"']*)["'] で読むため、型注釈を付けるなど行の形を変えないこと
# (tests/test_metadata.py が契約を固定している)。
METADATA_VERSION = "1.3.0"
