"""テストの修正用ヘルパー - StorageManagerのAPIを適応させる"""


def adapt_save_call(storage, **kwargs):
    """save_with_metadataの呼び出しをAPIに合わせて変換"""
    # 必須パラメータ
    data = kwargs.get("data", "")
    if isinstance(data, str):
        data = data.encode("utf-8")

    data_type = kwargs.get("data_type", "test")
    year = kwargs.get("year", 2024)
    period = kwargs.get("period", 1)

    # オプションパラメータ
    period_type = kwargs.get("period_type", "week")
    is_monthly = period_type == "month"

    metadata = kwargs.get("metadata", None)
    force_overwrite = kwargs.get("force_overwrite", False)

    # 実際のメソッドを呼び出す
    return storage.save_with_metadata(
        data=data,
        data_type=data_type,
        year=year,
        period=period,
        is_monthly=is_monthly,
        additional_metadata=metadata,
        force_overwrite=force_overwrite,
    )
