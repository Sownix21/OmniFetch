"""Bounded HTTP transfers with connection-time public-address validation."""
import ipaddress
import json
import shutil
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
from .common import ProviderError


def validate_url(url, *, private=False):
    try:
        parsed = urlsplit(url)
        parsed.port
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            raise ValueError()
    except (ValueError, TypeError):
        raise ProviderError("Invalid HTTP URL") from None
    if private:
        return
    if host == "localhost" or host.endswith((".localhost", ".local")) or "%" in host:
        raise ProviderError("Private network addresses are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ProviderError("Private network addresses are not allowed")


class PublicResolver(aiohttp.resolver.ThreadedResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await super().resolve(host, port, family)
        if not records or any(not ipaddress.ip_address(r["host"]).is_global for r in records):
            raise ProviderError("Private network addresses are not allowed")
        return records


class HTTP:
    def __init__(self, settings):
        self.settings = settings
        self.used = 0

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=None, connect=self.settings.timeout,
                                        sock_read=self.settings.timeout)
        self.public = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=PublicResolver()),
            timeout=timeout, trust_env=False, cookie_jar=aiohttp.DummyCookieJar())
        # Only the administrator-configured Cobalt endpoint and its exact
        # /tunnel route may use the unrestricted connector.
        self.endpoint = aiohttp.ClientSession(timeout=timeout, trust_env=False,
                                              cookie_jar=aiohttp.DummyCookieJar())
        return self

    async def __aexit__(self, *args):
        await self.public.close()
        await self.endpoint.close()

    def is_cobalt_tunnel(self, url):
        if not self.settings.cobalt_url:
            return False
        target, endpoint = urlsplit(url), urlsplit(self.settings.cobalt_url)
        return ((target.scheme, target.netloc) == (endpoint.scheme, endpoint.netloc)
                and target.path == endpoint.path.rstrip("/") + "/tunnel")

    @asynccontextmanager
    async def response(self, url, *, headers=None, body=None, trusted=False):
        headers = dict(headers or {})
        for _ in range(6):
            validate_url(url, private=trusted)
            session = self.endpoint if trusted else self.public
            response = await session.request("POST" if body is not None else "GET", url,
                                             json=body, headers=headers, allow_redirects=False)
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if body is not None or not location:
                    raise ProviderError("Provider API returned an unexpected redirect")
                next_url = urljoin(url, location)
                old, new = urlsplit(url), urlsplit(next_url)
                if (old.scheme, old.netloc) != (new.scheme, new.netloc):
                    headers = {k: v for k, v in headers.items()
                               if k.lower() not in {"authorization", "cookie", "x-csrftoken"}}
                url, trusted = next_url, False
                continue
            try:
                if response.status >= 400:
                    raise ProviderError(f"Provider returned HTTP {response.status}")
                yield response
            finally:
                response.close()
            return
        raise ProviderError("Too many HTTP redirects")

    async def json(self, url, *, headers=None, body=None, trusted=False):
        async with self.response(url, headers=headers, body=body, trusted=trusted) as response:
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise ProviderError("Provider metadata exceeded its size limit")
                chunks.append(chunk)
            try:
                return json.loads(b"".join(chunks))
            except (ValueError, UnicodeDecodeError):
                raise ProviderError("Provider returned invalid JSON") from None

    async def download(self, url, destination, *, headers=None, cobalt_tunnel=False):
        trusted = cobalt_tunnel and self.is_cobalt_tunnel(url)
        destination = Path(destination)
        temporary = destination.with_name(destination.name + ".part")
        written = 0
        try:
            async with self.response(url, headers=headers, trusted=trusted) as response:
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > self.settings.max_file_bytes or self.used + declared > self.settings.max_job_bytes:
                    raise ProviderError("Download exceeds the configured size limit")
                if shutil.disk_usage(destination.parent).free < declared + self.settings.reserve_bytes:
                    raise ProviderError("Not enough free disk space for this file")
                with temporary.open("wb") as output:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        written += len(chunk)
                        self.used += len(chunk)
                        if written > self.settings.max_file_bytes or self.used > self.settings.max_job_bytes:
                            raise ProviderError("Download exceeds the configured size limit")
                        if shutil.disk_usage(destination.parent).free < len(chunk) + self.settings.reserve_bytes:
                            raise ProviderError("Disk reserve reached during download")
                        output.write(chunk)
            if not written:
                raise ProviderError("Provider returned an empty file")
            temporary.replace(destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)
