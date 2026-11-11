# SRC comparison and OmniFetch integration

This comparison is based on the supplied File From Link source in `SRC/`,
not a live reliability test of its deployed bot. Its README is older than
some of its code: its Compose file now includes an optional local Bot API.

## What the reference actually does

| Source | Reference implementation | OmniFetch selection |
|---|---|---|
| Instagram, TikTok, X and supported social sites | Cobalt first; fall back to site-specific handlers or yt-dlp | Optional Cobalt provider with automatic fallback |
| Instagram posts, carousels and stories | Instagram media-info API; session cookies when configured; download all slides | Dedicated provider for photos, mixed carousels, stories and profile pictures |
| YouTube | yt-dlp plus a separate PO-token service/plugin, optional cookies/proxy, alternate player clients; Cobalt fallback | yt-dlp, IPv4/browser retries and optional Cobalt for individual videos; token sidecar not installed |
| Aparat | Extract public MP4 URLs from the watch page | Bounded watch-page fallback after yt-dlp fails |
| Spotify/Apple Music | Read metadata and search for a matching recording on YouTube | Retain spotDL's Spotify playlist matching and multiple audio providers; Apple Music matching not ported |
| Google Play | gplaydl requires server linking or an API key; delivers base APK and required splits | Retain apkeep with direct Play credentials or explicitly selected mirror/F-Droid |
| GitHub | Dedicated API and asset handlers | Retain OmniFetch's repository browser, release labels and in-chat files |

SRC runs a FastAPI service, Redis/RQ workers and Docker services. OmniFetch
keeps its systemd installer, Linux manager, private allow-list and local
Telegram Bot API. Redis, S3 link delivery, the alternate serverless frontend,
and SRC's infrastructure credentials are not imported.

## Runtime flow

`bot.py` accepts the link and format, then sends ordinary media requests to
`providers/router.py`. Each provider returns completed files, its name and
any warnings. Failed attempts have separate directories and are cleaned
before the next provider runs. The bot uploads the resulting files and
reports partial delivery, rather than returning provider download links.

Instagram with configured cookies or a profile URL tries the direct handler
first. Other eligible social URLs try Cobalt first when configured. YouTube
uses yt-dlp first; playlists stay on yt-dlp to preserve playlist semantics.
Upload-safe and M4A requests use appropriate handlers rather than silently
substituting a Cobalt format. Cobalt local-processing responses currently
fall back to yt-dlp.

## Setup and limits

The normal installer and `sudo omnifetch update` install Python dependencies
and apkeep. Use `sudo omnifetch config` to set `COBALT_API_URL` to an instance
you operate or can access, and `COBALT_API_KEY` if it needs one. An empty URL
disables Cobalt. The installer does **not** deploy Cobalt or a PO-token server.
For example, `http://127.0.0.1:9000` is valid only if a Cobalt instance is
already listening there. A Docker-only hostname such as `cobalt` from SRC's
Compose network is not normally reachable from the host systemd bot.

`IG_COOKIES_FILE` and `X_COOKIES_FILE` override the general `COOKIES_FILE`
for those sites. `MAX_JOB_MB` bounds aggregate temporary storage per job;
file limits, disk reserves, item counts and timeouts remain enforced. HTTP
providers validate addresses at connection time and discard credentials on
cross-origin redirects. Third-party subprocess extractors perform their own
network requests; these checks are not a full network sandbox for yt-dlp.

Provider routing does not guarantee every URL works. Account restrictions,
site outages, deleted media and Telegram's file ceiling still apply. A FLAC
conversion cannot restore audio quality missing from the matched source.
Automated tests use fixture responses and a local Cobalt-shaped HTTP server;
they do not certify live website downloads or production Telegram delivery.
