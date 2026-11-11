import asyncio
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import bot
from providers import cobalt, instagram, process
from providers.common import ProviderError, Result, Settings, check_files, host_is
from providers.router import Router
from providers.ytdlp import Options, YtDlp, MARKER

SETTINGS = Settings(10000, 20000, 10, 0, cobalt_url="http://127.0.0.1:9000")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fake_http(self, data, fail_at=None):
        downloaded = []
        http = AsyncMock()
        http.__aenter__.return_value = http
        http.json.return_value = data

        async def download(url, dest, **kw):
            downloaded.append((url, kw))
            if len(downloaded) == fail_at:
                raise ProviderError("Transfer failed")
            dest.write_bytes(b"content")
            return dest
        http.download.side_effect = download
        return http, downloaded

    async def test_cobalt_failure_discards_partial_files_and_uses_ytdlp(self):
        async def broken(url, action, root, settings, progress):
            (root / "incomplete.mp4").write_bytes(b"partial")
            raise ProviderError("Second slide failed")

        async def fallback(url, action, root):
            file = root / "complete.mp4"
            file.write_bytes(b"complete")
            return Result([file], "yt-dlp")

        router = Router(SETTINGS, fallback, AsyncMock())
        with patch("providers.cobalt.download", broken):
            result = await router.download("https://tiktok.com/example", "best", self.root, AsyncMock())
        self.assertEqual(result.provider, "yt-dlp")
        self.assertFalse((self.root / "cobalt").exists())
        self.assertEqual(result.files[0].read_bytes(), b"complete")

    def test_routes_use_exact_hosts_and_keep_playlist_on_ytdlp(self):
        router = Router(SETTINGS, AsyncMock(), AsyncMock())
        self.assertEqual(router.order("https://www.youtube.com/watch?v=one", "best"), ["ytdlp", "cobalt"])
        self.assertEqual(router.order("https://www.youtube.com/watch?v=one&list=album", "best"), ["ytdlp"])
        self.assertEqual(router.order("https://evil.test/instagram.com/a", "best"), ["ytdlp"])
        self.assertFalse(host_is("https://instagram.com.evil.test/p/A", "instagram.com"))
        self.assertEqual(Router(replace(SETTINGS, cobalt_url=""), None, None).order(
            "https://tiktok.com/a", "best"), ["ytdlp"])

    async def test_cobalt_picker_delivers_every_selected_file_with_unique_names(self):
        http, calls = self.fake_http({"status": "picker", "picker": [
            {"url": "https://cdn.example/first", "type": "photo"},
            {"url": "https://cdn.example/second", "type": "video"}]})
        with patch("providers.cobalt.HTTP", return_value=http):
            result = await cobalt.download("https://instagram.com/p/A", "best", self.root, SETTINGS, AsyncMock())
        self.assertEqual([f.name for f in result.files], ["media_01.jpg", "media_02.mp4"])
        self.assertEqual(len(calls), 2)
        self.assertFalse(result.warnings)

    async def test_cobalt_local_processing_is_a_fallback_not_a_fake_success(self):
        http, _ = self.fake_http({"status": "local-processing"})
        with patch("providers.cobalt.HTTP", return_value=http), self.assertRaises(ProviderError):
            await cobalt.download("https://tiktok.com/a", "best", self.root, SETTINGS, AsyncMock())
        http.download.assert_not_called()

    async def test_instagram_mixed_carousel_downloads_all_slides_without_sending_cookies_to_cdn(self):
        data = {"items": [{"carousel_media_count": 2, "carousel_media": [
            {"image_versions2": {"candidates": [{"url": "https://cdn.example/a.jpg", "width": 100, "height": 100}]}},
            {"video_versions": [{"url": "https://cdn.example/b.mp4", "width": 200, "height": 200}]}]}]}
        http, calls = self.fake_http(data)
        with patch("providers.instagram.HTTP", return_value=http), patch(
                "providers.instagram.cookie_headers", return_value={"Cookie": "sessionid=secret"}):
            result = await instagram.download("https://instagram.com/p/ABC/", "best", self.root,
                                               SETTINGS, AsyncMock(), AsyncMock())
        self.assertEqual([f.name for f in result.files], ["slide_01.jpg", "slide_02.mp4"])
        self.assertEqual(http.json.await_args.kwargs["headers"]["Cookie"], "sessionid=secret")
        self.assertTrue(all(not kw for _, kw in calls))

    async def test_instagram_does_not_silently_accept_missing_slides(self):
        http, _ = self.fake_http({"items": [{"carousel_media_count": 2, "carousel_media": [
            {"video_versions": [{"url": "https://cdn.example/a.mp4"}]}]}]})
        with patch("providers.instagram.HTTP", return_value=http), self.assertRaisesRegex(ProviderError, "incomplete"):
            await instagram.download("https://instagram.com/p/ABC", "best", self.root, SETTINGS, AsyncMock(), AsyncMock())
        http.download.assert_not_called()

    async def test_album_item_limit_is_reported(self):
        http, _ = self.fake_http({"status": "picker", "picker": [
            {"url": "https://cdn.example/a.mp4"}, {"url": "https://cdn.example/b.mp4"}]})
        with patch("providers.cobalt.HTTP", return_value=http):
            result = await cobalt.download("https://tiktok.com/a", "best", self.root,
                                           replace(SETTINGS, max_items=1), AsyncMock())
        self.assertEqual(len(result.files), 1)
        self.assertTrue(result.warnings)

    async def test_instagram_story_audio_uses_conversion(self):
        http, _ = self.fake_http({"items": [{"video_versions": [{"url": "https://cdn.example/a.mp4"}]}]})
        async def convert(source, dest, fmt):
            self.assertTrue(source.is_file())
            self.assertEqual(fmt, "mp3")
            dest.write_bytes(b"audio")
        with patch("providers.instagram.HTTP", return_value=http):
            result = await instagram.download("https://instagram.com/stories/user/1234567890/",
                                               "mp3", self.root, SETTINGS, AsyncMock(), convert)
        self.assertEqual(result.files[0].suffix, ".mp3")
        self.assertFalse((self.root / "slide_01.mp4").exists())
        self.assertIn("/1234567890/info/", http.json.await_args.args[0])

    def test_cookie_file_filters_unrelated_domains(self):
        file = self.root / "cookies.txt"
        file.write_text("# Netscape HTTP Cookie File\n"
                        ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tallowed\n"
                        ".notinstagram.com\tTRUE\t/\tTRUE\t9999999999\tsecret\tblocked\n")
        self.assertEqual(instagram.cookie_headers(str(file)), {"Cookie": "sessionid=allowed"})

    def test_file_validation_rejects_escape_and_aggregate_overflow(self):
        work = self.root / "job"; work.mkdir()
        outside = self.root / "outside"; outside.write_bytes(b"x")
        with self.assertRaises(ProviderError):
            check_files([outside], work, SETTINGS)
        first = work / "a"; first.write_bytes(b"123")
        second = work / "b"; second.write_bytes(b"456")
        with self.assertRaisesRegex(ProviderError, "total"):
            check_files([first, second], work, replace(SETTINGS, max_job_bytes=5))

    async def test_upload_failure_does_not_hide_remaining_files(self):
        context, status = NS(), AsyncMock()
        files = [self.root / name for name in ("a", "b", "c")]
        with patch.object(bot, "upload", AsyncMock(side_effect=[True, bot.TelegramError("failed"), True])):
            sent, failures = await bot.deliver_files(context, 1, files, status)
        self.assertEqual(sent, 2)
        self.assertEqual(failures, ["b: upload failed"])

    async def test_ytdlp_partial_playlist_is_reported_and_only_final_files_are_used(self):
        file = self.root / "complete.mp4"; file.write_bytes(b"video")
        (self.root / "secret.ini").write_text("do not upload")
        runner = AsyncMock(return_value=(1, f"{MARKER}{file}\nERROR: item 2 unavailable"))
        provider = YtDlp(Options(49, 500, 1000, 10, 20, 60, 0), runner)
        with patch.object(provider, "executable", return_value=sys.executable):
            result = await provider.download("https://youtube.com/playlist?list=x", "best", self.root)
        self.assertEqual(result.files, [file.resolve()])
        self.assertTrue(result.warnings)

    async def test_ytdlp_preserves_ipv4_retry(self):
        file = self.root / "video.mp4"; file.write_bytes(b"video")
        runner = AsyncMock(side_effect=[(1, "ERROR: Sign in to confirm you are not a bot"),
                                       (0, f"{MARKER}{file}")])
        provider = YtDlp(Options(49, 500, 1000, 10, 20, 60, 0), runner)
        with patch.object(provider, "executable", return_value=sys.executable):
            result = await provider.download("https://youtube.com/watch?v=x", "best", self.root)
        self.assertIn("--force-ipv4", runner.await_args.args[0])
        self.assertEqual(result.files, [file.resolve()])

    async def test_format_probe_filters_storyboards_and_audio(self):
        runner = AsyncMock(return_value=(0, json.dumps({"formats": [
            {"height": 1080, "vcodec": "avc1"}, {"height": 720, "vcodec": "avc1"},
            {"height": 1080, "vcodec": "avc1"}, {"height": 90, "vcodec": "images"},
            {"height": None, "vcodec": "none"}]})))
        provider = YtDlp(Options(49, 500, 1000, 10, 20, 60, 0), runner)
        with patch.object(provider, "executable", return_value=sys.executable):
            heights = await provider.probe("https://example.com/video")
        self.assertEqual(heights, [1080, 720])
        self.assertIn("--no-playlist", runner.await_args.args[0])

    async def test_cancel_terminates_download_subprocess(self):
        launched = asyncio.Event()
        processes = []
        real_spawn = asyncio.create_subprocess_exec
        async def capture(*args, **kwargs):
            child = await real_spawn(*args, **kwargs)
            processes.append(child)
            launched.set()
            return child
        with patch("providers.process.asyncio.create_subprocess_exec", capture):
            task = asyncio.create_task(process.run([sys.executable, "-c", "import time; time.sleep(60)"], 120))
            await asyncio.wait_for(launched.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(processes[0].returncode)
