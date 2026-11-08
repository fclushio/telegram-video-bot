#!/usr/bin/env python3
"""Small Telegram yt-dlp responder (standard, hosted Telegram Bot API)."""

import asyncio
import json
import logging
import os
import signal
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from telegram import Message, ReplyParameters, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from url_helpers import DEFAULT_DOMAINS, csv_set, extract_urls, host_allowed

LOG = logging.getLogger("telegram_video_bot")

# A finite list is intentional: accepting URLs to any arbitrary host is unsafe
# when a service downloads remotely supplied resources from a homelab.
FORMATS = (
    ("720p", "bv[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=720][ext=mp4]/bv[height<=720]+ba/b[height<=720]/b"),
    ("480p", "bv[height<=480][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=480][ext=mp4]/bv[height<=480]+ba/b[height<=480]/b"),
    ("360p", "bv[height<=360][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=360][ext=mp4]/bv[height<=360]+ba/b[height<=360]/b"),
)

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID_CONFIG = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
ALLOW_ALL_CHATS = CHAT_ID_CONFIG == "*"
CHAT_IDS = set()
if not ALLOW_ALL_CHATS:
    for _id in csv_set(CHAT_ID_CONFIG):
        try:
            CHAT_IDS.add(int(_id))
        except ValueError as exc:
            raise SystemExit("ALLOWED_CHAT_IDS must contain numeric Telegram chat IDs or be '*'") from exc

DOMAINS = DEFAULT_DOMAINS | csv_set(os.environ.get("EXTRA_DOMAINS", ""))
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "47000000"))
DOWNLOAD_TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT_SECONDS", "240"))
MAX_PENDING = int(os.environ.get("MAX_PENDING", "3"))
WORK_DIR = Path(os.environ.get("WORK_DIR", "/var/lib/telegram-video-bot/jobs"))
AUDIT_LOG = Path(os.environ.get("AUDIT_LOG", "/var/lib/telegram-video-bot/downloads.jsonl"))
SEMAPHORE = asyncio.Semaphore(1)
_pending = 0


def permitted(message: Message) -> bool:
    return ALLOW_ALL_CHATS or message.chat_id in CHAT_IDS


