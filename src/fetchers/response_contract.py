"""
Contract check for a fetched CSV body, run before the body is saved.

Even with HTTP 200, a body that is empty, HTML, undecodable, for another period or data type,
or in a changed layout raises InvalidResponseError so that the fetch counts as failed. The
legitimate body of an unpublished period (title and period rows, then header-only or all
zeros) passes and is left to the storage layer's all-zero check, as before.

The rules found zero violations in every file under data/raw (from 2000 on) and in the
unpublished bodies that were once saved and later deleted:
- Row 0 is the fixed title of the data type (TITLES). Rows 2 and 3 are the start and end of
  the aggregation period, valued `{year}年{period}{週|月}` (no zero padding) as requested.
- gender / notifiable: exactly one row starts with `疾病名` and equals the fixed header. Every
  later non-empty row has the header's width and digits in all cells but the first.
- age / health_center / medical_district: the `性別` rows are 男性, 女性, 男女合計 in order.
  Exactly three header rows (empty first cell) exist and are identical; disease columns may
  be added upstream, so their names are not fixed. Data rows from a header row up to the next
  title or `性別` row hold digits, empty strings or `*` in all cells but the first.

Decoding is strict shift_jis, not cp932: the storage layer and validate-data assume shift_jis
and every published file decodes strictly, so a cp932-only character fails loudly here.

Data types missing from TITLES are not checked (all 9 production types are listed). The
fullwidth parentheses of the notifiable_weekly title are written as escapes because the
fix-fullwidth-symbols pre-commit hook rewrites them even inside string literals.
"""

import csv
import io
import re

TITLES: dict[str, str] = {
    "sentinel_weekly_gender": "定点報告疾患 週報告分 男女別集計表",
    "sentinel_weekly_age": "定点報告疾患 週報告分 年齢別集計表",
    "sentinel_weekly_health_center": "定点報告疾患 週報告分 保健所別",
    "sentinel_weekly_medical_district": "定点報告疾患 週報告分 医療圏別",
    "sentinel_monthly_gender": "定点報告疾患 月報告分 男女別集計表",
    "sentinel_monthly_age": "定点報告疾患 月報告分 年齢別集計表",
    "sentinel_monthly_health_center": "定点報告疾患 月報告分 保健所別",
    "sentinel_monthly_medical_district": "定点報告疾患 月報告分 医療圏別",
    "notifiable_weekly": "全数報告疾病 週報告分 \uff08集計\uff09",
}

_GENDER_HEADER = ["疾病名", "男性", "女性", "男女合計", "定点数"]
_NOTIFIABLE_HEADER = ["疾病名", "報告数"]
_SEXES = [["性別", "男性"], ["性別", "女性"], ["性別", "男女合計"]]
_MATRIX_SECTIONS = len(_SEXES)
# ASCII digits only: \d also matches fullwidth and other Unicode digits
_COUNT = re.compile(r"[0-9]+")
_MATRIX_CELL = re.compile(r"[0-9]*|\*")


class InvalidResponseError(ValueError):
    """The fetched body does not match the contract of the published CSV."""


def validate_response(data: bytes, data_type: str, year: int, period: int) -> None:
    """Check that `data` is the published CSV of `data_type` for week / month `period` of `year`.

    Raises:
        InvalidResponseError: The body breaks the contract; the message says how.
    """
    title = TITLES.get(data_type)
    if title is None:
        return

    try:
        text = data.decode("shift_jis")
    except UnicodeDecodeError as e:
        raise InvalidResponseError(f"not decodable as shift_jis ({e.reason} at byte {e.start})") from e
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as e:
        raise InvalidResponseError(f"not parsable as CSV ({e})") from e

    if not rows or rows[0] != [title]:
        raise InvalidResponseError(f"unexpected title row {rows[0] if rows else None!r}")

    unit = "月" if "monthly" in data_type else "週"
    label = f"{year}年{period}{unit}"
    expected_period_rows = [[f"集計期間開始{unit}", label], [f"集計期間終了{unit}", label]]
    if rows[2:4] != expected_period_rows:
        raise InvalidResponseError(f"period rows {rows[2:4]!r} do not match {label}")

    if data_type == "notifiable_weekly":
        _check_table(rows, _NOTIFIABLE_HEADER)
    elif data_type.endswith("_gender"):
        _check_table(rows, _GENDER_HEADER)
    else:
        _check_matrix(rows, title)


def _check_table(rows: list[list[str]], header: list[str]) -> None:
    """One row per disease (gender / notifiable)."""
    header_indexes = [i for i, row in enumerate(rows) if row and row[0] == header[0]]
    if len(header_indexes) != 1 or rows[header_indexes[0]] != header:
        found = [rows[i] for i in header_indexes]
        raise InvalidResponseError(f"expected exactly one header row {header!r}, found {found!r}")

    for row in rows[header_indexes[0] + 1 :]:
        if not row:
            continue
        if len(row) != len(header) or not all(_COUNT.fullmatch(cell) for cell in row[1:]):
            raise InvalidResponseError(f"unexpected data row {row!r}")


def _check_matrix(rows: list[list[str]], title: str) -> None:
    """Three matrix sections, one per sex (age / health_center / medical_district)."""
    sexes = [row for row in rows if row and row[0] == "性別"]
    if sexes != _SEXES:
        raise InvalidResponseError(f"unexpected sex rows {sexes!r}")

    header_indexes = [i for i, row in enumerate(rows) if len(row) >= 2 and row[0] == ""]
    headers = [rows[i] for i in header_indexes]
    if len(headers) != _MATRIX_SECTIONS or any(header != headers[0] for header in headers):
        raise InvalidResponseError(f"expected {_MATRIX_SECTIONS} identical header rows, found {headers!r}")

    for start in header_indexes:
        for row in rows[start + 1 :]:
            if row == [title] or (row and row[0] == "性別"):
                break
            if not row or row[0].startswith("*"):
                continue
            if not all(_MATRIX_CELL.fullmatch(cell) for cell in row[1:]):
                raise InvalidResponseError(f"unexpected data row {row!r}")
