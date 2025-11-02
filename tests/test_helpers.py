"""テストヘルパー関数"""


def wrap_storage_save(storage):
    """StorageManagerのsave_with_metadataメソッドをテスト用にラップ"""
    original_save = storage.save_with_metadata

    def wrapped_save(**kwargs):
        data = kwargs.get("data", "")
        if isinstance(data, str):
            data = data.encode("utf-8")
        data_type = kwargs.get("data_type", "test")
        year = kwargs.get("year", 2024)
        period = kwargs.get("period", 1)
        period_type = kwargs.get("period_type", "week")
        is_monthly = period_type == "month"
        metadata = kwargs.get("metadata", kwargs.get("additional_metadata"))
        force_overwrite = kwargs.get("force_overwrite", False)

        return original_save(
            data=data,
            data_type=data_type,
            year=year,
            period=period,
            is_monthly=is_monthly,
            additional_metadata=metadata,
            force_overwrite=force_overwrite,
        )

    storage.save_with_metadata = wrapped_save
    return storage
