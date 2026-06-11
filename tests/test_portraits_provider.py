import json
import os
import unittest
import urllib.request

from ming_sim import portraits


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload


def _url(req) -> str:
    return req.full_url if isinstance(req, urllib.request.Request) else str(req)


class PortraitProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "NANO_BANANA_API_KEY",
                "NANO_BANANA_BASE_URL",
                "NANO_BANANA_FILE_BASE_URL",
                "NANO_BANANA_TEXT_ENDPOINT",
            )
        }
        self.old_urlopen = urllib.request.urlopen
        os.environ["NANO_BANANA_API_KEY"] = "test-key"
        os.environ["NANO_BANANA_BASE_URL"] = "https://api.302ai.cn"
        os.environ.pop("NANO_BANANA_FILE_BASE_URL", None)

    def tearDown(self) -> None:
        urllib.request.urlopen = self.old_urlopen
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_sync_output_file_url_is_rewritten_for_cn_base(self) -> None:
        png = b"\x89PNG\r\n\x1a\nsync"
        seen_urls = []

        def fake_urlopen(req, timeout=0):
            url = _url(req)
            seen_urls.append(url)
            if url == "https://api.302ai.cn/ws/api/v3/google/nano-banana-2/text-to-image":
                return _FakeResponse(json.dumps({
                    "code": 200,
                    "data": {
                        "status": "succeeded",
                        "outputs": ["https://file.302.ai/generated/output.png"],
                        "urls": {"get": "https://api.302.ai/ws/api/v3/predictions/job/result"},
                    },
                }).encode("utf-8"))
            if url == "https://file.302ai.cn/generated/output.png":
                return _FakeResponse(png)
            raise AssertionError(f"unexpected url {url}")

        urllib.request.urlopen = fake_urlopen

        self.assertEqual(portraits.nano_banana_generate_png("portrait", timeout=5), png)
        self.assertEqual(seen_urls[-1], "https://file.302ai.cn/generated/output.png")

    def test_async_result_url_is_polled_on_configured_base(self) -> None:
        png = b"\x89PNG\r\n\x1a\nasync"
        seen_urls = []

        def fake_urlopen(req, timeout=0):
            url = _url(req)
            seen_urls.append(url)
            if url == "https://api.302ai.cn/ws/api/v3/google/nano-banana-2/text-to-image":
                return _FakeResponse(json.dumps({
                    "code": 200,
                    "data": {
                        "status": "created",
                        "urls": {"get": "https://api.302.ai/ws/api/v3/predictions/job/result"},
                    },
                }).encode("utf-8"))
            if url == "https://api.302ai.cn/ws/api/v3/predictions/job/result":
                return _FakeResponse(json.dumps({
                    "code": 200,
                    "data": {
                        "status": "succeeded",
                        "outputs": ["https://file.302.ai/generated/final.png"],
                    },
                }).encode("utf-8"))
            if url == "https://file.302ai.cn/generated/final.png":
                return _FakeResponse(png)
            raise AssertionError(f"unexpected url {url}")

        urllib.request.urlopen = fake_urlopen

        self.assertEqual(portraits.nano_banana_generate_png("portrait", timeout=5), png)
        self.assertIn("https://api.302ai.cn/ws/api/v3/predictions/job/result", seen_urls)
        self.assertEqual(seen_urls[-1], "https://file.302ai.cn/generated/final.png")
