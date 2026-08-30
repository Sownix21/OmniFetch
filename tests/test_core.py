import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import bot


class UrlTests(unittest.TestCase):
    def test_extracts_and_trims_url(self):
        self.assertEqual(bot.extract_url("look https://example.com/video?id=1)."), "https://example.com/video?id=1")

    def test_rejects_non_url(self):
        self.assertIsNone(bot.extract_url("hello world"))

    def test_rejects_localhost(self):
        valid, reason = asyncio.run(bot.validate_url("http://localhost/private"))
        self.assertFalse(valid)
        self.assertIn("Local", reason)

    def test_safe_filename_removes_path_and_unsafe_characters(self):
        self.assertEqual(bot.safe_filename("../../release<1>.zip"), "release_1_.zip")


class DownloadOptionTests(unittest.TestCase):
    def test_playlist_limit_and_cookie_configuration(self):
        with TemporaryDirectory() as directory:
            options = bot.ytdlp_options("best", Path(directory))
        self.assertEqual(options["playlistend"], bot.MAX_PLAYLIST_ITEMS)
        self.assertEqual(options["merge_output_format"], "mp4")

    def test_safe_mode_has_size_ceiling(self):
        with TemporaryDirectory() as directory:
            options = bot.ytdlp_options("safe", Path(directory))
        self.assertEqual(options["max_filesize"], bot.MAX_UPLOAD_MB * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
