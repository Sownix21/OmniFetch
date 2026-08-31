import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

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

    def test_rejects_private_ip(self):
        valid, reason = bot.validate_public_url("http://127.0.0.1/private")
        self.assertFalse(valid)
        self.assertIn("Private", reason)

    def test_download_redirect_cannot_reach_private_network(self):
        redirect = Mock(is_redirect=True, headers={"Location": "http://127.0.0.1/secret"})
        with TemporaryDirectory() as directory, \
             patch.object(bot, "validate_public_url", side_effect=[(True, ""), (False, "Private address")]), \
             patch.object(bot.requests, "get", return_value=redirect) as request:
            with self.assertRaisesRegex(RuntimeError, "Blocked unsafe"):
                bot.download_http_file("https://example.com/file", Path(directory), "file.bin")
        redirect.close.assert_called_once()
        request.assert_called_once()

    def test_safe_filename_removes_path_and_unsafe_characters(self):
        self.assertEqual(bot.safe_filename("../../release<1>.zip"), "release_1_.zip")


class DownloadOptionTests(unittest.TestCase):
    def test_invalid_integer_configuration_is_clear(self):
        with patch.dict(os.environ, {"OMNIFETCH_TEST_INT": "not-a-number"}):
            with self.assertRaisesRegex(SystemExit, "must be a whole number"):
                bot.env_int("OMNIFETCH_TEST_INT", 1)

    def test_playlist_limit_and_safe_size_are_in_cli_command(self):
        with TemporaryDirectory() as directory, patch.object(bot.shutil, "which", return_value=bot.sys.executable):
            command = bot.ytdlp_command("safe", Path(directory), "https://example.com/video")
        self.assertEqual(command[command.index("--playlist-end") + 1], str(bot.MAX_PLAYLIST_ITEMS))
        self.assertIn(str(bot.MAX_UPLOAD_MB * 1024 * 1024), command)

    def test_ytdlp_command_reports_final_postprocessed_path(self):
        with TemporaryDirectory() as directory, patch.object(bot.shutil, "which", return_value=bot.sys.executable):
            command = bot.ytdlp_command("mp3", Path(directory), "https://example.com/watch?v=1")
        self.assertIn(f"after_move:{bot.YTDLP_FILE_MARKER}%(filepath)s", command)
        self.assertIn("--extract-audio", command)
        self.assertEqual(command[-1], "https://example.com/watch?v=1")

    def test_only_accepts_reported_files_inside_job_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "song.mp3"; media.write_bytes(b"audio")
            outside = root.parent / "outside.mp3"; outside.write_bytes(b"outside")
            try:
                output = f"{bot.YTDLP_FILE_MARKER}{media}\n{bot.YTDLP_FILE_MARKER}{outside}\n"
                self.assertEqual(bot.files_from_markers(output, root), [media.resolve()])
            finally:
                outside.unlink(missing_ok=True)

    def test_large_file_parts_reconstruct_exactly(self):
        with TemporaryDirectory() as directory:
            original = Path(directory) / "release.zip"
            payload = bytes(range(32)); original.write_bytes(payload)
            parts = bot.split_large_file(original, 7)
            rebuilt = b"".join(part.read_bytes() for part in parts)
        self.assertEqual(rebuilt, payload)
        self.assertEqual(len(parts), 5)

    def test_spotify_metadata_is_limited_before_download(self):
        with TemporaryDirectory() as directory, patch.object(bot, "MAX_PLAYLIST_ITEMS", 2):
            metadata = Path(directory) / "playlist.spotdl"
            metadata.write_text('{"songs":[{"id":1},{"id":2},{"id":3}]}', encoding="utf-8")
            original_count = bot.limit_spotdl_metadata(metadata)
            saved = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertEqual(original_count, 3)
        self.assertEqual(len(saved["songs"]), 2)


if __name__ == "__main__":
    unittest.main()
