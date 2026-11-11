import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import bot
from providers import aparat
from providers.common import Settings
from providers.router import Router


class Regressions(unittest.IsolatedAsyncioTestCase):
    async def test_failed_icon_keeps_apk_buttons_and_editable_status(self):
        status = AsyncMock()
        message = NS(reply_text=AsyncMock(return_value=status),
                     reply_photo=AsyncMock(side_effect=bot.TelegramError('bad photo')))
        update = NS(message=message, effective_user=NS(id=1))
        with patch.object(bot, 'play_app', return_value={'title': 'Test', 'icon': 'https://example.com/icon'}), \
             patch.object(bot, 'remember', return_value='1234567890'), \
             patch.object(bot, 'text', return_value='Loading'):
            await bot.playstore(update, NS(), 'https://play.google.com/store/apps/details?id=com.example.app')
        status.delete.assert_not_awaited()
        self.assertIn('reply_markup', status.edit_text.await_args.kwargs)

    async def test_apk_status_failure_releases_user_slot(self):
        query = NS(from_user=NS(id=123), answer=AsyncMock(), data='apk:1234567890:fdroid',
                   message=NS(reply_text=AsyncMock(side_effect=bot.TelegramError('offline'))))
        with patch.object(bot, 'authorized', return_value=True), \
             patch.object(bot, 'recall', return_value={'kind': 'apk', 'app_id': 'com.example.app'}):
            await bot.apk_callback(NS(callback_query=query), NS())
        self.assertNotIn(123, bot.ACTIVE_DOWNLOADS)

    async def test_spotify_partial_failure_delivers_audio_only_with_warning(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            async def save(*args, **kwargs):
                (root / 'selection.spotdl').write_text('[{}, {}]')
                return 'Saved two tracks'
            async def download(*args, **kwargs):
                (root / 'song.mp3').write_bytes(b'audio')
                (root / 'debug.log').write_text('not audio')
                (root / 'second.mp3.part').write_bytes(b'partial')
                return 1, 'ERROR: second track unavailable'
            with patch.object(bot.shutil, 'which', return_value=sys.executable), \
                 patch.object(bot, 'run_subprocess', side_effect=save), \
                 patch.object(bot, 'run_subprocess_result', side_effect=download):
                result = await bot.run_spotdl_result('https://open.spotify.com/playlist/test', 'mp3', root)
            self.assertEqual([p.name for p in result.files], ['song.mp3'])
            self.assertTrue(result.warnings)

    async def test_aparat_page_fallback_selects_requested_quality(self):
        page = b'<source src="https://cdn.example/video_1080p.mp4"><source src="https://cdn.example/video_720p.mp4?x=1&amp;y=2">'
        class Content:
            async def iter_chunked(self, size):
                yield page
        @asynccontextmanager
        async def response(*args, **kwargs):
            yield NS(content=Content())
        http = AsyncMock()
        http.__aenter__.return_value = http
        http.response = response
        async def save(url, destination):
            destination.write_bytes(b'video')
            return destination
        http.download.side_effect = save
        settings = Settings(1024, 2048, 10, 0)
        with TemporaryDirectory() as directory, patch.object(aparat, 'HTTP', return_value=http):
            result = await aparat.download('https://aparat.com/v/test', 'h720', Path(directory),
                                           settings, AsyncMock(), AsyncMock())
            self.assertEqual(result.files[0].read_bytes(), b'video')
        self.assertEqual(http.download.await_args.args[0], 'https://cdn.example/video_720p.mp4?x=1&y=2')
        self.assertEqual(Router(settings, None, None).order('https://www.aparat.com/v/test', 'h720'),
                         ['ytdlp', 'aparat'])
