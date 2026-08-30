#!/usr/bin/env python3
"""OmniFetch: an interactive Telegram downloader and link utility bot."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import requests
import yt_dlp
from dotenv import load_dotenv
from google_play_scraper import app as play_app
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from telegram import KeyboardButtonRequestUsers, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.ext import MessageHandler, filters

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DB_FILE, DOWNLOAD_DIR = BASE_DIR / "database.json", BASE_DIR / "downloads"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
COOKIES_FILE = os.getenv("COOKIES_FILE", "").strip()
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "49")))
MAX_PLAYLIST_ITEMS = max(1, int(os.getenv("MAX_PLAYLIST_ITEMS", "10")))
MAX_CONCURRENT_DOWNLOADS = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "20")))
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
LANGS = {"en", "fa", "ru", "zh"}
DB_LOCK = threading.RLock()
DOWNLOAD_SEMAPHORE: asyncio.Semaphore | None = None

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("omnifetch")

LANG = {
    "en": {
        "welcome": "🌌 <b>Welcome to OmniFetch</b>\n\nSend a media, GitHub, Spotify, or Google Play link. I’ll show the available actions.",
        "unauth": "🚫 You are not authorized. Ask the bot administrator for access.",
        "fetching": "🔎 Fetching details…", "wait": "⏳ Download queued…",
        "uploading": "📤 Download complete. Uploading…", "btn_lang": "🌍 Language",
        "btn_help": "✨ Features & Help", "btn_admin": "👑 Admin Panel",
        "help": "✨ <b>What I can do</b>\n\n• Download video or audio from sites supported by yt-dlp\n• Handle playlists (with an admin limit)\n• Download Spotify links through spotDL\n• Preview GitHub repositories, releases, and files\n• Show Google Play app details\n\nSome sites require cookies. Only download content you are legally allowed to access.",
        "lang_ok": "✅ Language updated.", "send_link": "🔗 Send a valid http(s) link, or use the menu below.",
    },
    "fa": {
        "welcome": "🌌 <b>به OmniFetch خوش آمدید</b>\n\nیک لینک رسانه، گیت‌هاب، اسپاتیفای یا گوگل‌پلی بفرستید.",
        "unauth": "🚫 شما مجاز نیستید. از مدیر ربات درخواست دسترسی کنید.",
        "fetching": "🔎 در حال دریافت اطلاعات…", "wait": "⏳ دانلود در صف قرار گرفت…",
        "uploading": "📤 دانلود تمام شد؛ در حال ارسال…", "btn_lang": "🌍 تغییر زبان",
        "btn_help": "✨ امکانات و راهنما", "btn_admin": "👑 پنل مدیریت",
        "help": "✨ <b>امکانات</b>\n\n• دانلود از سایت‌های پشتیبانی‌شده توسط yt-dlp\n• پلی‌لیست با محدودیت مدیر\n• دریافت آهنگ با spotDL\n• نمایش مخزن‌های گیت‌هاب\n• اطلاعات گوگل‌پلی\n\nفقط محتوایی را دانلود کنید که اجازه قانونی آن را دارید.",
        "lang_ok": "✅ زبان تغییر کرد.", "send_link": "🔗 یک لینک معتبر http(s) بفرستید یا از منو استفاده کنید.",
    },
    "ru": {
        "welcome": "🌌 <b>Добро пожаловать в OmniFetch</b>\n\nОтправьте ссылку на медиа, GitHub, Spotify или Google Play.",
        "unauth": "🚫 У вас нет доступа. Обратитесь к администратору.",
        "fetching": "🔎 Получаю информацию…", "wait": "⏳ Загрузка в очереди…",
        "uploading": "📤 Файл готов. Отправляю…", "btn_lang": "🌍 Язык",
        "btn_help": "✨ Возможности", "btn_admin": "👑 Панель администратора",
        "help": "✨ <b>Возможности</b>\n\n• Видео и аудио с сайтов yt-dlp\n• Плейлисты с лимитом\n• Spotify через spotDL\n• GitHub и Google Play\n\nСкачивайте только разрешённый контент.",
        "lang_ok": "✅ Язык обновлён.", "send_link": "🔗 Отправьте корректную http(s)-ссылку.",
    },
    "zh": {
        "welcome": "🌌 <b>欢迎使用 OmniFetch</b>\n\n请发送媒体、GitHub、Spotify 或 Google Play 链接。",
        "unauth": "🚫 您没有访问权限，请联系管理员。", "fetching": "🔎 正在获取信息…",
        "wait": "⏳ 下载已排队…", "uploading": "📤 下载完成，正在发送…",
        "btn_lang": "🌍 语言", "btn_help": "✨ 功能与帮助", "btn_admin": "👑 管理面板",
        "help": "✨ <b>功能</b>\n\n• 从 yt-dlp 支持的网站下载\n• 播放列表支持\n• spotDL、GitHub 和 Google Play\n\n请只下载您有权访问的内容。",
        "lang_ok": "✅ 语言已更新。", "send_link": "🔗 请发送有效的 http(s) 链接。",
    },
}


def new_db() -> dict[str, Any]:
    return {"users": {str(ADMIN_ID): {"lang": "en", "allowed": True}} if ADMIN_ID else {}}


def load_db() -> dict[str, Any]:
    with DB_LOCK:
        if not DB_FILE.exists(): return new_db()
        try:
            data = json.loads(DB_FILE.read_text(encoding="utf-8"))
            if not isinstance(data.get("users"), dict): raise ValueError("missing users object")
            return data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.error("Could not read database: %s", exc)
            return new_db()


def save_db(data: dict[str, Any]) -> None:
    with DB_LOCK:
        temp = DB_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, DB_FILE)


def text(user_id: int, key: str) -> str:
    lang = load_db()["users"].get(str(user_id), {}).get("lang", "en")
    return LANG[lang if lang in LANGS else "en"].get(key, LANG["en"].get(key, key))


def authorized(user_id: int) -> bool:
    return user_id == ADMIN_ID or bool(load_db()["users"].get(str(user_id), {}).get("allowed"))


def set_user(user_id: int, allowed: bool | None = None, lang: str | None = None) -> None:
    data = load_db(); record = data["users"].setdefault(str(user_id), {"lang": "en", "allowed": False})
    if allowed is not None: record["allowed"] = allowed
    if lang in LANGS: record["lang"] = lang
    save_db(data)


def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text(user_id, "btn_lang")), KeyboardButton(text(user_id, "btn_help"))]]
    if user_id == ADMIN_ID: rows.append([KeyboardButton(text(user_id, "btn_admin"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, input_field_placeholder="🔗 Paste a link…")


def admin_menu() -> ReplyKeyboardMarkup:
    request = KeyboardButtonRequestUsers(1, user_is_bot=False, max_quantity=1)
    return ReplyKeyboardMarkup([[KeyboardButton("➕ Add Telegram user", request_users=request)], [KeyboardButton("📋 List users"), KeyboardButton("🔙 Main menu")]], resize_keyboard=True)


def extract_url(value: str) -> str | None:
    match = URL_RE.search(value or "")
    return match.group(0).rstrip(".,;:!?)]}\"") if match else None


async def validate_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname: return False, "Only valid http(s) links are supported."
    if parsed.username or parsed.password: return False, "Links containing credentials are not allowed."
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".local"): return False, "Local network addresses are not allowed."
    try:
        results = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(item[4][0]).is_global for item in results): return False, "Private or reserved addresses are not allowed."
    except (socket.gaierror, ValueError, OSError): return False, "That host could not be resolved."
    return True, ""


def remember(context: ContextTypes.DEFAULT_TYPE, payload: dict[str, Any]) -> str:
    jobs = context.user_data.setdefault("jobs", {}); now = time.time()
    for key in list(jobs):
        if now - jobs[key].get("created", now) > 3600: jobs.pop(key, None)
    token = uuid.uuid4().hex[:10]; jobs[token] = {**payload, "created": now}; return token


def recall(context: ContextTypes.DEFAULT_TYPE, token: str) -> dict[str, Any] | None:
    job = context.user_data.get("jobs", {}).get(token)
    return job if job and time.time() - job.get("created", 0) <= 3600 else None


async def api_get(url: str, **kwargs: Any) -> requests.Response:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "OmniFetch"}
    if GITHUB_TOKEN: headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return await asyncio.to_thread(requests.get, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user: return
    uid = update.effective_user.id
    if not authorized(uid): await update.message.reply_text(text(uid, "unauth")); return
    set_user(uid, True)
    await update.message.reply_text(text(uid, "welcome"), parse_mode=ParseMode.HTML, reply_markup=main_menu(uid))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.effective_user and authorized(update.effective_user.id):
        await update.message.reply_text(text(update.effective_user.id, "help"), parse_mode=ParseMode.HTML)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.effective_user and authorized(update.effective_user.id):
        await update.message.reply_text(f"🟢 <b>OmniFetch is online</b>\n📦 Playlist limit: {MAX_PLAYLIST_ITEMS}\n📤 Upload limit: {MAX_UPLOAD_MB} MB\n⚙️ Download workers: {MAX_CONCURRENT_DOWNLOADS}", parse_mode=ParseMode.HTML)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.effective_user:
        await update.message.reply_text(f"🪪 Your Telegram user ID: <code>{update.effective_user.id}</code>", parse_mode=ParseMode.HTML)


async def change_access(update: Update, context: ContextTypes.DEFAULT_TYPE, allow: bool) -> None:
    if not update.message or not update.effective_user or update.effective_user.id != ADMIN_ID: return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"Usage: <code>/{'allow' if allow else 'revoke'} 123456789</code>", parse_mode=ParseMode.HTML); return
    target = int(context.args[0])
    if target == ADMIN_ID and not allow: await update.message.reply_text("⚠️ The administrator cannot be revoked."); return
    set_user(target, allow)
    await update.message.reply_text(f"{'✅ Authorized' if allow else '🚫 Revoked'} <code>{target}</code>.", parse_mode=ParseMode.HTML)


async def allow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await change_access(update, context, True)
async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await change_access(update, context, False)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or update.effective_user.id != ADMIN_ID: return
    lines = ["📋 <b>Users</b>"]
    for uid, data in load_db()["users"].items(): lines.append(f"{'✅' if data.get('allowed') else '🚫'} <code>{html.escape(uid)}</code> · {html.escape(data.get('lang', 'en'))}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def user_shared(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.message.users_shared and update.effective_user and update.effective_user.id == ADMIN_ID:
        uid = update.message.users_shared.users[0].user_id; set_user(uid, True)
        await update.message.reply_text(f"✅ User <code>{uid}</code> is authorized.", parse_mode=ParseMode.HTML)


async def message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.message.text: return
    uid, value = update.effective_user.id, update.message.text.strip()
    if not authorized(uid): await update.message.reply_text(text(uid, "unauth")); return
    if value.startswith("🌍"):
        keys = [[InlineKeyboardButton("🇺🇸 English", callback_data="lang:en"), InlineKeyboardButton("🇮🇷 فارسی", callback_data="lang:fa")], [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"), InlineKeyboardButton("🇨🇳 中文", callback_data="lang:zh")]]
        await update.message.reply_text("🌍 Choose your language:", reply_markup=InlineKeyboardMarkup(keys)); return
    if value.startswith(("✨", "ℹ️")): await help_command(update, context); return
    if value.startswith("👑") and uid == ADMIN_ID: await update.message.reply_text("👑 <b>Admin panel</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu()); return
    if value.startswith("🔙") and uid == ADMIN_ID: await update.message.reply_text("🏠 Main menu", reply_markup=main_menu(uid)); return
    if value.startswith("📋") and uid == ADMIN_ID: await users_command(update, context); return
    url = extract_url(value)
    if not url: await update.message.reply_text(text(uid, "send_link")); return
    valid, reason = await validate_url(url)
    if not valid: await update.message.reply_text(f"❌ {reason}"); return
    host, path = (urlparse(url).hostname or "").lower(), urlparse(url).path
    if host in {"github.com", "www.github.com"}: await github_menu(update, context, url)
    elif host == "open.spotify.com": await spotify_menu(update, context, url)
    elif host == "play.google.com" and "/store/apps/details" in path: await playstore(update, url)
    else: await media_menu(update, context, url)


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer(); set_user(query.from_user.id, lang=query.data.split(":")[1])
    await query.edit_message_text(text(query.from_user.id, "lang_ok"))
    await context.bot.send_message(query.from_user.id, "🏠 Main menu", reply_markup=main_menu(query.from_user.id))


async def github_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    assert update.message
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) < 2: await update.message.reply_text("❌ Send a GitHub repository link."); return
    owner, repo = parts[0], parts[1].removesuffix(".git")
    status = await update.message.reply_text(text(update.effective_user.id, "fetching"))
    try:
        response = await api_get(f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}")
        if not response.ok: await status.edit_text("❌ Repository not found or GitHub API limit reached."); return
        data = response.json(); token = remember(context, {"kind": "github", "owner": owner, "repo": repo}); branch = data.get("default_branch", "main")
        keys = [[InlineKeyboardButton("⬇️ Source ZIP", url=f"https://github.com/{owner}/{repo}/archive/refs/heads/{quote(branch)}.zip")], [InlineKeyboardButton("📖 README", callback_data=f"gh:{token}:readme"), InlineKeyboardButton("🏷 Releases", callback_data=f"gh:{token}:releases")], [InlineKeyboardButton("📂 Browse", callback_data=f"gh:{token}:browse"), InlineKeyboardButton("🌐 GitHub", url=data["html_url"])]]
        caption = f"📦 <b>{html.escape(data['full_name'])}</b>\n{html.escape(data.get('description') or 'No description')}\n\n⭐ {data.get('stargazers_count', 0):,} · 🍴 {data.get('forks_count', 0):,} · 🐞 {data.get('open_issues_count', 0):,}\n🌿 <code>{html.escape(branch)}</code>"
        await status.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keys))
    except requests.RequestException as exc:
        log.warning("GitHub failed: %s", exc); await status.edit_text("❌ GitHub is temporarily unavailable.")


async def github_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer("Loading…"); _, token, action = query.data.split(":", 2); job = recall(context, token)
    if not job: await query.message.reply_text("⌛ This menu expired. Send the link again."); return
    base = f"https://api.github.com/repos/{quote(job['owner'])}/{quote(job['repo'])}"
    try:
        if action == "readme":
            response = await api_get(f"{base}/readme"); link = response.json().get("html_url") if response.ok else None
            await query.message.reply_text(f"📖 {link}" if link else "❌ No README found.")
        elif action == "releases":
            response = await api_get(f"{base}/releases", params={"per_page": 5}); items = response.json() if response.ok else []
            lines = [f"🏷 <a href=\"{html.escape(item['html_url'])}\">{html.escape(item.get('name') or item['tag_name'])}</a>" for item in items]
            await query.message.reply_text("\n".join(lines) or "🏷 No published releases yet.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        else:
            response = await api_get(f"{base}/contents"); items = response.json() if response.ok else []
            if not isinstance(items, list): items = []
            lines = [f"{'📁' if item['type'] == 'dir' else '📄'} <a href=\"{html.escape(item['html_url'])}\">{html.escape(item['name'])}</a>" for item in items[:40]]
            await query.message.reply_text("\n".join(lines) or "❌ Contents unavailable.", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except (requests.RequestException, TelegramError) as exc:
        log.warning("GitHub callback failed: %s", exc); await query.message.reply_text("❌ Could not load that view.")


async def spotify_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    token = remember(context, {"kind": "spotify", "url": url})
    keys = [[InlineKeyboardButton("🎧 MP3 320k", callback_data=f"dl:{token}:sp3"), InlineKeyboardButton("💿 FLAC", callback_data=f"dl:{token}:sfl")]]
    await update.message.reply_text("🎵 <b>Spotify link detected</b>\nChoose a format:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keys))


async def media_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    token = remember(context, {"kind": "media", "url": url})
    keys = [[InlineKeyboardButton("🎬 Best video", callback_data=f"dl:{token}:best"), InlineKeyboardButton("📱 Upload-safe", callback_data=f"dl:{token}:safe")], [InlineKeyboardButton("🎧 MP3", callback_data=f"dl:{token}:mp3"), InlineKeyboardButton("🎼 M4A", callback_data=f"dl:{token}:m4a")]]
    await update.message.reply_text("🔗 <b>Media link detected</b>\nChoose a format. Playlists are supported up to the configured limit.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keys))


async def playstore(update: Update, url: str) -> None:
    app_id = parse_qs(urlparse(url).query).get("id", [""])[0]; status = await update.message.reply_text(text(update.effective_user.id, "fetching"))
    if not app_id: await status.edit_text("❌ This link has no application ID."); return
    try:
        data = await asyncio.to_thread(play_app, app_id)
        keys = [[InlineKeyboardButton("▶️ Google Play", url=url)], [InlineKeyboardButton("🔎 APKMirror", url=f"https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s={quote(app_id)}")]]
        caption = f"📱 <b>{html.escape(data['title'])}</b>\n🏢 {html.escape(data.get('developer', 'Unknown'))}\n⭐ {data.get('score') or 'N/A'} · ⬇️ {html.escape(data.get('installs', 'N/A'))}\n\n{html.escape((data.get('summary') or '')[:500])}"
        await status.delete(); await update.message.reply_photo(data["icon"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keys))
    except Exception as exc:
        log.warning("Play lookup failed: %s", exc); await status.edit_text("❌ App details could not be loaded.")


def ytdlp_options(action: str, output: Path) -> dict[str, Any]:
    limit = MAX_UPLOAD_MB * 1024 * 1024
    opts: dict[str, Any] = {"outtmpl": str(output / "%(playlist_index&{} - |)s%(title).180B [%(id)s].%(ext)s"), "quiet": True, "no_warnings": True, "playlistend": MAX_PLAYLIST_ITEMS, "windowsfilenames": True, "retries": 3, "fragment_retries": 3, "socket_timeout": REQUEST_TIMEOUT}
    if COOKIES_FILE: opts["cookiefile"] = COOKIES_FILE
    if action == "best": opts.update({"format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", "merge_output_format": "mp4"})
    elif action == "safe": opts.update({"format": f"best[filesize<={limit}]/best[filesize_approx<={limit}]/bestvideo[filesize<={int(limit*.82)}]+bestaudio[filesize<={int(limit*.18)}]/best", "merge_output_format": "mp4", "max_filesize": limit})
    elif action == "mp3": opts.update({"format": "bestaudio/best", "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}]})
    else: opts.update({"format": "bestaudio[ext=m4a]/bestaudio/best", "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]})
    return opts


async def run_spotdl(url: str, fmt: str, output: Path) -> None:
    executable = shutil.which("spotdl") or str(Path(sys.executable).parent / ("spotdl.exe" if os.name == "nt" else "spotdl"))
    if not Path(executable).exists(): raise RuntimeError("spotDL is not installed")
    process = await asyncio.create_subprocess_exec(executable, "download", url, "--format", fmt, "--output", str(output / "{artists} - {title}.{output-ext}"), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    stdout, _ = await process.communicate()
    if process.returncode: raise RuntimeError(stdout.decode(errors="replace")[-600:].strip() or "spotDL failed")


def files_in(path: Path) -> list[Path]:
    return sorted((item for item in path.rglob("*") if item.is_file() and item.suffix.lower() not in {".part", ".ytdl", ".temp"}), key=lambda item: item.name)


async def upload(context: ContextTypes.DEFAULT_TYPE, chat_id: int, path: Path) -> bool:
    size = path.stat().st_size / 1024 / 1024
    if size > MAX_UPLOAD_MB:
        await context.bot.send_message(chat_id, f"⚠️ <code>{html.escape(path.name)}</code> is {size:.1f} MB (limit: {MAX_UPLOAD_MB} MB).", parse_mode=ParseMode.HTML); return False
    with path.open("rb") as file:
        if path.suffix.lower() in {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}: await context.bot.send_audio(chat_id, file, read_timeout=120, write_timeout=120)
        elif path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
            try: await context.bot.send_video(chat_id, file, supports_streaming=True, read_timeout=120, write_timeout=120)
            except BadRequest: file.seek(0); await context.bot.send_document(chat_id, file, read_timeout=120, write_timeout=120)
        else: await context.bot.send_document(chat_id, file, read_timeout=120, write_timeout=120)
    return True


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    uid = query.from_user.id
    if not authorized(uid): await query.answer(text(uid, "unauth"), show_alert=True); return
    await query.answer(); _, token, action = query.data.split(":", 2); job = recall(context, token)
    if not job: await query.message.reply_text("⌛ This menu expired. Send the link again."); return
    status = await query.message.reply_text(text(uid, "wait")); DOWNLOAD_DIR.mkdir(exist_ok=True); work = Path(tempfile.mkdtemp(prefix=f"{uid}-", dir=DOWNLOAD_DIR))
    global DOWNLOAD_SEMAPHORE
    if DOWNLOAD_SEMAPHORE is None: DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    try:
        async with DOWNLOAD_SEMAPHORE:
            await status.edit_text("⬇️ Downloading and processing…")
            if job["kind"] == "spotify": await run_spotdl(job["url"], "flac" if action == "sfl" else "mp3", work)
            else: await asyncio.to_thread(yt_dlp.YoutubeDL(ytdlp_options(action, work)).download, [job["url"]])
        found = files_in(work)
        if not found: raise RuntimeError("The extractor produced no downloadable file")
        await status.edit_text(text(uid, "uploading")); sent = 0
        for path in found[:MAX_PLAYLIST_ITEMS]: sent += bool(await upload(context, query.message.chat_id, path))
        await status.edit_text(f"✅ Done — sent {sent} of {len(found)} file(s)." if sent else "⚠️ Every file exceeded the upload limit.")
    except (yt_dlp.utils.DownloadError, RuntimeError, OSError, TelegramError) as exc:
        log.warning("Download failed for %s: %s", uid, exc); detail = str(exc).splitlines()[-1][:350]
        hint = "\n\n💡 This site may require COOKIES_FILE." if any(word in detail.lower() for word in ("cookie", "sign in", "age")) else ""
        await status.edit_text(f"❌ Download failed.\n<code>{html.escape(detail)}</code>{hint}", parse_mode=ParseMode.HTML)
    finally: shutil.rmtree(work, ignore_errors=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled update error", exc_info=context.error)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([BotCommand("start", "Open OmniFetch"), BotCommand("help", "Features and help"), BotCommand("status", "Bot limits and status"), BotCommand("id", "Your Telegram ID")])


def main() -> None:
    if not BOT_TOKEN: raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and configure it.")
    if not ADMIN_ID: raise SystemExit("ADMIN_ID must be a numeric Telegram user ID.")
    if COOKIES_FILE and not Path(COOKIES_FILE).is_file(): log.warning("COOKIES_FILE does not exist: %s", COOKIES_FILE)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    for command, callback in (("start", start), ("help", help_command), ("status", status_command), ("id", id_command), ("allow", allow_command), ("revoke", revoke_command), ("users", users_command)): app.add_handler(CommandHandler(command, callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.USERS_SHARED, user_shared))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang:(en|fa|ru|zh)$"))
    app.add_handler(CallbackQueryHandler(github_callback, pattern=r"^gh:[a-f0-9]{10}:(readme|releases|browse)$"))
    app.add_handler(CallbackQueryHandler(download_callback, pattern=r"^dl:[a-f0-9]{10}:(best|safe|mp3|m4a|sp3|sfl)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message)); app.add_error_handler(error_handler)
    log.info("OmniFetch is running"); app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__": main()
