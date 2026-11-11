"""yt-dlp subprocess provider with final-file tracking and format probing."""
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .common import ProviderError, Result, host_is

MARKER = "__OMNIFETCH_FILE__"


@dataclass(frozen=True)
class Options:
    max_upload_mb: int
    max_download_mb: int
    max_job_mb: int
    max_items: int
    request_timeout: int
    transfer_timeout: int
    reserve_mb: int
    cookies: str = ""
    instagram_cookies: str = ""
    x_cookies: str = ""


def diagnostic(output, fallback="The provider returned no downloadable file"):
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in output.splitlines()]
    useful = [line for line in lines if line and not line.startswith(MARKER)]
    for index, line in enumerate(useful):
        if "audio providererror" in line.lower() or "yt-dlp download error" in line.lower():
            return " | ".join(useful[max(0, index - 2):index + 1])[-700:]
    errors = [line for line in useful if any(word in line.lower() for word in
              ("error", "failed", "forbidden", "unavailable", "login", "private", "drm"))]
    return (errors[-1] if errors else useful[-1] if useful else fallback)[-700:]


def completed_files(output, root):
    root = root.resolve()
    found = []
    for line in output.splitlines():
        if line.startswith(MARKER):
            candidate = Path(line[len(MARKER):].strip()).resolve()
            if candidate.is_relative_to(root) and candidate.is_file() and candidate not in found:
                found.append(candidate)
    return found


class YtDlp:
    def __init__(self, options, runner):
        self.options, self.run = options, runner

    def executable(self):
        exe = shutil.which("yt-dlp") or str(Path(sys.executable).parent / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"))
        if not Path(exe).is_file():
            raise ProviderError("yt-dlp is not installed; run sudo omnifetch update")
        return exe

    def cookie_file(self, url):
        opts = self.options
        if host_is(url, "instagram.com") and opts.instagram_cookies:
            return opts.instagram_cookies
        if (host_is(url, "x.com") or host_is(url, "twitter.com")) and opts.x_cookies:
            return opts.x_cookies
        return opts.cookies

    def command(self, action, output, url, impersonate=False, force_ipv4=False):
        opts = self.options
        limit = opts.max_upload_mb * 1024 * 1024
        args = [self.executable(), "--ignore-config", "--no-progress", "--ignore-errors", "--windows-filenames",
                "--playlist-end", str(opts.max_items), "--retries", "5", "--fragment-retries", "5",
                "--socket-timeout", str(opts.request_timeout), "--max-filesize", f"{opts.max_download_mb}M",
                "--print", f"after_move:{MARKER}%(filepath)s",
                "--output", str(output / "%(playlist_index&{} - |)s%(title).180B [%(id)s].%(ext)s")]
        cookies = self.cookie_file(url)
        if cookies:
            args += ["--cookies", cookies]
        if force_ipv4:
            args.append("--force-ipv4")
        if impersonate:
            args += ["--impersonate", "chrome"]
        if action == "best":
            args += ["--format", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]
        elif re.fullmatch(r"h\d{3,4}", action):
            height = int(action[1:])
            args += ["--format", f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
                     "--merge-output-format", "mp4"]
        elif action == "safe":
            args += ["--format", f"best[filesize<={limit}]/best[filesize_approx<={limit}]/bestvideo[filesize<={int(limit*.82)}]+bestaudio[filesize<={int(limit*.18)}]/best",
                     "--merge-output-format", "mp4", "--max-filesize", str(limit)]
        elif action in {"mp3", "m4a"}:
            args += ["--format", "bestaudio/best", "--extract-audio", "--audio-format", action, "--audio-quality", "0"]
        else:
            raise ProviderError("Unknown media format")
        return args + [url]

    async def download(self, url, action, output):
        opts = self.options
        last = ""
        for variant in ("default", "ipv4", "browser"):
            command = self.command(action, output, url, impersonate=variant == "browser", force_ipv4=variant == "ipv4")
            try:
                code, text = await self.run(command, timeout=opts.transfer_timeout,
                                            budget_root=output, budget_bytes=opts.max_job_mb * 1024 * 1024,
                                            reserve_bytes=opts.reserve_mb * 1024 * 1024)
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from exc
            files = completed_files(text, output)
            if files:
                warnings = []
                if code or any(line.lstrip().startswith("ERROR:") for line in text.splitlines()):
                    warnings.append("Some playlist items failed: " + diagnostic(text)[:250])
                return Result(files, "yt-dlp", warnings)
            last = diagnostic(text, last or "No completed file was produced")
            retryable = any(word in last.lower() for word in
                            ("403", "forbidden", "cloudflare", "impersonat", "blocked", "redirection", "sign in", "not a bot"))
            if not retryable:
                break
        raise ProviderError(last)

    async def probe(self, url):
        args = [self.executable(), "--ignore-config", "--no-playlist", "--skip-download",
                "--dump-single-json", "--no-warnings", "--socket-timeout", "10", "--retries", "0"]
        cookies = self.cookie_file(url)
        if cookies:
            args += ["--cookies", cookies]
        code, text = await self.run(args + [url], timeout=25)
        if code:
            raise ProviderError("Could not inspect available formats")
        try:
            info = json.loads(text)
        except ValueError:
            raise ProviderError("Invalid format metadata") from None
        formats = info.get("formats") or []
        heights = sorted({int(f["height"]) for f in formats
                          if f.get("height") and f.get("vcodec") not in (None, "none", "images")
                          and not str(f.get("format_id", "")).startswith("sb")}, reverse=True)
        return [height for height in heights if 144 <= height <= 4320][:8]
