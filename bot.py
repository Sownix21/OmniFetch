#!/usr/bin/env python3
"""OmniFetch: an interactive Telegram downloader and link utility bot."""

from __future__ import annotations

import asyncio
import base64
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
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests
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


def env_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        return int(value or default)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a whole number, not {value!r}") from exc


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = env_int("ADMIN_ID", 0)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
COOKIES_FILE = os.getenv("COOKIES_FILE", "").strip()
GOOGLE_PLAY_EMAIL = os.getenv("GOOGLE_PLAY_EMAIL", "").strip()
GOOGLE_PLAY_TOKEN = os.getenv("GOOGLE_PLAY_TOKEN", "").strip()
GOOGLE_PLAY_TOKEN_TYPE = os.getenv("GOOGLE_PLAY_TOKEN_TYPE", "aas").strip().lower()
GOOGLE_PLAY_ACCEPT_TOS = os.getenv("GOOGLE_PLAY_ACCEPT_TOS", "false").strip().lower() in {"1", "true", "yes"}
BOT_API_URL = os.getenv("BOT_API_URL", "").strip().rstrip("/")
LOCAL_BOT_API = bool(BOT_API_URL)
if LOCAL_BOT_API:
    try:
        parsed_bot_api = urlparse(BOT_API_URL)
    except ValueError as exc:
        raise SystemExit("BOT_API_URL is malformed") from exc
    bot_api_host = (parsed_bot_api.hostname or "").lower()
    try:
        loopback_host = bot_api_host == "localhost" or ipaddress.ip_address(bot_api_host).is_loopback
        parsed_bot_api.port
    except ValueError:
        loopback_host = False
    if parsed_bot_api.scheme != "http" or not loopback_host or parsed_bot_api.path not in {"", "/"} or parsed_bot_api.username or parsed_bot_api.password or parsed_bot_api.query or parsed_bot_api.fragment:
        raise SystemExit("BOT_API_URL must be a private loopback HTTP endpoint such as http://127.0.0.1:18081")
upload_setting = os.getenv("MAX_UPLOAD_MB", "").strip()
if LOCAL_BOT_API and upload_setting in {"", "49"}:
    upload_setting = "1900"
try:
    upload_limit = int(upload_setting or "49")
except ValueError as exc:
    raise SystemExit(f"MAX_UPLOAD_MB must be a whole number, not {upload_setting!r}") from exc
MAX_UPLOAD_MB = min(1990 if LOCAL_BOT_API else 49, max(1, upload_limit))
MAX_DOWNLOAD_MB = max(MAX_UPLOAD_MB, env_int("MAX_DOWNLOAD_MB", 500))
MAX_PLAYLIST_ITEMS = max(1, env_int("MAX_PLAYLIST_ITEMS", 10))
MAX_CONCURRENT_DOWNLOADS = max(1, env_int("MAX_CONCURRENT_DOWNLOADS", 2))
REQUEST_TIMEOUT = max(5, env_int("REQUEST_TIMEOUT", 20))
TRANSFER_TIMEOUT = max(120, env_int("TRANSFER_TIMEOUT", 3600 if LOCAL_BOT_API else 180))
MIN_FREE_DISK_MB = max(256, env_int("MIN_FREE_DISK_MB", 1024))
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
ANDROID_APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
LANGS = {"en", "fa", "ru", "zh"}
DB_LOCK = threading.RLock()
DOWNLOAD_SEMAPHORE: asyncio.Semaphore | None = None
ACTIVE_DOWNLOADS: set[int] = set()
YTDLP_FILE_MARKER = "__OMNIFETCH_FILE__"

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("omnifetch")
logging.getLogger("httpx").setLevel(logging.WARNING)  # Avoid leaking token-bearing Bot API URLs in verbose logs.

