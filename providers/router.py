"""Ordered provider fallbacks with isolated attempt directories."""
import asyncio
import logging
import shutil
from urllib.parse import urlsplit, parse_qs

import aiohttp
from . import aparat, cobalt, instagram
from .common import ProviderError, Result, check_files, host_is
from .http import validate_url

log = logging.getLogger(__name__)


class Router:
    def __init__(self, settings, ytdlp, convert):
        self.settings = settings
        self.ytdlp = ytdlp
        self.convert = convert

    def order(self, url, action):
        names = []
        is_ig = host_is(url, "instagram.com")
        if is_ig and (self.settings.instagram_cookies or instagram.profile_name(url)):
            names.append("instagram")
        if self.settings.cobalt_url and cobalt.supported(url) and action not in {"safe", "m4a"}:
            names.append("cobalt")
        if is_ig and "instagram" not in names and action != "safe":
            names.append("instagram")
        names.append("ytdlp")
        if host_is(url, "aparat.com") and action != "safe":
            names.append("aparat")
        youtube = host_is(url, "youtube.com") or host_is(url, "youtu.be")
        if youtube and self.settings.cobalt_url and action not in {"safe", "m4a"} and "list" not in parse_qs(urlsplit(url).query):
            names.append("cobalt")
        return names

    async def download(self, url, action, root, progress):
        validate_url(url)
        failures = []
        for provider in self.order(url, action):
            directory = root / provider
            directory.mkdir()
            await progress(f"Trying {provider}…")
            try:
                if provider == "cobalt":
                    result = await cobalt.download(url, action, directory, self.settings, progress)
                elif provider == "instagram":
                    result = await instagram.download(url, action, directory, self.settings, progress, self.convert)
                elif provider == "aparat":
                    result = await aparat.download(url, action, directory, self.settings, progress, self.convert)
                else:
                    result = await self.ytdlp(url, action, directory)
                    if not isinstance(result, Result):
                        result = Result(result, "yt-dlp")
                    if host_is(url, "instagram.com") and "/p/" in urlsplit(url).path:
                        result.warnings.append("Instagram API was unavailable; yt-dlp may return fewer carousel slides.")
                result.files = check_files(result.files, directory, self.settings)
                if len(result.files) > self.settings.max_items:
                    result.files = result.files[:self.settings.max_items]
                    result.warnings.append("Result limited to the configured maximum item count.")
                return result
            except asyncio.CancelledError:
                raise
            except (RuntimeError, OSError, ValueError, KeyError, TypeError, AttributeError, aiohttp.ClientError, TimeoutError) as exc:
                reason = str(exc)[:350] if isinstance(exc, ProviderError) else type(exc).__name__
                failures.append(f"{provider}: {reason}")
                log.info("Provider %s failed (%s); checking fallback", provider, type(exc).__name__)
                shutil.rmtree(directory)
        raise ProviderError("No provider succeeded. " + " | ".join(failures))
