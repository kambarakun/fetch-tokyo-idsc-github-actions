"""非同期処理とエラーハンドリングの包括的なテスト"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.fetchers.enhanced_fetcher import (
    DataFetcherConfig,
    EnhancedEpidemicDataFetcher,
    FetchParams,
    RateLimiter,
    RetryHandler,
)


class TestAsyncErrorHandling:
    """非同期処理とエラーハンドリングの詳細テスト"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.config = DataFetcherConfig(
            max_retries=3,
            base_delay=0.1,
            max_delay=1.0,
            timeout=5,
            rate_limit_delay=0.1,
            enable_jitter=False,
        )
        self.fetcher = EnhancedEpidemicDataFetcher()
        self.retry_handler = RetryHandler(config=self.config)
        self.rate_limiter = RateLimiter(min_delay=0.1)  # 10 requests per second = 0.1s delay

    @pytest.mark.asyncio
    async def test_retry_handler_with_http_errors(self):
        """HTTPエラーコードごとのリトライ処理"""
        # 429 (Rate Limit) - 長い待機時間
        with patch("src.fetchers.enhanced_fetcher.HTTPError") as mock_error:
            mock_error.return_value.response.status_code = 429

            async def rate_limited_func():
                raise mock_error.return_value

            # リトライ設定
            config = DataFetcherConfig(max_retries=2, base_delay=0.1, max_delay=1.0)
            handler = RetryHandler(config=config)

            with pytest.raises(Exception):
                await handler.execute_with_retry(rate_limited_func)

    @pytest.mark.asyncio
    async def test_concurrent_batch_processing(self):
        """並行バッチ処理のテスト"""
        # モックセッションの設定
        with patch("aiohttp.ClientSession") as mock_session:
            mock_instance = MagicMock()
            mock_session.return_value.__aenter__.return_value = mock_instance

            # 様々なレスポンスを返す
            responses = []
            for i in range(10):
                mock_response = MagicMock()
                if i % 3 == 0:  # 3の倍数はエラー
                    mock_response.status = 500
                else:
                    mock_response.status = 200
                    mock_response.text = AsyncMock(return_value=f"data_{i}")
                responses.append(mock_response)

            mock_instance.post.side_effect = responses

            # バッチ処理を実行
            params_list = [
                FetchParams(
                    start_year="2024",
                    start_sub_period="1",
                    end_year="2024",
                    end_sub_period="1",
                    data_type="test",
                    report_type="1",
                )
                for i in range(1, 11)
            ]

            # 並行度を変えてテスト
            for max_concurrent in [1, 3, 5]:
                results = await self.fetcher.batch_fetch_parallel(params_list, max_concurrent=max_concurrent)

                # 結果の検証
                successful = [r for r in results if r is not None]
                assert len(successful) > 0

    @pytest.mark.asyncio
    async def test_timeout_and_cancellation(self):
        """タイムアウトとキャンセレーション処理"""

        # 長時間かかる処理
        async def slow_operation():
            await asyncio.sleep(10)
            return "completed"

        # タイムアウトテスト
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_operation(), timeout=0.1)

        # キャンセレーションテスト
        task = asyncio.create_task(slow_operation())
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_rate_limiter_under_load(self):
        """高負荷時のレートリミッターの動作"""
        limiter = RateLimiter(min_delay=0.1)  # 10 requests per second

        # 100個のリクエストを同時に送信

        async def timed_acquire():
            start = time.time()
            await limiter.acquire()
            return time.time() - start

        # 並列実行
        tasks = [timed_acquire() for _ in range(20)]
        wait_times = await asyncio.gather(*tasks)

        # レート制限が機能していることを確認
        # 20リクエスト / 10rps = 約2秒
        assert max(wait_times) > 0  # 待機時間が発生

    @pytest.mark.asyncio
    async def test_error_aggregation_in_batch(self):
        """バッチ処理でのエラー集約"""
        # 様々なエラーを発生させる
        error_types = [
            aiohttp.ClientError("Connection error"),
            aiohttp.ServerTimeoutError("Timeout"),
            ValueError("Invalid data"),
            KeyError("Missing key"),
            None,  # 成功ケース
        ]

        async def fetch_with_errors(params, error_type):
            if error_type:
                raise error_type
            return f"success_{params.start_sub_period}"

        # バッチ処理
        results = []
        errors = []

        for i, error in enumerate(error_types):
            params = FetchParams(
                start_year="2024",
                start_sub_period="1",
                end_year="2024",
                end_sub_period="1",
                data_type="test",
                report_type="1",
            )

            try:
                result = await fetch_with_errors(params, error)
                results.append(result)
            except Exception as e:
                errors.append({"params": params, "error": e, "type": type(e).__name__})

        # エラーの分析
        assert len(errors) == 4
        assert len(results) == 1

        # エラータイプの集計
        error_types_count = {}
        for error_info in errors:
            error_type = error_info["type"]
            error_types_count[error_type] = error_types_count.get(error_type, 0) + 1

        assert "ClientError" in error_types_count
        assert "ValueError" in error_types_count

    @pytest.mark.asyncio
    async def test_circuit_breaker_pattern(self):
        """サーキットブレーカーパターンの実装テスト"""

        class CircuitBreaker:
            def __init__(self, failure_threshold=3, recovery_timeout=1.0):
                self.failure_count = 0
                self.failure_threshold = failure_threshold
                self.recovery_timeout = recovery_timeout
                self.last_failure_time = None
                self.state = "closed"  # closed, open, half-open

            async def call(self, func, *args, **kwargs):
                if self.state == "open":
                    if time.time() - self.last_failure_time > self.recovery_timeout:
                        self.state = "half-open"
                    else:
                        raise Exception("Circuit breaker is open")

                try:
                    result = await func(*args, **kwargs)
                    if self.state == "half-open":
                        self.state = "closed"
                        self.failure_count = 0
                    return result
                except Exception as e:
                    self.failure_count += 1
                    self.last_failure_time = time.time()

                    if self.failure_count >= self.failure_threshold:
                        self.state = "open"

                    raise e

        breaker = CircuitBreaker(failure_threshold=3)

        call_count = 0

        async def unreliable_service():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise Exception("Service error")
            return "success"

        # 3回失敗してサーキットがオープンになる
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(unreliable_service)

        assert breaker.state == "open"

        # サーキットがオープンの間は呼び出しがブロックされる
        with pytest.raises(Exception, match="Circuit breaker is open"):
            await breaker.call(unreliable_service)

        # 復旧タイムアウト後
        await asyncio.sleep(1.1)
        breaker.state = "half-open"

        # 成功すればサーキットが閉じる
        result = await breaker.call(unreliable_service)
        assert result == "success"
        assert breaker.state == "closed"

    @pytest.mark.asyncio
    async def test_graceful_degradation(self):
        """グレースフルデグラデーションのテスト"""

        # 段階的な機能低下をシミュレート
        class DegradableService:
            def __init__(self):
                self.degradation_level = 0
                self.request_count = 0

            async def fetch_data(self, params):
                self.request_count += 1

                # 負荷に応じて機能を制限
                if self.request_count > 100:
                    self.degradation_level = 3  # 最小限の機能
                elif self.request_count > 50:
                    self.degradation_level = 2  # 一部機能制限
                elif self.request_count > 20:
                    self.degradation_level = 1  # 軽微な制限

                if self.degradation_level == 3:
                    # 最小限のデータのみ返す
                    return {"status": "degraded", "data": "minimal"}
                if self.degradation_level == 2:
                    # 基本データのみ返す
                    return {"status": "limited", "data": "basic"}
                if self.degradation_level == 1:
                    # ほぼ完全なデータ
                    await asyncio.sleep(0.01)  # 少し遅延
                    return {"status": "slow", "data": "full"}
                # 完全なデータ
                return {"status": "ok", "data": "complete"}

        service = DegradableService()

        # 負荷をかけてデグラデーションを確認
        results = []
        for i in range(150):
            params = FetchParams(
                start_year="2024",
                start_sub_period="1",
                end_year="2024",
                end_sub_period="1",
                data_type="test",
                report_type="1",
            )
            result = await service.fetch_data(params)
            results.append(result)

        # デグラデーションレベルの確認
        assert service.degradation_level == 3
        assert results[-1]["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """非同期コンテキストマネージャーのテスト"""

        class AsyncResource:
            def __init__(self):
                self.acquired = False
                self.released = False

            async def __aenter__(self):
                self.acquired = True
                await asyncio.sleep(0.01)
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                self.released = True
                await asyncio.sleep(0.01)

        resource = AsyncResource()

        # 正常な使用
        async with resource as r:
            assert r.acquired
            assert not r.released

        assert resource.released

        # 例外発生時のクリーンアップ
        resource2 = AsyncResource()
        with pytest.raises(ValueError):
            async with resource2 as r:
                assert r.acquired
                raise ValueError("Test error")

        assert resource2.released
