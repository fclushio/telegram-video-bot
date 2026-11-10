"""Offline tests for per-chat output modes and MP3 command construction."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from chat_modes import get_mode, initialize, set_mode


class ChatModeTests(unittest.TestCase):
    def test_persistence_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db" / "modes.sqlite3"
            initialize(path)
            self.assertEqual(get_mode(path, -10001), "video")
            set_mode(path, -10001, "mp3")
            self.assertEqual(get_mode(path, -10001), "mp3")
            self.assertEqual(get_mode(path, -10002), "video")
            initialize(path)  # Reinstalling must not reset per-chat modes
            self.assertEqual(get_mode(path, -10001), "mp3")
            set_mode(path, -10001, "video")
            self.assertEqual(get_mode(path, -10001), "video")

    def test_reject_invalid_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modes.sqlite3"
            initialize(path)
            with self.assertRaises(ValueError):
                set_mode(path, 123, "wav")

    def test_mp3_command_extracts_audio_not_video(self):
        cmd = bot.build_yt_dlp_command(
            "https://youtu.be/example", Path("/tmp/audio"), "unused", mode="mp3"
        )
        self.assertIn("--extract-audio", cmd)
        self.assertIn("--audio-format", cmd)
        self.assertEqual(cmd[cmd.index("--audio-format") + 1], "mp3")
        self.assertNotIn("--remux-video", cmd)
        self.assertIn("bestaudio/best", cmd)
        self.assertEqual(cmd[-2:], ["--", "https://youtu.be/example"])

    def test_only_youtube_mp3_embeds_thumbnail(self):
        youtube_urls = (
            "https://www.youtube.com/watch?v=123",
            "https://music.youtube.com/watch?v=123",
            "https://m.youtube.com/shorts/123",
            "https://youtu.be/123",
            "https://www.youtube-nocookie.com/embed/123",
        )
        other_urls = (
            "https://www.tiktok.com/@example/video/123",
            "https://www.instagram.com/reel/123/",
            "https://youtube.com.evil.example/video",
            "https://notyoutube.com/video",
            "https://youtu.be.evil.example/123",
        )
        for url in youtube_urls:
            with self.subTest(url=url):
                cmd = bot.build_yt_dlp_command(url, Path("/tmp/audio"), "unused", mode="mp3")
                self.assertIn("--embed-thumbnail", cmd)
                self.assertIn("--convert-thumbnails", cmd)
                self.assertEqual(cmd[cmd.index("--convert-thumbnails") + 1], "jpg")
                self.assertIn("--embed-metadata", cmd)
        for url in other_urls:
            with self.subTest(url=url):
                cmd = bot.build_yt_dlp_command(url, Path("/tmp/audio"), "unused", mode="mp3")
                self.assertNotIn("--embed-thumbnail", cmd)
                self.assertNotIn("--convert-thumbnails", cmd)
                self.assertIn("--embed-metadata", cmd)

    def test_youtube_video_does_not_embed_thumbnail(self):
        cmd = bot.build_yt_dlp_command(
            "https://youtu.be/123", Path("/tmp/video"), "bv+ba", mode="video"
        )
        self.assertNotIn("--embed-thumbnail", cmd)
        self.assertNotIn("--convert-thumbnails", cmd)

    def test_video_command_keeps_existing_pipeline(self):
        cmd = bot.build_yt_dlp_command(
            "https://youtu.be/example", Path("/tmp/video"), "bv+ba", mode="video"
        )
        self.assertIn("--remux-video", cmd)
        self.assertNotIn("--extract-audio", cmd)
        self.assertIn("bv+ba", cmd)

    def test_mp3_uses_source_title_in_filename_and_embeds_metadata(self):
        cmd = bot.build_yt_dlp_command(
            "https://youtu.be/example", Path("/tmp/audio"), "unused", mode="mp3"
        )
        template = cmd[cmd.index("--output") + 1]
        self.assertEqual(template, "/tmp/audio/%(title).180B.%(ext)s")
        self.assertIn("--embed-metadata", cmd)
        self.assertNotIn("audio.%(ext)s", template)

    def test_video_uses_source_title_in_filename(self):
        cmd = bot.build_yt_dlp_command(
            "https://youtu.be/example", Path("/tmp/video"), "best", mode="video"
        )
        template = cmd[cmd.index("--output") + 1]
        self.assertEqual(template, "/tmp/video/%(title).180B.%(ext)s")
        self.assertNotIn("video.%(ext)s", template)


class ModeAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_chat_is_authorized(self):
        msg = SimpleNamespace(chat=SimpleNamespace(type="private"))
        self.assertTrue(await bot.is_chat_admin(msg, SimpleNamespace()))

    async def test_group_admin_only(self):
        msg = SimpleNamespace(
            chat=SimpleNamespace(type="supergroup"),
            chat_id=-100123,
            from_user=SimpleNamespace(id=42),
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(
                get_chat_administrators=AsyncMock(
                    return_value=[SimpleNamespace(user=SimpleNamespace(id=42))]
                )
            )
        )
        self.assertTrue(await bot.is_chat_admin(msg, context))
        msg.from_user.id = 99
        self.assertFalse(await bot.is_chat_admin(msg, context))

    async def test_anonymous_group_admin_fails_closed(self):
        msg = SimpleNamespace(
            chat=SimpleNamespace(type="supergroup"),
            chat_id=-100123, from_user=None,
        )
        self.assertFalse(await bot.is_chat_admin(msg, SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
