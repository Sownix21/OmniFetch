import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from aiohttp import web
import bot
from providers.common import Settings, Result
from providers.router import Router


class Integration(unittest.IsolatedAsyncioTestCase):
    async def test_local_cobalt_http_to_completed_files(self):
        observed = []
        async def api(request):
            observed.append(("api", request.headers.get("Authorization"), await request.json()))
            return web.json_response({"status": "picker", "picker": [
                {"url": base + "/tunnel?id=photo", "type": "photo"},
                {"url": base + "/tunnel?id=video", "type": "video"}]})
        async def tunnel(request):
            observed.append(("tunnel", request.headers.get("Authorization"), request.query["id"]))
            return web.Response(body=b"test-media")
        app = web.Application()
        app.router.add_post("/", api)
        app.router.add_get("/tunnel", tunnel)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        try:
            settings = Settings(1024, 2048, 10, 0, cobalt_url=base, cobalt_key="test-key")
            fallback = AsyncMock()
            with TemporaryDirectory() as directory:
                result = await Router(settings, fallback, AsyncMock()).download(
                    "https://tiktok.com/example", "best", Path(directory), AsyncMock())
                self.assertEqual(len(result.files), 2)
                self.assertTrue(all(p.read_bytes() == b"test-media" for p in result.files))
            self.assertEqual(observed[0][1], "Api-Key test-key")
            self.assertTrue(all(row[1] is None for row in observed[1:]))
            fallback.assert_not_called()
        finally:
            await runner.cleanup()

    def setup_request(self):
        status = AsyncMock()
        message = NS(chat_id=42, reply_text=AsyncMock(return_value=status))
        query = NS(from_user=NS(id=42), message=message, answer=AsyncMock(),
                   data="dl:1234567890:best")
        return NS(callback_query=query), NS(), status

    async def test_bot_reports_partial_upload_and_removes_job_files(self):
        update, context, status = self.setup_request()
        async def download(url, action, root, progress):
            files = [root / name for name in ("a.mp4", "b.mp4", "c.mp4")]
            for file in files:
                file.write_bytes(b"media")
            return Result(files, "test provider")
        with TemporaryDirectory() as directory, patch.multiple(
                bot, DOWNLOAD_DIR=Path(directory), DOWNLOAD_SEMAPHORE=None), \
                patch.object(bot, "authorized", return_value=True), \
                patch.object(bot, "recall", return_value={"kind": "media", "url": "https://example.com/video"}), \
                patch.object(bot, "validate_url", AsyncMock(return_value=(True, ""))), \
                patch.object(bot, "ensure_download_capacity"), \
                patch.object(bot.Router, "download", download), \
                patch.object(bot, "upload", AsyncMock(side_effect=[True, bot.TelegramError("fail"), True])):
            # Router.download is an instance method; the replacement includes
            # its receiver through an AsyncMock side effect below.
            with patch.object(bot.Router, "download", AsyncMock(side_effect=download)):
                await bot.download_callback(update, context)
            report = status.edit_text.await_args.args[0]
            self.assertIn("Partial result", report)
            self.assertIn("sent 2 of 3", report)
            self.assertFalse(list(Path(directory).iterdir()))
            self.assertNotIn(42, bot.ACTIVE_DOWNLOADS)

    async def test_cancelled_request_removes_files_and_releases_user_slot(self):
        update, context, status = self.setup_request()
        started = asyncio.Event()
        async def download(url, action, root, progress):
            (root / "partial").write_bytes(b"partial")
            started.set()
            await asyncio.Event().wait()
        with TemporaryDirectory() as directory, patch.multiple(
                bot, DOWNLOAD_DIR=Path(directory), DOWNLOAD_SEMAPHORE=None), \
                patch.object(bot, "authorized", return_value=True), \
                patch.object(bot, "recall", return_value={"kind": "media", "url": "https://example.com/video"}), \
                patch.object(bot, "validate_url", AsyncMock(return_value=(True, ""))), \
                patch.object(bot, "ensure_download_capacity"), \
                patch.object(bot.Router, "download", AsyncMock(side_effect=download)):
            task = asyncio.create_task(bot.download_callback(update, context))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(list(Path(directory).iterdir()))
            self.assertNotIn(42, bot.ACTIVE_DOWNLOADS)

    async def test_finished_report_falls_back_to_reply_when_edit_fails(self):
        status = AsyncMock()
        status.edit_text.side_effect = bot.TelegramError("edit failed")
        await bot.update_status(status, "Sent 2 of 3 files.", final=True)
        status.reply_text.assert_awaited_once_with("Sent 2 of 3 files.", parse_mode=None)
