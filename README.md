# 🌌 OmniFetch

OmniFetch is a private, interactive Telegram bot for media downloads and useful link previews. It uses yt-dlp's broad extractor collection, spotDL for Spotify links, the GitHub API for repository cards, and Google Play metadata.

## Highlights

- 🎬 Best-video and upload-safe video choices
- 🎧 MP3, M4A, Spotify MP3, and FLAC choices
- 📎 Direct in-chat delivery for original image, media, archive, and document URLs
- 📦 Intact single-file delivery up to 1,900 MB through the official local Bot API
- 🧩 Multipart fallback for ordinary files when using Telegram's hosted API (Android packages are never split)
- 📚 Playlist support with a configurable item limit
- 🌐 Generic yt-dlp routing instead of a fragile hardcoded site list
- 🔞 Age-restricted/adult sites when supported by the installed yt-dlp extractor; cookies may be required
- 📦 GitHub repository details plus in-chat downloads for source ZIPs, READMEs, release assets, and browsed files
- 📱 Google Play details with in-chat APK delivery through a third-party APK provider
- 🌍 English, Persian, Russian, and Chinese menus
- 👑 Private allow-list with interactive and command-based admin controls
- 🔒 Private-network URL blocking, isolated jobs, concurrency limits, and automatic cleanup
- 🐧 systemd service plus a functional terminal management menu

Site support changes whenever websites change. The current yt-dlp project explicitly notes that the only reliable support test is trying a URL, so keep OmniFetch updated with `sudo omnifetch update`.

## One-line install (Ubuntu/Debian)

You need:

- a Telegram bot token from [@BotFather](https://t.me/BotFather);
- your numeric Telegram user ID; and
- an `api_id` and `api_hash` created at [my.telegram.org](https://my.telegram.org).

The API ID/hash identify the local Bot API application on your VPS. They do not restrict who can talk to your bot: OmniFetch's administrator allow-list controls that.

```bash
curl -fsSL https://raw.githubusercontent.com/Sownix21/OmniFetch/main/install.sh | sudo bash
```

The installer prompts safely through `/dev/tty`, installs a checksum-verified Deno runtime for current yt-dlp/YouTube support, builds Telegram's official local Bot API, performs the required one-time hosted-to-local migration, and starts both hardened systemd services. The first build can take several minutes.

For unattended installation:

```bash
curl -fsSL https://raw.githubusercontent.com/Sownix21/OmniFetch/main/install.sh | sudo BOT_TOKEN='123:token' ADMIN_ID='123456789' TELEGRAM_API_ID='1234567' TELEGRAM_API_HASH='0123456789abcdef0123456789abcdef' bash
```

Requirements: Ubuntu 22.04+ or Debian 12+, systemd, and Python 3.10+.

## Linux manager

Open the interactive menu:

```bash
sudo omnifetch
```

It controls the bot and private API together, shows both statuses and log streams, edits their separate configurations, updates either component, and runs an end-to-end health check. Direct subcommands are also supported:

```bash
sudo omnifetch status
sudo omnifetch logs
sudo omnifetch api-logs
sudo omnifetch update
sudo omnifetch update-api
sudo omnifetch config
sudo omnifetch api-config
sudo omnifetch health
sudo omnifetch uninstall
```

`sudo omnifetch uninstall` is also available as option 12 in the menu. It requires typing `UNINSTALL`, then removes both services, the bot database/download directory, private API credentials and data, the compiled API server/source, manager command, and service users. Shared VPS packages and runtimes are deliberately retained.

## Manual development setup

```bash
git clone https://github.com/Sownix21/OmniFetch.git
cd OmniFetch
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

FFmpeg and a current supported JavaScript runtime are required on `PATH`. Deno 2.3+ is recommended by yt-dlp.

## Configuration

| Variable | Required | Default | Purpose |
|---|---:|---:|---|
| `BOT_TOKEN` | yes | — | Token issued by BotFather |
| `ADMIN_ID` | yes | — | Numeric Telegram user ID of the owner |
| `BOT_API_URL` | no | empty | Root URL of a self-hosted Bot API server, e.g. `http://127.0.0.1:8081` |
| `GITHUB_TOKEN` | no | empty | Raises GitHub API limits for repository previews |
| `COOKIES_FILE` | no | empty | Netscape-format cookies file for sites requiring login/age verification |
| `MAX_UPLOAD_MB` | no | `49` hosted / `1900` local | Single-file ceiling, automatically clamped for the selected API mode |
| `MAX_DOWNLOAD_MB` | no | `500` hosted / `1900` installer | Maximum file downloaded; never lower than the upload ceiling |
| `MAX_PLAYLIST_ITEMS` | no | `10` | Maximum media items processed/sent per request |
| `MAX_CONCURRENT_DOWNLOADS` | no | `2` | Global download worker count |
| `REQUEST_TIMEOUT` | no | `20` | Network timeout in seconds |
| `TRANSFER_TIMEOUT` | no | `180` hosted / `3600` local | Upload/download operation timeout in seconds |
| `MIN_FREE_DISK_MB` | no | `1024` (`2048` installer) | Free space reserved while downloading |
| `LOG_LEVEL` | no | `INFO` | Python logging level |

Keep `.env` and cookie files private. A browser cookie export can grant account access; use a dedicated low-value account where possible and set restrictive file permissions. yt-dlp expects Netscape/Mozilla cookie-file format.

## Bot commands

- `/start` — open the main menu
- `/help` — supported features and usage
- `/status` — current limits and worker status
- `/id` — show your numeric Telegram ID
- `/allow USER_ID` — authorize a user (admin only)
- `/revoke USER_ID` — revoke a user (admin only)
- `/users` — list known users (admin only)

The admin panel also uses Telegram's interactive user picker when the client supports it.

## Notes and limitations

- OmniFetch does not bypass DRM, paywalls, or access controls. Downloads must comply with local law and the source site's terms.
- spotDL uses YouTube/YouTube Music as its audio source; selecting FLAC changes the container/encoding but cannot create quality absent from the source.
- Some websites require fresh cookies, a compatible JavaScript runtime, or extractor updates. Use `sudo omnifetch update` first when a previously working site breaks.
- The hosted Telegram Bot API accepts bot uploads only up to 50 MB. The official local server raises bot uploads to 2,000 MB; OmniFetch uses a conservative 1,900 MB ceiling. Files beyond Telegram's limit cannot be sent as one Telegram attachment.
- OmniFetch can split ordinary files when hosted mode is used, but never splits APK/APKS/XAPK packages because those pieces are not installable.
- Downloads consume VPS bandwidth and temporary disk space before Telegram accepts them. Keep more free space than `MAX_DOWNLOAD_MB + MIN_FREE_DISK_MB` and use `/status` or `sudo omnifetch health` to inspect capacity.

## Large-file architecture and security

The one-line installer configures large-file mode automatically:

- `telegram-bot-api` runs as its own unprivileged user and listens only on `127.0.0.1:8081`; it is not exposed to the internet.
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` live in `/etc/omnifetch-bot-api.env`, owned by root with mode `0600`. They are not placed in the bot's `.env` or source tree.
- OmniFetch runs under a different unprivileged user. Its `.env`, database, temporary downloads, and systemd processes use restrictive permissions and hardening.
- The bot accepts only users approved by `ADMIN_ID`, `/allow`, or the administrator's interactive user picker.
- User-supplied direct links and every HTTP redirect are rejected if they resolve to loopback, private, link-local, or reserved networks. `BOT_API_URL` itself must be a loopback HTTP address.
- Downloads have concurrency, maximum-size, timeout, free-disk reserve, stale-file cleanup, and safe-filename controls.

Telegram requires a one-time `logOut` call before a bot moves from the hosted API to a local server. The installer performs it once and records a per-bot migration marker. During that short migration, the bot cannot use Telegram's hosted endpoint. If installation fails afterward, inspect `sudo omnifetch api-logs` and rerun the installer with the same credentials.

The local API credentials are infrastructure credentials, not end-user login credentials. Other people can use this bot only after the administrator authorizes their Telegram IDs; they never receive the API ID/hash.

### Local API authentication troubleshooting

If installation reports a local authentication failure, inspect the service first:

```bash
sudo systemctl status telegram-bot-api --no-pager
sudo journalctl -u telegram-bot-api -n 100 --no-pager
```

Verify that the API ID is numeric and the hash is 32 hexadecimal characters without printing the secret:

```bash
sudo awk -F= '/TELEGRAM_API_ID/{print "API ID:",$2} /TELEGRAM_API_HASH/{print "API hash length:",length($2)}' /etc/omnifetch-bot-api.env
```

Correct credentials with `sudo omnifetch api-config`, or rerun the installer. The health check waits up to four minutes for first-time authorization and reports Telegram's response with the bot token redacted.

## Contributing

Run the checks before opening a pull request:

```bash
python -m py_compile bot.py
python -m unittest discover -s tests -v
bash -n install.sh omnifetch-manager.sh
```

Please do not commit tokens, `.env`, databases, downloaded media, or cookie files.