def audit_event(message: Message, url: str, event: str, **details: object) -> None:
    """Record a download attempt/outcome in journald and an append-only JSONL history."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "url": url,
        **details,
    }
    encoded = json.dumps(record, ensure_ascii=True)
    LOG.info("video_audit %s", encoded)
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
    except OSError:
        LOG.exception("Failed to write video audit history")


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if msg := update.effective_message:
        user_id = msg.from_user.id if msg.from_user else "unknown"
        await msg.reply_text(f"Chat ID: {msg.chat_id}\nYour user ID: {user_id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not (msg := update.effective_message):
        return
    if not permitted(msg):
        await msg.reply_text("This chat is not authorized. Use /id to find its numeric ID.")
        return
    await msg.reply_text(
        "Send a supported video URL, use /dl <URL>, or reply /dl to a message containing a URL. "
        "One video per message; the upload limit is about 47 MB."
    )


async def run_yt_dlp(url: str, folder: Path, fmt: str) -> tuple[Path | None, str]:
    """Run an isolated, time-limited downloader without going through a shell."""
    folder.mkdir(parents=True, exist_ok=True)
    command = [
        str(Path(__file__).resolve().parent / ".venv" / "bin" / "yt-dlp"),
        "--ignore-config", "--no-playlist", "--playlist-items", "1",
        "--no-progress", "--no-warnings", "--quiet",
        "--max-filesize", "45M", "--socket-timeout", "20",
        "--retries", "2", "--fragment-retries", "2",
        "--merge-output-format", "mp4", "--remux-video", "mp4",
        "--format", fmt,
        "--output", str(folder / "video.%(ext)s"),
        "--print", "after_move:filepath",
        "--", url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        os.killpg(proc.pid, signal.SIGKILL)
        await proc.communicate()
        return None, "timeout"

    if proc.returncode:
        # We deliberately do not log URLs, stderr or query parameters, which
        # may contain personal information or site authentication tokens.
        error = stderr.decode("utf-8", errors="replace").lower()
        if "larger than max-filesize" in error or "exceeds max-filesize" in error:
            return None, "large"
        if "unsupported url" in error:
            return None, "unsupported"
        LOG.warning("yt-dlp failed: domain=%s returncode=%s", urlsplit(url).hostname, proc.returncode)
        return None, "failed"

    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        candidate = Path(line)
        if candidate.is_file() and candidate.resolve().is_relative_to(folder.resolve()):
            return candidate, "ok"
    return None, "failed"


async def perform_download(msg: Message, url: str) -> None:
    global _pending
    audit_event(msg, url, "requested")
    if _pending >= MAX_PENDING:
        audit_event(msg, url, "rejected", reason="queue_full")
        await msg.reply_text("Download queue is full. Try again after another video finishes.")
        return
    _pending += 1
    status: Message | None = None
    try:
        async with SEMAPHORE:
            status = await msg.reply_text("Downloading video…")
            with tempfile.TemporaryDirectory(prefix="job-", dir=WORK_DIR) as dirname:
                base = Path(dirname)
                reasons: list[str] = []
                for label, format_spec in FORMATS:
                    file, reason = await run_yt_dlp(url, base / label, format_spec)
                    reasons.append(reason)
                    if not file:
                        if reason in {"timeout", "unsupported"}:
                            break
                        # If extraction fails, trying different sizes usually won't help.
                        if reason == "failed":
                            break
                        continue
                    if file.stat().st_size > MAX_BYTES:
                        reasons.append("large")
                        continue
                    reply = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
                    with file.open("rb") as stream:
                        opts = dict(caption=None, reply_parameters=reply, read_timeout=240, write_timeout=240)
                        if file.suffix.lower() == ".mp4":
                            sent = await msg.reply_video(video=stream, supports_streaming=True, **opts)
                        else:
                            sent = await msg.reply_document(document=stream, filename=file.name, **opts)
                    audit_event(
                        msg, url, "uploaded", quality=label,
                        size_bytes=file.stat().st_size, telegram_message_id=sent.message_id,
                    )
                    if status:
                        try:
                            await status.delete()
                        except TelegramError:
                            LOG.warning("Could not remove progress message after successful upload")
                    return

                if "timeout" in reasons:
                    result_reason = "timeout"
                    response = "Download timed out."
                elif "large" in reasons:
                    result_reason = "large"
                    response = "This video is too large for the standard Telegram Bot API (~47 MB safe upload limit)."
                elif "unsupported" in reasons:
                    result_reason = "unsupported"
                    response = "This site or link type isn't supported by yt-dlp."
                else:
                    result_reason = "failed"
                    response = "Couldn't download this video. It may be private, geo-blocked, require a login, or the site may have changed."
                audit_event(msg, url, "failed", reason=result_reason)
                if status:
                    await status.edit_text(response)
    except TelegramError:
        LOG.exception("Telegram upload or reply failed")
        audit_event(msg, url, "failed", reason="telegram_error")
        if status:
            try:
                await status.edit_text("Downloaded the video, but Telegram couldn't accept the upload.")
            except TelegramError:
                pass
    except (OSError, ValueError):
        LOG.exception("Local download failure")
        audit_event(msg, url, "failed", reason="local_error")
        if status:
            try:
                await status.edit_text("The local downloader encountered an error. See service logs.")
            except TelegramError:
                pass
    finally:
        _pending -= 1


async def dl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not (msg := update.effective_message):
        return
    if not permitted(msg):
        await msg.reply_text("This chat is not authorized. Use /id to find its ID.")
        return
    urls = extract_urls(msg)
    source_message = msg
    if not urls and msg.reply_to_message:
        source_message = msg.reply_to_message
        urls = extract_urls(source_message)
    valid = next((u for u in urls if host_allowed(u, DOMAINS)), None)
    if not valid:
        await msg.reply_text("Reply /dl to a supported link, or send /dl https://... . Use EXTRA_DOMAINS to allow other sites.")
        return
    await perform_download(source_message, valid)


async def auto_detect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not (msg := update.effective_message) or not permitted(msg):
        return
    if msg.from_user and msg.from_user.is_bot:
        return
    if msg.text and msg.text.lstrip().startswith("/"):
        return
    urls = extract_urls(msg)
    if valid := next((u for u in urls if host_allowed(u, DOMAINS)), None):
        await perform_download(msg, valid)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx INFO logs can include Bot API request URLs (and thus the bot token).
    for noisy_logger in ("httpx", "httpcore", "telegram.request"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    if not TOKEN:
        raise SystemExit("BOT_TOKEN must be configured in /etc/telegram-video-bot.env")
    if MAX_BYTES < 1 or MAX_PENDING < 1 or DOWNLOAD_TIMEOUT < 1:
        raise SystemExit("Invalid size, queue, or timeout configuration")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not ALLOW_ALL_CHATS and not CHAT_IDS:
        LOG.warning("ALLOWED_CHAT_IDS is empty: only /id responds until you allow a chat")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler(["start", "help"], help_command))
    app.add_handler(CommandHandler("dl", dl_command, block=False))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, auto_detect, block=False))
    LOG.info(
        "Starting Telegram video bot; allowed chats: %s",
        "all" if ALLOW_ALL_CHATS else str(len(CHAT_IDS)),
    )
    app.run_polling(allowed_updates=["message"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
