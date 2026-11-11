import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from providers.common import Settings, ProviderError
from providers.http import HTTP, PublicResolver, validate_url

SETTINGS = Settings(10, 15, 10, 0, cobalt_url="http://127.0.0.1:9000")


def response(status=200, headers=None, chunks=()):
    async def stream(size):
        for chunk in chunks:
            yield chunk
    return NS(status=status, headers=headers or {}, close=Mock(),
              content=NS(iter_chunked=stream))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def test_literal_private_addresses_and_credentials_are_rejected(self):
        for url in ["http://127.0.0.1/a", "http://[::1]/a", "http://169.254.169.254/",
                    "http://localhost/a", "http://user:pass@example.com", "file:///etc/passwd"]:
            with self.subTest(url=url), self.assertRaises(ProviderError):
                validate_url(url)

    async def test_dns_resolution_rejects_mixed_public_private_answers(self):
        resolver = PublicResolver()
        try:
            with patch.object(aiohttp.resolver.ThreadedResolver, "resolve", AsyncMock(
                    return_value=[{"host": "8.8.8.8"}, {"host": "127.0.0.1"}])):
                with self.assertRaises(ProviderError):
                    await resolver.resolve("example.com")
        finally:
            await resolver.close()

    async def test_redirect_to_private_ip_is_rejected_before_second_request(self):
        http = HTTP(SETTINGS)
        redirect = response(302, {"Location": "http://127.0.0.1/private"})
        http.public = NS(request=AsyncMock(return_value=redirect))
        with self.assertRaises(ProviderError):
            async with http.response("https://example.com/file"):
                self.fail("private redirect followed")
        http.public.request.assert_awaited_once()
        redirect.close.assert_called_once()

    async def test_cross_origin_redirect_strips_credentials(self):
        http = HTTP(SETTINGS)
        http.public = NS(request=AsyncMock(side_effect=[
            response(302, {"Location": "https://cdn.example/file"}), response()]))
        async with http.response("https://example.com/file", headers={
            "Cookie": "secret", "Authorization": "token", "X-CSRFToken": "secret", "Accept": "image/*"}):
            pass
        self.assertEqual(http.public.request.await_args.kwargs["headers"], {"Accept": "image/*"})

    async def test_configured_cobalt_tunnel_is_narrowly_scoped(self):
        http = HTTP(SETTINGS)
        self.assertTrue(http.is_cobalt_tunnel("http://127.0.0.1:9000/tunnel?id=x"))
        for url in ["http://127.0.0.1:9000/admin", "http://127.0.0.1:9001/tunnel?id=x",
                    "http://127.0.0.2:9000/tunnel?id=x"]:
            self.assertFalse(http.is_cobalt_tunnel(url))

    async def test_unknown_length_overflow_removes_partial_file(self):
        http = HTTP(SETTINGS)
        http.public = NS(request=AsyncMock(return_value=response(chunks=[b"123456", b"789012"])))
        with TemporaryDirectory() as directory:
            dest = Path(directory) / "media.mp4"
            with self.assertRaisesRegex(ProviderError, "size limit"):
                await http.download("https://example.com/media", dest)
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_name("media.mp4.part").exists())

    async def test_aggregate_budget_applies_across_files(self):
        http = HTTP(replace(SETTINGS, max_job_bytes=10))
        http.public = NS(request=AsyncMock(return_value=response(chunks=[b"123456"])))
        with TemporaryDirectory() as directory:
            await http.download("https://example.com/a", Path(directory) / "a")
            with self.assertRaises(ProviderError):
                await http.download("https://example.com/b", Path(directory) / "b")

    async def test_disk_reserve_is_enforced(self):
        http = HTTP(replace(SETTINGS, reserve_bytes=100))
        http.public = NS(request=AsyncMock(return_value=response(chunks=[b"x"])))
        with TemporaryDirectory() as directory, patch("providers.http.shutil.disk_usage", return_value=NS(free=99)):
            with self.assertRaisesRegex(ProviderError, "disk"):
                await http.download("https://example.com/a", Path(directory) / "a")
