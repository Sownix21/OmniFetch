# 🌌 OmniFetch

OmniFetch is a private, interactive Telegram bot for media downloads and useful link previews. It uses yt-dlp's broad extractor collection, spotDL for Spotify links, the GitHub API for repository cards, and Google Play metadata.

## Highlights

- 🎬 Best-video and upload-safe video choices
- 🎧 MP3, M4A, Spotify MP3, and FLAC choices
- 📎 Direct in-chat delivery for original image, media, archive, and document URLs
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

You need a Telegram bot token from [@BotFather](https://t.me/BotFather) and your numeric Telegram ID (send `/id` to an existing helper bot, or use another trusted method).

```bash
curl -fsSL https://raw.githubusercontent.com/Sownix21/OmniFetch/main/install.sh | sudo bash
```

The installer prompts safely through `/dev/tty`, creates `/opt/omnifetch-bot`, installs FFmpeg, Node.js, and Python dependencies, creates an unprivileged `omnifetch` service user, and starts the systemd service.

For unattended installation:

```bash
curl -fsSL https://raw.githubusercontent.com/Sownix21/OmniFetch/main/install.sh | sudo BOT_TOKEN='123:token' ADMIN_ID='123456789' bash
```

Requirements: Ubuntu 22.04+ or Debian 12+, systemd, and Python 3.10+.

## Linux manager

Open the interactive menu:

```bash
sudo omnifetch
```

It can start, stop, restart, show status, follow logs, edit configuration, update the repository/dependencies, and run a health check. Direct subcommands are also supported:

```bash
sudo omnifetch status
sudo omnifetch logs
sudo omnifetch update
sudo omnifetch config
```

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

FFmpeg must be available on `PATH`.

## Configuration

| Variable | Required | Default | Purpose |
|---|---:|---:|---|
| `BOT_TOKEN` | yes | — | Token issued by BotFather |
| `ADMIN_ID` | yes | — | Numeric Telegram user ID of the owner |
| `GITHUB_TOKEN` | no | empty | Raises GitHub API limits for repository previews |
| `COOKIES_FILE` | no | empty | Netscape-format cookies file for sites requiring login/age verification |
| `MAX_UPLOAD_MB` | no | `49` | Cloud Bot API upload ceiling used by the bot |
| `MAX_PLAYLIST_ITEMS` | no | `10` | Maximum media items processed/sent per request |
| `MAX_CONCURRENT_DOWNLOADS` | no | `2` | Global download worker count |
| `REQUEST_TIMEOUT` | no | `20` | Network timeout in seconds |
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
- The default public Telegram Bot API has a relatively small bot upload limit. `MAX_UPLOAD_MB=49` stays below that limit; a local Bot API server requires additional deployment changes.

## Contributing

Run the checks before opening a pull request:

```bash
python -m py_compile bot.py
python -m unittest discover -s tests -v
bash -n install.sh omnifetch-manager.sh
```

Please do not commit tokens, `.env`, databases, downloaded media, or cookie files.