LANG = {
    "en": {
        "welcome": "🌌 <b>Welcome to OmniFetch</b>\n\nSend a media, GitHub, Spotify, or Google Play link. I’ll show the available actions.",
        "unauth": "🚫 You are not authorized. Ask the bot administrator for access.",
        "fetching": "🔎 Fetching details…", "wait": "⏳ Download queued…",
        "uploading": "📤 Download complete. Uploading…", "btn_lang": "🌍 Language",
        "btn_help": "✨ Features & Help", "btn_status": "📊 Bot Status", "btn_admin": "👑 Admin Panel",
        "help": "✨ <b>What I can do</b>\n\n• Send video/audio from YouTube, TikTok, Instagram, X, adult sites, and other yt-dlp extractors\n• Process supported playlists and Spotify tracks/playlists\n• Upload GitHub source, releases, READMEs, and repository files\n• Show Google Play details and send available APKs\n• Deliver direct file links inside this chat\n\nSome sites require cookies. DRM and inaccessible/private content cannot be bypassed. Only download content you are legally allowed to access.",
        "lang_ok": "✅ Language updated.", "send_link": "🔗 Send a valid http(s) link, or use the menu below.",
    },
    "fa": {
        "welcome": "🌌 <b>به OmniFetch خوش آمدید</b>\n\nیک لینک رسانه، گیت‌هاب، اسپاتیفای یا گوگل‌پلی بفرستید.",
        "unauth": "🚫 شما مجاز نیستید. از مدیر ربات درخواست دسترسی کنید.",
        "fetching": "🔎 در حال دریافت اطلاعات…", "wait": "⏳ دانلود در صف قرار گرفت…",
        "uploading": "📤 دانلود تمام شد؛ در حال ارسال…", "btn_lang": "🌍 تغییر زبان",
        "btn_help": "✨ امکانات و راهنما", "btn_status": "📊 وضعیت ربات", "btn_admin": "👑 پنل مدیریت",
        "help": "✨ <b>امکانات</b>\n\n• دانلود از سایت‌های پشتیبانی‌شده توسط yt-dlp\n• پلی‌لیست با محدودیت مدیر\n• دریافت آهنگ با spotDL\n• نمایش مخزن‌های گیت‌هاب\n• اطلاعات گوگل‌پلی\n\nفقط محتوایی را دانلود کنید که اجازه قانونی آن را دارید.",
        "lang_ok": "✅ زبان تغییر کرد.", "send_link": "🔗 یک لینک معتبر http(s) بفرستید یا از منو استفاده کنید.",
    },
    "ru": {
        "welcome": "🌌 <b>Добро пожаловать в OmniFetch</b>\n\nОтправьте ссылку на медиа, GitHub, Spotify или Google Play.",
        "unauth": "🚫 У вас нет доступа. Обратитесь к администратору.",
        "fetching": "🔎 Получаю информацию…", "wait": "⏳ Загрузка в очереди…",
        "uploading": "📤 Файл готов. Отправляю…", "btn_lang": "🌍 Язык",
        "btn_help": "✨ Возможности", "btn_status": "📊 Статус бота", "btn_admin": "👑 Панель администратора",
        "help": "✨ <b>Возможности</b>\n\n• Видео и аудио с сайтов yt-dlp\n• Плейлисты с лимитом\n• Spotify через spotDL\n• GitHub и Google Play\n\nСкачивайте только разрешённый контент.",
        "lang_ok": "✅ Язык обновлён.", "send_link": "🔗 Отправьте корректную http(s)-ссылку.",
    },
    "zh": {
        "welcome": "🌌 <b>欢迎使用 OmniFetch</b>\n\n请发送媒体、GitHub、Spotify 或 Google Play 链接。",
        "unauth": "🚫 您没有访问权限，请联系管理员。", "fetching": "🔎 正在获取信息…",
        "wait": "⏳ 下载已排队…", "uploading": "📤 下载完成，正在发送…",
        "btn_lang": "🌍 语言", "btn_help": "✨ 功能与帮助", "btn_status": "📊 机器人状态", "btn_admin": "👑 管理面板",
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
    rows = [[KeyboardButton(text(user_id, "btn_lang")), KeyboardButton(text(user_id, "btn_status"))], [KeyboardButton(text(user_id, "btn_help"))]]
    if user_id == ADMIN_ID: rows.append([KeyboardButton(text(user_id, "btn_admin"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, input_field_placeholder="🔗 Paste a link…")


def admin_menu() -> ReplyKeyboardMarkup:
    request = KeyboardButtonRequestUsers(1, user_is_bot=False, max_quantity=1)
    return ReplyKeyboardMarkup([[KeyboardButton("➕ Add Telegram user", request_users=request)], [KeyboardButton("📋 List users"), KeyboardButton("🔙 Main menu")]], resize_keyboard=True)


def extract_url(value: str) -> str | None:
    match = URL_RE.search(value or "")
    return match.group(0).rstrip(".,;:!?)]}\"") if match else None


def validate_public_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname: return False, "Only valid http(s) links are supported."
    if parsed.username or parsed.password: return False, "Links containing credentials are not allowed."
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".local"): return False, "Local network addresses are not allowed."
    try:
        results = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(item[4][0]).is_global for item in results): return False, "Private or reserved addresses are not allowed."
    except (socket.gaierror, ValueError, OSError): return False, "That host could not be resolved."
    return True, ""


async def validate_url(url: str) -> tuple[bool, str]:
    return await asyncio.to_thread(validate_public_url, url)


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


def safe_filename(value: str, fallback: str = "download") -> str:
    name = Path(value.replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return (name or fallback)[:180]


def download_http_file(url: str, destination: Path, filename: str) -> Path:
    """Stream an approved URL to disk while enforcing the download ceiling."""
    limit = MAX_DOWNLOAD_MB * 1024 * 1024
    path = destination / safe_filename(filename)
    current_url = url
    response: requests.Response | None = None
    for _ in range(6):
        valid, reason = validate_public_url(current_url)
        if not valid:
            raise RuntimeError(f"Blocked unsafe download URL: {reason}")
        headers = {"Accept": "application/octet-stream", "User-Agent": "OmniFetch"}
        host = (urlparse(current_url).hostname or "").lower()
        if GITHUB_TOKEN and (host == "github.com" or host.endswith(".github.com") or host.endswith(".githubusercontent.com")):
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        response = requests.get(current_url, headers=headers, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=False)
        if not response.is_redirect:
            break
        location = response.headers.get("Location")
        response.close()
        if not location:
            raise RuntimeError("Download redirect did not include a destination")
        current_url = urljoin(current_url, location)
    else:
        raise RuntimeError("Download exceeded the redirect limit")
    assert response is not None
    with response:
        response.raise_for_status()
        declared = int(response.headers.get("Content-Length", "0") or 0)
        if declared > limit:
            raise RuntimeError(f"File is {declared / 1024 / 1024:.1f} MB; bot download ceiling is {MAX_DOWNLOAD_MB} MB")
        required = declared or limit
        free = shutil.disk_usage(destination).free
        reserve = MIN_FREE_DISK_MB * 1024 * 1024
        if free < required + reserve:
            raise RuntimeError(f"Not enough VPS disk space; {MIN_FREE_DISK_MB} MB must remain free")
        written = 0
        with path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    raise RuntimeError(f"File exceeds the {MAX_DOWNLOAD_MB} MB bot download ceiling")
                output.write(chunk)
    return path


def github_item(job: dict[str, Any], payload: dict[str, Any]) -> str:
    key = uuid.uuid4().hex[:8]
    job.setdefault("github_items", {})[key] = payload
    return key


def ensure_download_capacity() -> None:
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    free = shutil.disk_usage(DOWNLOAD_DIR).free
    required = (MAX_DOWNLOAD_MB + MIN_FREE_DISK_MB) * 1024 * 1024
    if free < required:
        raise RuntimeError(f"VPS needs at least {MAX_DOWNLOAD_MB + MIN_FREE_DISK_MB} MB free before starting this download")


def cleanup_stale_downloads(max_age_seconds: int = 24 * 3600) -> None:
    if not DOWNLOAD_DIR.exists():
        return
    cutoff = time.time() - max_age_seconds
    for item in DOWNLOAD_DIR.iterdir():
        try:
            if item.stat().st_mtime < cutoff:
                if item.is_dir(): shutil.rmtree(item, ignore_errors=True)
                else: item.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not clean stale download %s: %s", item, exc)


def download_semaphore() -> asyncio.Semaphore:
    global DOWNLOAD_SEMAPHORE
    if DOWNLOAD_SEMAPHORE is None:
        DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    return DOWNLOAD_SEMAPHORE


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
        mode = "Local Bot API" if LOCAL_BOT_API else "Hosted Bot API"
        free_mb = shutil.disk_usage(BASE_DIR).free / 1024 / 1024
        await update.message.reply_text(f"🟢 <b>OmniFetch is online</b>\n🔌 {mode}\n📦 Playlist limit: {MAX_PLAYLIST_ITEMS}\n📤 Single-file limit: {MAX_UPLOAD_MB} MB\n⬇️ Download ceiling: {MAX_DOWNLOAD_MB} MB\n💽 Free disk: {free_mb:,.0f} MB\n⚙️ Download workers: {MAX_CONCURRENT_DOWNLOADS}", parse_mode=ParseMode.HTML)


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
    if value.startswith("📊"): await status_command(update, context); return
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
    elif host == "play.google.com" and "/store/apps/details" in path: await playstore(update, context, url)
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
        data = response.json(); branch = data.get("default_branch", "main")
        token = remember(context, {"kind": "github", "owner": owner, "repo": repo, "branch": branch})
        keys = [[InlineKeyboardButton("⬇️ Download Source ZIP", callback_data=f"gh:{token}:source")], [InlineKeyboardButton("📖 Send README", callback_data=f"gh:{token}:readme"), InlineKeyboardButton("🏷 Release Downloads", callback_data=f"gh:{token}:releases")], [InlineKeyboardButton("📂 Browse & Download Files", callback_data=f"gh:{token}:browse")]]
        caption = f"📦 <b>{html.escape(data['full_name'])}</b>\n{html.escape(data.get('description') or 'No description')}\n\n⭐ {data.get('stargazers_count', 0):,} · 🍴 {data.get('forks_count', 0):,} · 🐞 {data.get('open_issues_count', 0):,}\n🌿 <code>{html.escape(branch)}</code>"
        await status.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keys))
    except requests.RequestException as exc:
        log.warning("GitHub failed: %s", exc); await status.edit_text("❌ GitHub is temporarily unavailable.")


async def github_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    if not authorized(query.from_user.id):
        await query.answer(text(query.from_user.id, "unauth"), show_alert=True); return
    await query.answer("Loading…"); parts = query.data.split(":"); token, action = parts[1], parts[2]; job = recall(context, token)
    if not job: await query.message.reply_text("⌛ This menu expired. Send the link again."); return
    base = f"https://api.github.com/repos/{quote(job['owner'])}/{quote(job['repo'])}"
    try:
        if action == "source":
            url = f"https://github.com/{job['owner']}/{job['repo']}/archive/refs/heads/{quote(job['branch'])}.zip"
            source_name = f"{job['repo']}-{job['branch'].replace('/', '-')}.zip"
            await send_remote_document(query.message, context, url, source_name)
        elif action == "readme":
            response = await api_get(f"{base}/readme")
            if not response.ok:
                await query.message.reply_text("❌ No README found."); return
            data = response.json(); content = base64.b64decode(data.get("content", ""), validate=False)
            DOWNLOAD_DIR.mkdir(exist_ok=True); work = Path(tempfile.mkdtemp(prefix="github-readme-", dir=DOWNLOAD_DIR))
            try:
                path = work / safe_filename(data.get("name", "README.md"), "README.md"); path.write_bytes(content)
                with path.open("rb") as document:
                    await context.bot.send_document(query.message.chat_id, document, caption=f"📖 {job['owner']}/{job['repo']} README")
            finally:
                shutil.rmtree(work, ignore_errors=True)
        elif action == "releases":
            response = await api_get(f"{base}/releases", params={"per_page": 10}); releases = response.json() if response.ok else []
            if not releases:
                await query.message.reply_text("🏷 No published releases. Use “Download Source ZIP” on the repository card."); return
            buttons = []
            stable_id = next((release.get("id") for release in releases if not release.get("draft") and not release.get("prerelease")), None)
            newest_id = next((release.get("id") for release in releases if not release.get("draft")), None)
            for release in releases:
                release = dict(release)
                if release.get("draft"):
                    status_label = "📝 Draft"
                elif release.get("prerelease"):
                    status_label = "🧪 Latest pre-release" if release.get("id") == newest_id else "🧪 Pre-release"
                elif release.get("id") == stable_id:
                    status_label = "✅ Latest stable"
                else:
                    status_label = "🏷 Stable"
                release["omnifetch_status"] = status_label
                key = github_item(job, {"type": "release", "release": release})
                label = safe_filename(release.get("name") or release.get("tag_name") or "Release")
                buttons.append([InlineKeyboardButton(f"{status_label} · {label[:34]}", callback_data=f"gh:{token}:release:{key}")])
            await query.message.reply_text("🏷 <b>Choose a release</b>\nAssets and source archives will be uploaded here.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        elif action == "release":
            item = job.get("github_items", {}).get(parts[3]) if len(parts) > 3 else None
            if not item or item.get("type") != "release":
                await query.message.reply_text("⌛ This release menu expired."); return
            release = item["release"]; buttons = []
            for asset in release.get("assets", [])[:30]:
                key = github_item(job, {"type": "download", "url": asset["browser_download_url"], "name": asset["name"]})
                size = asset.get("size", 0) / 1024 / 1024
                buttons.append([InlineKeyboardButton(f"📦 {asset['name'][:38]} · {size:.1f} MB", callback_data=f"gh:{token}:download:{key}")])
            source_name = f"{job['repo']}-{release['tag_name'].replace('/', '-')}.zip"
            key = github_item(job, {"type": "download", "url": release["zipball_url"], "name": source_name})
            buttons.append([InlineKeyboardButton("🗜 Source code (ZIP)", callback_data=f"gh:{token}:download:{key}")])
            status_label = release.get("omnifetch_status", "🏷 Release")
            published = (release.get("published_at") or release.get("created_at") or "")[:10]
            await query.message.reply_text(
                f"{html.escape(status_label)}\n<b>{html.escape(release.get('name') or release['tag_name'])}</b>\n"
                f"🏷 <code>{html.escape(release.get('tag_name', ''))}</code>"
                f"{f' · 📅 {published}' if published else ''}\nChoose a file to receive in Telegram:",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons),
            )
        elif action == "download":
            item = job.get("github_items", {}).get(parts[3]) if len(parts) > 3 else None
            if not item or item.get("type") != "download":
                await query.message.reply_text("⌛ This download expired."); return
            await send_remote_document(query.message, context, item["url"], item["name"])
        elif action == "browse":
            await send_github_browser(query.message, job, token, "")
        elif action == "path":
            item = job.get("github_items", {}).get(parts[3]) if len(parts) > 3 else None
            if not item or item.get("type") != "path":
                await query.message.reply_text("⌛ This folder menu expired."); return
            await send_github_browser(query.message, job, token, item["path"])
    except (requests.RequestException, TelegramError, OSError, ValueError) as exc:
        log.warning("GitHub callback failed: %s", exc); await query.message.reply_text("❌ Could not load that view.")


def split_large_file(path: Path, chunk_size: int) -> list[Path]:
    parts: list[Path] = []
    with path.open("rb") as source:
        index = 1
        while chunk := source.read(chunk_size):
            part = path.with_name(f"{path.name}.part{index:03d}")
            part.write_bytes(chunk); parts.append(part); index += 1
    return parts


async def send_large_file_parts(context: ContextTypes.DEFAULT_TYPE, chat_id: int, path: Path) -> None:
    chunk_size = MAX_UPLOAD_MB * 1024 * 1024
    parts = await asyncio.to_thread(split_large_file, path, chunk_size)
    names = [part.name for part in parts]
    linux_command = f"cat '{path.name}.part'* > '{path.name}'"
    windows_sources = "+".join(f'"{name}"' for name in names)
    windows_command = f'copy /b {windows_sources} "{path.name}"'
    await context.bot.send_message(
        chat_id,
        f"🧩 <b>Large file split into {len(parts)} parts</b>\nOriginal: <code>{html.escape(path.name)}</code>\n\n"
        f"After downloading every part, rebuild it:\n🐧 <code>{html.escape(linux_command)}</code>\n"
        f"🪟 <code>{html.escape(windows_command)}</code>",
        parse_mode=ParseMode.HTML,
    )
    for index, part in enumerate(parts, 1):
        with part.open("rb") as document:
            await context.bot.send_document(chat_id, document, caption=f"🧩 {path.name} · part {index}/{len(parts)}", read_timeout=TRANSFER_TIMEOUT, write_timeout=TRANSFER_TIMEOUT)


async def send_remote_document(message: Any, context: ContextTypes.DEFAULT_TYPE, url: str, filename: str, source: str = "GitHub") -> None:
    filename = safe_filename(filename)
    active_key = message.chat_id
    if active_key in ACTIVE_DOWNLOADS:
        await message.reply_text("⏳ This chat already has an active download. Please wait for it to finish."); return
    ACTIVE_DOWNLOADS.add(active_key)
    status = None
    work: Path | None = None
    try:
        status = await message.reply_text(f"⬇️ Downloading <code>{html.escape(filename)}</code> from {html.escape(source)}…", parse_mode=ParseMode.HTML)
        DOWNLOAD_DIR.mkdir(exist_ok=True); work = Path(tempfile.mkdtemp(prefix="remote-", dir=DOWNLOAD_DIR))
        async with download_semaphore():
            path = await asyncio.to_thread(download_http_file, url, work, filename)
        await status.edit_text("📤 Uploading to Telegram…")
        is_installable_package = path.suffix.lower() in {".apk", ".apks", ".xapk"}
        if path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024 and is_installable_package:
            api_description = "configured local Bot API" if LOCAL_BOT_API else "Telegram hosted Bot API"
            await status.edit_text(
                f"⚠️ <b>This APK must remain one file.</b>\nIt is {path.stat().st_size / 1024 / 1024:.1f} MB, but the {api_description} single-file limit is configured as {MAX_UPLOAD_MB} MB.\n\nConfigure <code>BOT_API_URL</code> and a larger <code>MAX_UPLOAD_MB</code> to send it intact.",
                parse_mode=ParseMode.HTML,
            ); return
        if path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
            await send_large_file_parts(context, message.chat_id, path)
        else:
            with path.open("rb") as document:
                await context.bot.send_document(message.chat_id, document, caption=f"✅ {filename}", read_timeout=TRANSFER_TIMEOUT, write_timeout=TRANSFER_TIMEOUT)
        try: await status.delete()
        except TelegramError: pass
    except (requests.RequestException, RuntimeError, OSError, TelegramError, ValueError) as exc:
        log.warning("Remote file delivery failed: %s", exc)
        if status:
            try: await status.edit_text(f"❌ Could not send this file.\n<code>{html.escape(str(exc)[:350])}</code>", parse_mode=ParseMode.HTML)
            except TelegramError: pass
    finally:
        if work: shutil.rmtree(work, ignore_errors=True)
        ACTIVE_DOWNLOADS.discard(active_key)


async def send_github_browser(message: Any, job: dict[str, Any], token: str, path: str) -> None:
    base = f"https://api.github.com/repos/{quote(job['owner'])}/{quote(job['repo'])}/contents"
    response = await api_get(f"{base}/{quote(path, safe='/')}" if path else base)
    items = response.json() if response.ok else []
    if not isinstance(items, list):
        await message.reply_text("❌ This folder could not be loaded."); return
    buttons = []
    if path:
        parent = "/".join(path.split("/")[:-1]); key = github_item(job, {"type": "path", "path": parent})
        buttons.append([InlineKeyboardButton("⬆️ Parent folder", callback_data=f"gh:{token}:path:{key}")])
    for item in sorted(items, key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))[:40]:
        if item["type"] == "dir":
            key = github_item(job, {"type": "path", "path": item["path"]}); label = f"📁 {item['name'][:48]}"; action = "path"
        else:
            key = github_item(job, {"type": "download", "url": item.get("download_url") or item["url"], "name": item["name"]}); label = f"📄 {item['name'][:48]}"; action = "download"
        buttons.append([InlineKeyboardButton(label, callback_data=f"gh:{token}:{action}:{key}")])
    if not buttons:
        await message.reply_text("📂 This repository folder is empty."); return
    location = f"/{path}" if path else "/"
    await message.reply_text(f"📂 <b>{html.escape(job['owner'])}/{html.escape(job['repo'])}</b> <code>{html.escape(location)}</code>\nTap a file to receive it here:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def spotify_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    token = remember(context, {"kind": "spotify", "url": url})
    keys = [[InlineKeyboardButton("🎧 MP3 320k", callback_data=f"dl:{token}:sp3"), InlineKeyboardButton("💿 FLAC", callback_data=f"dl:{token}:sfl")]]
    await update.message.reply_text("🎵 <b>Spotify link detected</b>\nChoose a format:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keys))


async def media_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    token = remember(context, {"kind": "media", "url": url})
    keys = [[InlineKeyboardButton("🎬 Best video", callback_data=f"dl:{token}:best"), InlineKeyboardButton("📱 Upload-safe", callback_data=f"dl:{token}:safe")], [InlineKeyboardButton("🎧 MP3", callback_data=f"dl:{token}:mp3"), InlineKeyboardButton("🎼 M4A", callback_data=f"dl:{token}:m4a")], [InlineKeyboardButton("📎 Send original file", callback_data=f"dl:{token}:direct")]]
    await update.message.reply_text("🔗 <b>Media link detected</b>\nChoose a format. Playlists are supported up to the configured limit.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keys))


async def playstore(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    app_id = parse_qs(urlparse(url).query).get("id", [""])[0]; status = await update.message.reply_text(text(update.effective_user.id, "fetching"))
    if not ANDROID_APP_ID_RE.fullmatch(app_id): await status.edit_text("❌ This link has no valid Android application ID."); return
    token = remember(context, {"kind": "apk", "app_id": app_id, "name": f"{app_id}.apk"})
    keys = []
    if GOOGLE_PLAY_EMAIL and GOOGLE_PLAY_TOKEN:
        keys.append([InlineKeyboardButton("🛡 Google Play (direct)", callback_data=f"apk:{token}:google")])
    keys.append([InlineKeyboardButton("📦 APKPure mirror", callback_data=f"apk:{token}:apkpure"), InlineKeyboardButton("♻️ F-Droid", callback_data=f"apk:{token}:fdroid")])
    markup = InlineKeyboardMarkup(keys)
    try:
        data = await asyncio.to_thread(play_app, app_id)
        caption = f"📱 <b>{html.escape(data['title'])}</b>\n🏢 {html.escape(data.get('developer', 'Unknown'))}\n⭐ {data.get('score') or 'N/A'} · ⬇️ {html.escape(data.get('installs', 'N/A'))}\n\n{html.escape((data.get('summary') or '')[:500])}"
        if not (GOOGLE_PLAY_EMAIL and GOOGLE_PLAY_TOKEN):
            caption += "\n\nℹ️ Direct Google Play download needs optional server credentials; mirror choices are shown below."
        await status.delete(); await update.message.reply_photo(data["icon"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception as exc:
        log.warning("Play metadata lookup failed for %s: %s", app_id, exc)
        await status.edit_text(
            f"📱 <b>{html.escape(app_id)}</b>\nMetadata could not be loaded, but package providers are still available:",
            parse_mode=ParseMode.HTML, reply_markup=markup,
        )


async def apk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    if not authorized(query.from_user.id):
        await query.answer(text(query.from_user.id, "unauth"), show_alert=True); return
    await query.answer(); _, token, provider = query.data.split(":", 2); job = recall(context, token)
    if not job or job.get("kind") != "apk":
        await query.message.reply_text("⌛ This APK download expired. Send the Google Play link again."); return
    uid = query.from_user.id
    if uid in ACTIVE_DOWNLOADS:
        await query.message.reply_text("⏳ You already have an active download. Please wait for it to finish."); return
    ACTIVE_DOWNLOADS.add(uid); status = await query.message.reply_text("⬇️ Downloading the Android package…"); work: Path | None = None
    try:
        ensure_download_capacity(); DOWNLOAD_DIR.mkdir(exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"apk-{uid}-", dir=DOWNLOAD_DIR))
        source = {"google": "google-play", "apkpure": "apk-pure", "fdroid": "f-droid"}[provider]
        async with download_semaphore():
            found = await run_apkeep(job["app_id"], source, work)
        await status.edit_text("📤 Package downloaded. Uploading to Telegram…")
        sent = 0
        for path in found:
            sent += bool(await upload(context, query.message.chat_id, path))
        await status.edit_text(f"✅ Sent {sent} package file(s)." if sent else "⚠️ The package exceeded the configured upload limit.")
    except (RuntimeError, OSError, TelegramError) as exc:
        log.warning("APK download failed for %s: %s", job.get("app_id"), exc)
        await status.edit_text(f"❌ APK download failed.\n<code>{html.escape(str(exc)[:500])}</code>", parse_mode=ParseMode.HTML)
    finally:
        if work: shutil.rmtree(work, ignore_errors=True)
        ACTIVE_DOWNLOADS.discard(uid)


async def run_subprocess_result(args: list[str], timeout: int = TRANSFER_TIMEOUT) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill(); await process.communicate()
        raise RuntimeError(f"Download process exceeded the {timeout}-second timeout") from exc
    output = stdout.decode(errors="replace")
    return process.returncode or 0, output


async def run_subprocess(args: list[str], timeout: int = TRANSFER_TIMEOUT) -> str:
    returncode, output = await run_subprocess_result(args, timeout)
    if returncode:
        raise RuntimeError(last_diagnostic(output) or f"Download process exited with code {returncode}")
    return output


def last_diagnostic(output: str, fallback: str = "The provider returned no downloadable file") -> str:
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in output.splitlines()]
    useful = [line for line in lines if line and not line.startswith(YTDLP_FILE_MARKER)]
    errors = [line for line in useful if any(word in line.lower() for word in ("error", "failed", "forbidden", "unavailable", "login", "private", "drm"))]
    return (errors[-1] if errors else useful[-1] if useful else fallback)[-700:]


def files_from_markers(output: str, root: Path) -> list[Path]:
    root = root.resolve(); found: list[Path] = []
    for line in output.splitlines():
        if not line.startswith(YTDLP_FILE_MARKER):
            continue
        candidate = Path(line[len(YTDLP_FILE_MARKER):].strip()).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            log.warning("Extractor reported an output outside its work directory: %s", candidate)
            continue
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    return found


def ytdlp_command(action: str, output: Path, url: str, impersonate: bool = False) -> list[str]:
    executable = shutil.which("yt-dlp") or str(Path(sys.executable).parent / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp"))
    if not Path(executable).exists():
        raise RuntimeError("yt-dlp is not installed")
    limit = MAX_UPLOAD_MB * 1024 * 1024
    args = [
        executable, "--ignore-config", "--no-progress", "--ignore-errors", "--windows-filenames",
        "--playlist-end", str(MAX_PLAYLIST_ITEMS), "--retries", "5", "--fragment-retries", "5",
        "--socket-timeout", str(REQUEST_TIMEOUT), "--max-filesize", f"{MAX_DOWNLOAD_MB}M",
        "--print", f"after_move:{YTDLP_FILE_MARKER}%(filepath)s",
        "--output", str(output / "%(playlist_index&{} - |)s%(title).180B [%(id)s].%(ext)s"),
    ]
    if COOKIES_FILE:
        args.extend(["--cookies", COOKIES_FILE])
    if impersonate:
        args.extend(["--impersonate", "chrome"])
    if action == "best":
        args.extend(["--format", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", "--merge-output-format", "mp4"])
    elif action == "safe":
        args.extend(["--format", f"best[filesize<={limit}]/best[filesize_approx<={limit}]/bestvideo[filesize<={int(limit*.82)}]+bestaudio[filesize<={int(limit*.18)}]/best", "--merge-output-format", "mp4", "--max-filesize", str(limit)])
    elif action == "mp3":
        args.extend(["--format", "bestaudio/best", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"])
    else:
        args.extend(["--format", "bestaudio[ext=m4a]/bestaudio/best", "--extract-audio", "--audio-format", "m4a"])
    args.append(url)
    return args


async def run_ytdlp(url: str, action: str, output: Path) -> list[Path]:
    _, first_output = await run_subprocess_result(ytdlp_command(action, output, url))
    found = files_from_markers(first_output, output) or files_in(output)
    if found:
        return found
    diagnostic = last_diagnostic(first_output)
    retryable = any(word in diagnostic.lower() for word in ("403", "forbidden", "cloudflare", "impersonat", "blocked", "redirection"))
    if retryable:
        _, retry_output = await run_subprocess_result(ytdlp_command(action, output, url, impersonate=True))
        found = files_from_markers(retry_output, output) or files_in(output)
        if found:
            return found
        diagnostic = last_diagnostic(retry_output, diagnostic)
    raise RuntimeError(diagnostic)


def limit_spotdl_metadata(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        songs = data
    elif isinstance(data, dict) and isinstance(data.get("songs"), list):
        songs = data["songs"]
    else:
        raise RuntimeError("spotDL returned an unsupported metadata format")
    original_count = len(songs)
    del songs[MAX_PLAYLIST_ITEMS:]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return original_count


async def run_spotdl(url: str, fmt: str, output: Path) -> list[Path]:
    executable = shutil.which("spotdl") or str(Path(sys.executable).parent / ("spotdl.exe" if os.name == "nt" else "spotdl"))
    if not Path(executable).exists(): raise RuntimeError("spotDL is not installed")
    metadata = output / "selection.spotdl"
    save_output = await run_subprocess([executable, "save", url, "--save-file", str(metadata)])
    if not metadata.is_file():
        raise RuntimeError(last_diagnostic(save_output, "Spotify returned no track metadata"))
    count = await asyncio.to_thread(limit_spotdl_metadata, metadata)
    if not count: raise RuntimeError("The Spotify link contained no downloadable tracks")
    args = [
        executable, "download", str(metadata), "--format", fmt,
        "--audio", "youtube-music", "youtube", "soundcloud", "bandcamp",
        "--threads", str(MAX_CONCURRENT_DOWNLOADS),
        "--output", str(output / "{artists} - {title}.{output-ext}"),
        "--yt-dlp-args", f"--max-filesize {MAX_DOWNLOAD_MB}M --socket-timeout {REQUEST_TIMEOUT} --retries 5 --fragment-retries 5",
    ]
    if fmt == "mp3":
        args.extend(["--bitrate", "320k"])
    if COOKIES_FILE: args.extend(["--cookie-file", COOKIES_FILE])
    download_output = await run_subprocess(args)
    found = files_in(output)
    if not found:
        raise RuntimeError(last_diagnostic(download_output, "spotDL could not match this Spotify item to an available audio source"))
    return found


async def run_apkeep(app_id: str, provider: str, output: Path) -> list[Path]:
    executable = shutil.which("apkeep") or "/usr/local/bin/apkeep"
    if not Path(executable).exists():
        raise RuntimeError("apkeep is not installed; run sudo omnifetch update")
    args = [executable, "-a", app_id, "-d", provider]
    if provider == "google-play":
        if not GOOGLE_PLAY_EMAIL or not GOOGLE_PLAY_TOKEN:
            raise RuntimeError("Direct Google Play needs GOOGLE_PLAY_EMAIL and GOOGLE_PLAY_TOKEN in the server .env")
        if GOOGLE_PLAY_TOKEN_TYPE not in {"aas", "auth"}:
            raise RuntimeError("GOOGLE_PLAY_TOKEN_TYPE must be aas or auth")
        config = output / "apkeep.ini"
        token_key = "aas_token" if GOOGLE_PLAY_TOKEN_TYPE == "aas" else "auth_token"
        config.write_text(f"[google]\nemail = {GOOGLE_PLAY_EMAIL}\n{token_key} = {GOOGLE_PLAY_TOKEN}\n", encoding="utf-8")
        try:
            config.chmod(0o600)
        except OSError:
            pass
        args.extend(["-i", str(config)])
        if GOOGLE_PLAY_ACCEPT_TOS:
            args.append("--accept-tos")
    args.append(str(output))
    provider_output = await run_subprocess(args)
    found = [path for path in files_in(output) if path.suffix.lower() in {".apk", ".apks", ".xapk", ".obb"}]
    if not found:
        raise RuntimeError(last_diagnostic(provider_output, f"{provider} returned no installable package for {app_id}"))
    return found


def files_in(path: Path) -> list[Path]:
    return sorted((item for item in path.rglob("*") if item.is_file() and item.suffix.lower() not in {".part", ".ytdl", ".temp", ".spotdl"}), key=lambda item: item.name)


async def upload(context: ContextTypes.DEFAULT_TYPE, chat_id: int, path: Path) -> bool:
    size = path.stat().st_size / 1024 / 1024
    if size > MAX_UPLOAD_MB:
        if size > MAX_DOWNLOAD_MB:
            await context.bot.send_message(chat_id, f"⚠️ <code>{html.escape(path.name)}</code> is {size:.1f} MB (download ceiling: {MAX_DOWNLOAD_MB} MB).", parse_mode=ParseMode.HTML); return False
        if path.suffix.lower() in {".apk", ".apks", ".xapk"}:
            await context.bot.send_message(chat_id, "⚠️ This Android package is too large for the configured single-file limit and will not be split. Configure a local Bot API server to send it intact."); return False
        await send_large_file_parts(context, chat_id, path); return True
    with path.open("rb") as file:
        if path.suffix.lower() in {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}: await context.bot.send_audio(chat_id, file, read_timeout=TRANSFER_TIMEOUT, write_timeout=TRANSFER_TIMEOUT)
        elif path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
            try: await context.bot.send_video(chat_id, file, supports_streaming=True, read_timeout=TRANSFER_TIMEOUT, write_timeout=TRANSFER_TIMEOUT)
            except BadRequest: file.seek(0); await context.bot.send_document(chat_id, file, read_timeout=TRANSFER_TIMEOUT, write_timeout=TRANSFER_TIMEOUT)
        else: await context.bot.send_document(chat_id, file, read_timeout=TRANSFER_TIMEOUT, write_timeout=TRANSFER_TIMEOUT)
    return True


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    uid = query.from_user.id
    if not authorized(uid): await query.answer(text(uid, "unauth"), show_alert=True); return
    await query.answer(); _, token, action = query.data.split(":", 2); job = recall(context, token)
    if not job: await query.message.reply_text("⌛ This menu expired. Send the link again."); return
    if action == "direct":
        filename = safe_filename(unquote(Path(urlparse(job["url"]).path).name), "download.bin")
        await send_remote_document(query.message, context, job["url"], filename, "source website")
        return
    if uid in ACTIVE_DOWNLOADS:
        await query.message.reply_text("⏳ You already have an active download. Please wait for it to finish."); return
    ACTIVE_DOWNLOADS.add(uid)
    status = None
    try:
        status = await query.message.reply_text(text(uid, "wait"))
        ensure_download_capacity()
    except RuntimeError as exc:
        ACTIVE_DOWNLOADS.discard(uid)
        if status: await status.edit_text(f"❌ {html.escape(str(exc))}")
        return
    except TelegramError:
        ACTIVE_DOWNLOADS.discard(uid)
        return
    assert status is not None
    work: Path | None = None
    try:
        work = Path(tempfile.mkdtemp(prefix=f"{uid}-", dir=DOWNLOAD_DIR))
        async with download_semaphore():
            await status.edit_text("⬇️ Downloading and processing…")
            if job["kind"] == "spotify":
                found = await run_spotdl(job["url"], "flac" if action == "sfl" else "mp3", work)
            else:
                found = await run_ytdlp(job["url"], action, work)
        await status.edit_text(text(uid, "uploading")); sent = 0
        for path in found[:MAX_PLAYLIST_ITEMS]: sent += bool(await upload(context, query.message.chat_id, path))
        await status.edit_text(f"✅ Done — sent {sent} of {len(found)} file(s)." if sent else "⚠️ Every file exceeded the upload limit.")
    except (RuntimeError, OSError, TelegramError) as exc:
        log.warning("Download failed for %s: %s", uid, exc); detail = str(exc).splitlines()[-1][:350]
        hint = "\n\n💡 This site may require COOKIES_FILE." if any(word in detail.lower() for word in ("cookie", "sign in", "age")) else ""
        await status.edit_text(f"❌ Download failed.\n<code>{html.escape(detail)}</code>{hint}", parse_mode=ParseMode.HTML)
    finally:
        if work: shutil.rmtree(work, ignore_errors=True)
        ACTIVE_DOWNLOADS.discard(uid)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled update error", exc_info=context.error)


async def post_init(application: Application) -> None:
    await asyncio.to_thread(cleanup_stale_downloads)
    await application.bot.set_my_commands([BotCommand("start", "Open OmniFetch"), BotCommand("help", "Features and help"), BotCommand("status", "Bot limits and status"), BotCommand("id", "Your Telegram ID")])


def main() -> None:
    if not BOT_TOKEN: raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and configure it.")
    if not ADMIN_ID: raise SystemExit("ADMIN_ID must be a numeric Telegram user ID.")
    if COOKIES_FILE and not Path(COOKIES_FILE).is_file(): log.warning("COOKIES_FILE does not exist: %s", COOKIES_FILE)
    builder = Application.builder().token(BOT_TOKEN).post_init(post_init).concurrent_updates(max(4, MAX_CONCURRENT_DOWNLOADS * 2))
    if LOCAL_BOT_API:
        builder = builder.base_url(f"{BOT_API_URL}/bot").base_file_url(f"{BOT_API_URL}/file/bot").local_mode(True)
        log.info("Using local Telegram Bot API at %s", BOT_API_URL)
    app = builder.build()
    for command, callback in (("start", start), ("help", help_command), ("status", status_command), ("id", id_command), ("allow", allow_command), ("revoke", revoke_command), ("users", users_command)): app.add_handler(CommandHandler(command, callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.USERS_SHARED, user_shared))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang:(en|fa|ru|zh)$"))
    app.add_handler(CallbackQueryHandler(github_callback, pattern=r"^gh:[a-f0-9]{10}:(source|readme|releases|browse|release:[a-f0-9]{8}|download:[a-f0-9]{8}|path:[a-f0-9]{8})$"))
    app.add_handler(CallbackQueryHandler(apk_callback, pattern=r"^apk:[a-f0-9]{10}:(google|apkpure|fdroid)$"))
    app.add_handler(CallbackQueryHandler(download_callback, pattern=r"^dl:[a-f0-9]{10}:(best|safe|mp3|m4a|direct|sp3|sfl)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message)); app.add_error_handler(error_handler)
    log.info("OmniFetch is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)


if __name__ == "__main__": main()
