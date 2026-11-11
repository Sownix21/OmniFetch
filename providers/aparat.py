"""Aparat watch-page fallback adapted from SRC/app/downloader.py."""
import html
import re
from urllib.parse import urlsplit

from .common import ProviderError, Result
from .http import HTTP


async def download(url, action, root, settings, progress, convert):
    if action not in {"best", "mp3", "m4a"} and not re.fullmatch(r"h\d{3,4}", action):
        raise ProviderError("This format uses yt-dlp")
    async with HTTP(settings) as http:
        async with http.response(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise ProviderError("Aparat page exceeded its size limit")
                chunks.append(chunk)
        page = html.unescape(b"".join(chunks).decode("utf-8", errors="replace")).replace(r"\/", "/")
        urls = set(re.findall(r'https://[^\s\x00-\x20"\'<>\\]+?\.mp4(?:\?[^\s\x00-\x20"\'<>\\]*)?', page))
        choices = []
        for target in urls:
            match = re.search(r"(\d{3,4})p", urlsplit(target).path)
            choices.append((int(match[1]) if match else 0, target))
        if not choices:
            raise ProviderError("Aparat watch page returned no public MP4 sources")
        if action.startswith("h"):
            height = int(action[1:])
            choices = [item for item in choices if 0 < item[0] <= height]
            if not choices:
                raise ProviderError("Aparat page has no source at the selected resolution")
        target = max(choices)[1]
        await progress("Aparat: downloading video…")
        file = await http.download(target, root / "aparat.mp4")
        if action in {"mp3", "m4a"}:
            converted = file.with_suffix("." + action)
            await convert(file, converted, action)
            file.unlink()
            file = converted
        return Result([file], "Aparat watch page")
