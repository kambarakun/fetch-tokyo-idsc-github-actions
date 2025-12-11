"""
HTTPエラー時のリトライロジックのテスト
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from requests.exceptions import HTTPError

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.base_fetcher import TokyoEpidemicSurveillanceFetcher
from src.fetchers.enhanced_fetcher import DataFetcherConfig, RetryHandler


class TestBaseFetcherHTTPErrors(unittest.TestCase):
    """BaseFetcherのHTTPエラー処理のテスト"""

    def setUp(self):
        self.fetcher = TokyoEpidemicSurveillanceFetcher()

    @patch("requests.Session.post")
    def test_post_request_raises_http_error_on_403(self, mock_post):
        """403エラー時にHTTPErrorを投げることをテスト"""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = HTTPError("403 Forbidden")
        mock_post.return_value = mock_response

        with self.assertRaises(HTTPError):
            self.fetcher._post_request(
                endpoint="CSV100.do",
                report_type="csv1",
                pref_code="13",
                hc_code="00",
                epid_code="00",
                start_year="2025",
                start_sub_period="1",
                end_year="2025",
                end_sub_period="1",
                total_mode="0",
            )

        # raise_for_status()が呼ばれたことを確認
        mock_response.raise_for_status.assert_called_once()

    @patch("requests.Session.post")
    def test_post_request_raises_http_error_on_404(self, mock_post):
        """404エラー時にHTTPErrorを投げることをテスト"""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = HTTPError("404 Not Found")
        mock_post.return_value = mock_response

        with self.assertRaises(HTTPError):
            self.fetcher._post_request(
                endpoint="CSV100.do",
                report_type="csv1",
                pref_code="13",
                hc_code="00",
                epid_code="00",
                start_year="2025",
                start_sub_period="1",
                end_year="2025",
                end_sub_period="1",
                total_mode="0",
            )

    @patch("requests.Session.post")
    def test_post_request_raises_http_error_on_500(self, mock_post):
        """500エラー時にHTTPErrorを投げることをテスト"""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = HTTPError("500 Internal Server Error")
        mock_post.return_value = mock_response

        with self.assertRaises(HTTPError):
            self.fetcher._post_request(
                endpoint="CSV100.do",
                report_type="csv1",
                pref_code="13",
                hc_code="00",
                epid_code="00",
                start_year="2025",
                start_sub_period="1",
                end_year="2025",
                end_sub_period="1",
                total_mode="0",
            )

    @patch("requests.Session.post")
    def test_post_request_success_returns_content(self, mock_post):
        """200成功時にコンテンツを返すことをテスト"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"test data"
        mock_post.return_value = mock_response

        result = self.fetcher._post_request(
            endpoint="CSV100.do",
            report_type="csv1",
            pref_code="13",
            hc_code="00",
            epid_code="00",
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
            total_mode="0",
        )

        self.assertEqual(result, b"test data")
        # raise_for_status()が呼ばれていないことを確認
        mock_response.raise_for_status.assert_not_called()


