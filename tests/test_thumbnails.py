"""Offline tests for Telegram thumbnail generation and MP3 upload behavior."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest

import bot


class TelegramThumbnailTests(unittest.IsolatedAsyncioTestCase):
    def test_ffmpeg_command_creates_320_square_jpeg_from_embedded_art(self):
        cmd = bot.build_telegram_thumbnail_command(
            Path("/tmp/song.mp3"), Path("/tmp/cover.jpg")
        )
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertEqual(cmd[cmd.index("-map") + 1], "0:v:0")
        self.assertIn("scale=320:320", cmd[cmd.index("-vf") + 1])
        self.assertIn("pad=320:320", cmd[cmd.index("-vf") + 1])
        self.assertEqual(cmd[-1], "/tmp/cover.jpg")

    async def test_valid_jpeg_is_available_for_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "cover.jpg"
            dest.write_bytes(bytes((0xff, 0xd8, 0xff, 0xd9)))
            proc = SimpleNamespace(returncode=0, wait=AsyncMock())
            with patch("bot.asyncio.create_subprocess_exec", new_callable=AsyncMock) as start:
                start.return_value = proc
                result = await bot.prepare_telegram_thumbnail(
                    Path(directory) / "audio.mp3", dest
                )
            self.assertEqual(result, dest)
            start.assert_awaited_once()

    async def test_missing_or_oversized_cover_does_not_block_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "cover.jpg"
            proc = SimpleNamespace(returncode=0, wait=AsyncMock())
            with patch("bot.asyncio.create_subprocess_exec", new_callable=AsyncMock) as start:
                start.return_value = proc
                self.assertIsNone(
                    await bot.prepare_telegram_thumbnail(Path("audio.mp3"), dest)
                )
                dest.write_bytes(bytes((0xff, 0xd8, 0xff)) + b"x" * 200000)
                self.assertIsNone(
                    await bot.prepare_telegram_thumbnail(Path("audio.mp3"), dest)
                )

    async def test_mp3_upload_supplies_separate_cover_without_changing_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "My Music.mp3"
            cover = Path(directory) / "telegram-cover.jpg"
            audio.write_bytes(b"mp3-audio-data")
            cover.write_bytes(bytes((0xff, 0xd8, 0xff, 0xd9)))
            calls = []

            async def upload(**kwargs):
                calls.append({
                    "audio": kwargs["audio"].read(),
                    "cover": kwargs["thumbnail"].read(),
                    "title": kwargs["title"],
                    "filename": kwargs["filename"],
                })
                return SimpleNamespace(message_id=77)

            msg = SimpleNamespace(reply_audio=AsyncMock(side_effect=upload))
            result = await bot.send_mp3(msg, audio, {"caption": None}, cover)

            self.assertEqual(result.message_id, 77)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["audio"], b"mp3-audio-data")
            self.assertEqual(calls[0]["cover"], cover.read_bytes())
            self.assertEqual(calls[0]["title"], "My Music")
            self.assertEqual(calls[0]["filename"], "My Music.mp3")
            self.assertEqual(audio.read_bytes(), b"mp3-audio-data")

    async def test_invalid_thumbnail_retries_mp3_without_it(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "Song.mp3"
            cover = Path(directory) / "telegram-cover.jpg"
            audio.write_bytes(b"original-mp3")
            cover.write_bytes(bytes((0xff, 0xd8, 0xff, 0xd9)))
            calls = []

            async def upload(**kwargs):
                calls.append((kwargs["audio"].read(), "thumbnail" in kwargs))
                if "thumbnail" in kwargs:
                    raise BadRequest("Bad Request: wrong thumbnail")
                return SimpleNamespace(message_id=78)

            msg = SimpleNamespace(reply_audio=AsyncMock(side_effect=upload))
            result = await bot.send_mp3(msg, audio, {}, cover)

            self.assertEqual(result.message_id, 78)
            self.assertEqual(calls, [
                (b"original-mp3", True),
                (b"original-mp3", False),
            ])
            self.assertEqual(audio.read_bytes(), b"original-mp3")

    async def test_non_youtube_behavior_can_upload_without_thumbnail(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "Other Site.mp3"
            audio.write_bytes(b"unchanged")
            msg = SimpleNamespace(
                reply_audio=AsyncMock(return_value=SimpleNamespace(message_id=79))
            )
            await bot.send_mp3(msg, audio, {}, None)
            self.assertNotIn("thumbnail", msg.reply_audio.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
