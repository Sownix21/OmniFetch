"""Instagram photos, full carousel lists, stories, and profile pictures."""
import http.cookiejar
import re
from urllib.parse import urlsplit
from .common import ProviderError, Result, host_is
from .http import HTTP

API = "https://i.instagram.com/api/v1"
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
UA = {"User-Agent": "Mozilla/5.0", "X-IG-App-ID": "936619743392459"}


def profile_name(url):
    if not host_is(url, "instagram.com"):
        return None
    path = urlsplit(url).path.strip("/")
    if path.lower() in {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "highlights"}:
        return None
    return path if re.fullmatch(r"[A-Za-z0-9._]{1,30}", path) else None


def media_id(url):
    path = urlsplit(url).path
    story = re.fullmatch(r"/stories/[^/]+/(\d+)/?", path)
    if story:
        return story[1]
    post = re.fullmatch(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)/?", path)
    if not post:
        raise ProviderError("Instagram URL is not a supported post or story")
    number = 0
    for character in post[1]:
        number = number * 64 + ALPHABET.index(character)
    return str(number)


def cookie_headers(path):
    if not path:
        return {}
    jar = http.cookiejar.MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True)
    except (OSError, http.cookiejar.LoadError):
        raise ProviderError("IG_COOKIES_FILE must be a readable Netscape cookie file") from None
    values = {cookie.name: cookie.value for cookie in jar
              if not cookie.is_expired() and (cookie.domain.lstrip(".") == "instagram.com"
                                              or cookie.domain.endswith(".instagram.com"))}
    return {"Cookie": "; ".join(f"{key}={value}" for key, value in values.items())} if values else {}


def choose(versions, height=None):
    versions = [v for v in versions if isinstance(v, dict) and isinstance(v.get("url"), str)]
    if not versions:
        raise ProviderError("Instagram returned an unavailable slide")
    if height:
        under = [v for v in versions if 0 < (v.get("height") or 0) <= height]
        if under:
            versions = under
        else:
            return min(versions, key=lambda v: v.get("height") or 100000)
    return max(versions, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))


async def download(url, action, root, settings, progress, convert):
    if action == "safe":
        raise ProviderError("Upload-safe video uses yt-dlp")
    async with HTTP(settings) as http:
        headers = {**UA, **cookie_headers(settings.instagram_cookies)}
        profile = profile_name(url)
        if profile:
            if action in {"mp3", "m4a"}:
                raise ProviderError("Profile pictures contain no audio")
            data = await http.json(API + "/users/web_profile_info/?username=" + profile, headers=headers)
            user = (data.get("data") or {}).get("user") or {}
            picture = user.get("profile_pic_url_hd") or user.get("profile_pic_url")
            if not picture:
                raise ProviderError("Instagram did not release this profile picture")
            file = await http.download(picture, root / f"{profile}.jpg")
            return Result([file], "Instagram")
        data = await http.json(API + "/media/" + media_id(url) + "/info/", headers=headers)
        items = data.get("items") or []
        if not items:
            raise ProviderError("Instagram returned no media; login cookies may be required")
        item = items[0]
        slides = item.get("carousel_media") or [item]
        count = item.get("carousel_media_count")
        if isinstance(count, int) and count > len(slides):
            raise ProviderError("Instagram returned an incomplete carousel")
        targets = []
        height = int(action[1:]) if re.fullmatch(r"h\d{3,4}", action) else None
        for slide in slides:
            video = slide.get("video_versions")
            image = (slide.get("image_versions2") or {}).get("candidates")
            if video:
                targets.append(("video", choose(video, height)["url"]))
            elif image:
                targets.append(("image", choose(image, height)["url"]))
            else:
                raise ProviderError("Instagram returned an unavailable slide")
        warnings = []
        if action in {"mp3", "m4a"}:
            targets = [t for t in targets if t[0] == "video"]
            if not targets:
                raise ProviderError("This Instagram post has no audio to extract")
            if len(targets) > 1:
                warnings.append("Audio was extracted from the first video slide.")
            targets = targets[:1]
        elif len(targets) > settings.max_items:
            warnings.append(f"Only the first {settings.max_items} slides were requested (configured item limit).")
            targets = targets[:settings.max_items]
        files = []
        for index, (kind, media_url) in enumerate(targets, 1):
            await progress(f"Instagram: downloading item {index}/{len(targets)}…")
            file = await http.download(media_url, root / f"slide_{index:02d}{'.mp4' if kind == 'video' else '.jpg'}")
            if action in {"mp3", "m4a"}:
                converted = file.with_suffix("." + action)
                await convert(file, converted, action)
                file.unlink()
                file = converted
            files.append(file)
        return Result(files, "Instagram", warnings)