class TestRetryHandlerHTTPErrors(unittest.IsolatedAsyncioTestCase):
    """RetryHandlerのHTTPエラー処理のテスト"""

    def setUp(self):
        self.config = DataFetcherConfig(max_retries=3, base_delay=1.0, max_delay=10.0, enable_jitter=False)
        self.handler = RetryHandler(self.config)

    def test_is_rate_limit_error_with_429(self):
        """429エラーは常にレート制限と判定されることをテスト"""
        response = Mock()
        response.status_code = 429
        response.headers = {}
        error = HTTPError("429 Too Many Requests")
        error.response = response

        self.assertTrue(self.handler._is_rate_limit_error(error))

    def test_is_rate_limit_error_with_403_and_retry_after(self):
        """Retry-Afterヘッダー付き403はレート制限と判定されることをテスト"""
        response = Mock()
        response.status_code = 403
        response.headers = {"Retry-After": "60"}
        error = HTTPError("403 Forbidden")
        error.response = response

        self.assertTrue(self.handler._is_rate_limit_error(error))

    def test_is_rate_limit_error_with_403_and_x_ratelimit_headers(self):
        """X-RateLimit-*ヘッダー付き403はレート制限と判定されることをテスト"""
        response = Mock()
        response.status_code = 403
        response.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1234567890"}
        error = HTTPError("403 Forbidden")
        error.response = response

        self.assertTrue(self.handler._is_rate_limit_error(error))

    def test_is_rate_limit_error_with_403_no_headers(self):
        """レート制限ヘッダーなし403はレート制限と判定されないことをテスト"""
        response = Mock()
        response.status_code = 403
        response.headers = {}
        error = HTTPError("403 Forbidden")
        error.response = response

        self.assertFalse(self.handler._is_rate_limit_error(error))

    def test_is_rate_limit_error_with_500(self):
        """500エラーはレート制限と判定されないことをテスト"""
        response = Mock()
        response.status_code = 500
        response.headers = {}
        error = HTTPError("500 Internal Server Error")
        error.response = response

        self.assertFalse(self.handler._is_rate_limit_error(error))

    def test_is_rate_limit_error_without_response(self):
        """responseがないエラーはレート制限と判定されないことをテスト"""
        error = HTTPError("Generic HTTP Error")

        self.assertFalse(self.handler._is_rate_limit_error(error))

    @patch("asyncio.sleep")
    async def test_http_error_without_response_uses_normal_delay(self, mock_sleep):
        """responseがないHTTPErrorは通常の遅延でリトライすることをテスト"""
        call_count = 0

        async def func_http_error_no_response_then_success():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # responseがないHTTPErrorをシミュレート
                error = HTTPError("Generic HTTP Error")
                # response属性を明示的に削除
                if hasattr(error, "response"):
                    delattr(error, "response")
                raise error
            return "success"

        result = await self.handler.execute_with_retry(func_http_error_no_response_then_success)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 2)
        # responseがない場合は通常の遅延
        # base_delay * 2^0 = 1.0 * 1 = 1.0
        mock_sleep.assert_called_once_with(1.0)

    @patch("asyncio.sleep")
    async def test_retry_on_403_with_rate_limit_headers(self, mock_sleep):
        """レート制限ヘッダー付き403エラー時に2倍の待機時間でリトライすることをテスト"""
        call_count = 0

        async def func_403_rate_limit_then_success():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # レート制限ヘッダー付き403エラーをシミュレート
                response = Mock()
                response.status_code = 403
                response.headers = {"Retry-After": "60", "X-RateLimit-Remaining": "0"}
                error = HTTPError("403 Forbidden")
                error.response = response
                raise error
            return "success"

        result = await self.handler.execute_with_retry(func_403_rate_limit_then_success)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 2)
        # レート制限の場合は2倍の待機時間でリトライされる
        # base_delay * 2^1 * 2 = 1.0 * 2 * 2 = 4.0
        mock_sleep.assert_called_once_with(4.0)

    @patch("asyncio.sleep")
    async def test_retry_on_403_without_rate_limit_headers_once(self, mock_sleep):
        """レート制限ヘッダーなし403エラーは1回だけリトライすることをテスト"""
        call_count = 0

        async def func_403_no_headers_then_success():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # レート制限ヘッダーなし403エラーをシミュレート
                response = Mock()
                response.status_code = 403
                response.headers = {}
                error = HTTPError("403 Forbidden")
                error.response = response
                raise error
            return "success"

        result = await self.handler.execute_with_retry(func_403_no_headers_then_success)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 2)
        # レート制限ヘッダーがない場合は通常の待機時間
        # base_delay * 2^0 = 1.0 * 1 = 1.0
        mock_sleep.assert_called_once_with(1.0)

    @patch("asyncio.sleep")
    async def test_403_without_rate_limit_headers_fails_after_one_retry(self, mock_sleep):
        """レート制限ヘッダーなし403エラーは1回リトライ後に失敗することをテスト"""

        async def func_always_403_no_headers():
            # 常にレート制限ヘッダーなし403エラー
            response = Mock()
            response.status_code = 403
            response.headers = {}
            error = HTTPError("403 Forbidden")
            error.response = response
            raise error

        with self.assertRaises(HTTPError):
            await self.handler.execute_with_retry(func_always_403_no_headers)

        # 1回だけリトライ(計2回実行)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("asyncio.sleep")
    async def test_retry_on_429_with_double_delay(self, mock_sleep):
        """429エラー時に2倍の待機時間でリトライすることをテスト"""
        call_count = 0

        async def func_429_then_success():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 429エラーをシミュレート
                response = Mock()
                response.status_code = 429
                response.headers = {}
                error = HTTPError("429 Too Many Requests")
                error.response = response
                raise error
            return "success"

        result = await self.handler.execute_with_retry(func_429_then_success)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 2)
        # 2倍の待機時間でリトライされたことを確認
        mock_sleep.assert_called_once_with(4.0)

    @patch("asyncio.sleep")
    async def test_retry_on_500_with_normal_delay(self, mock_sleep):
        """500エラー時に通常の待機時間でリトライすることをテスト"""
        call_count = 0

        async def func_500_then_success():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 500エラーをシミュレート
                response = Mock()
                response.status_code = 500
                error = HTTPError("500 Internal Server Error")
                error.response = response
                raise error
            return "success"

        result = await self.handler.execute_with_retry(func_500_then_success)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 2)
        # 通常の待機時間でリトライされたことを確認
        # base_delay * 2^0 = 1.0 * 1 = 1.0
        mock_sleep.assert_called_once_with(1.0)

    @patch("asyncio.sleep")
    async def test_multiple_403_rate_limit_errors_exponential_backoff(self, mock_sleep):
        """複数回のレート制限403エラー時に指数バックオフでリトライすることをテスト"""
        call_count = 0

        async def func_403_rate_limit_twice_then_success():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # レート制限ヘッダー付き403エラーをシミュレート
                response = Mock()
                response.status_code = 403
                response.headers = {"Retry-After": "60"}
                error = HTTPError("403 Forbidden")
                error.response = response
                raise error
            return "success"

        result = await self.handler.execute_with_retry(func_403_rate_limit_twice_then_success)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)
        # レート制限の場合は2倍の遅延
        # 1回目: base_delay * 2^1 * 2 = 1.0 * 2 * 2 = 4.0
        # 2回目: base_delay * 2^2 * 2 = 1.0 * 4 * 2 = 8.0
        self.assertEqual(mock_sleep.call_count, 2)
        calls = [call[0][0] for call in mock_sleep.call_args_list]
        self.assertEqual(calls, [4.0, 8.0])

    @patch("asyncio.sleep")
    async def test_429_error_max_retries_exceeded(self, mock_sleep):
        """429エラーが最大リトライ回数を超えた場合のテスト"""

        async def func_always_429():
            # 常に429エラーをシミュレート
            response = Mock()
            response.status_code = 429
            response.headers = {}
            error = HTTPError("429 Too Many Requests")
            error.response = response
            raise error

        with self.assertRaises(HTTPError):
            await self.handler.execute_with_retry(func_always_429)

        # max_retries=3なので、3回リトライ(計4回実行)
        self.assertEqual(mock_sleep.call_count, 3)


if __name__ == "__main__":
    unittest.main()
