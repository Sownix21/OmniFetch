"""Cobalt API routing, adapted from the File From Link reference."""
import re
from pathlib import Path
from urllib.parse import urlsplit
from .common import ProviderError, Result, host_is
from .http import HTTP

HOSTS = ("instagram.com", "tiktok.com", "twitter.com", "x.com", "reddit.com",
         "facebook.com", "fb.watch", "vimeo.com", "vk.com", "pinterest.com",
         "dailymotion.com", "twitch.tv", "tumblr.com", "bilibili.com", "streamable.com",
         "snapchat.com", "ok.ru", "rutube.ru", "loom.com", "newgrounds.com",
         "soundcloud.com", "xiaohongshu.com", "xhslink.com")


def supported(url):
    return any(host_is(url, host) for host in HOSTS)


async def download(url, action, root, settings, progress):
    if action not in {"best", "mp3"} and not re.fullmatch(r"h\d{3,4}", action):
        raise ProviderError("This format uses yt-dlp")
    body = {"url": url, "filenameStyle": "classic", "downloadMode": "audio" if action == "mp3" else "auto"}
    if action == "mp3":
        body.update(audioFormat="mp3", audioBitrate="320")
    else:
        body["videoQuality"] = action[1:] if action.startswith("h") else "max"
    headers = {"Accept": "application/json"}
    if settings.cobalt_key:
        headers["Authorization"] = "Api-Key " + settings.cobalt_key
    async with HTTP(settings) as http:
        data = await http.json(settings.cobalt_url.rstrip("/") + "/", headers=headers,
                               body=body, trusted=True)
        if not isinstance(data, dict):
            raise ProviderError("Cobalt returned invalid metadata")
        status = data.get("status")
        if status == "picker" and action != "mp3":
            targets = data.get("picker") or []
        elif status in {"tunnel", "redirect"} and data.get("url"):
            targets = [{"url": data["url"], "type": "audio" if action == "mp3" else "video",
                        "filename": data.get("filename")}]
        else:
            raise ProviderError("Cobalt requires another download method")
        if not isinstance(targets, list) or not targets:
            raise ProviderError("Cobalt returned no media")
        warnings = []
        if len(targets) > settings.max_items:
            warnings.append(f"Only the first {settings.max_items} items were requested (configured item limit).")
        files = []
        for index, item in enumerate(targets[:settings.max_items], 1):
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                raise ProviderError("Cobalt returned an incomplete media list")
            extension = Path(item.get("filename") or urlsplit(item["url"]).path).suffix.lower()
            if extension not in {".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp3", ".m4a", ".ogg"}:
                extension = ".jpg" if item.get("type") == "photo" else ".mp3" if action == "mp3" else ".mp4"
            await progress(f"Cobalt: downloading item {index}/{min(len(targets), settings.max_items)}…")
            files.append(await http.download(item["url"], root / f"media_{index:02d}{extension}", cobalt_tunnel=True))
        return Result(files, "Cobalt", warnings)
