# Telegram video responder — Debian 13 Proxmox LXC

A minimal long-polling Telegram bot. It sees URLs from approved sites in an allowed chat, runs `yt-dlp`, then replies to the originating message with the video. Also supports `/dl URL` and replying `/dl` to a message containing a URL. In the latter case it replies with the video to the **original URL message**.

## Before deploying

* Use a dedicated **unprivileged** Debian 13 LXC, x86_64, 1 vCPU, 1 GB RAM, 16–20 GB disk, outbound HTTPS/DNS. No inbound access or port forwarding needed.
* Only download content you have permission to access and redistribute.
* The default Telegram Bot API limits video/document uploads to **50 MB**; the bot uses a conservative **47,000,000-byte** cutoff. It tries 720p, then 480p, then 360p if the files are too large. It does not transcode/recompress.
* YouTube requires a current JS runtime and EJS solver: the installer supplies Deno and `yt-dlp[default]`.

## Step 1: Telegram

1. Open `@BotFather` and send `/newbot`; save the bot token.
2. For automatic **group** processing, send `/setprivacy`, select the bot, **Disable**. If it was already in the group, remove and re-add it. Manual commands can work with privacy enabled.
3. Add your bot to the group. It does not need to be a group admin simply to read messages with privacy disabled, as long as group permissions permit its messages.

## Step 2: Install

Copy this folder into the LXC (for example `scp -r telegram-video-bot root@LXC_IP:/root/`) and then:

```bash
cd /root/telegram-video-bot
bash install.sh
nano /etc/telegram-video-bot.env
```

Replace `YOUR_BOT_TOKEN_HERE` in `BOT_TOKEN`. Leave `ALLOWED_CHAT_IDS` empty for your first run (only `/id` works without an allowlist). To enable downloads in **all** chats, set `ALLOWED_CHAT_IDS=*` instead. This allows anyone who can message or add the bot to a group to initiate downloads, so use it only if that is acceptable.

```bash
systemctl start telegram-video-bot
journalctl -u telegram-video-bot -f
```

Send `/id` in the intended Telegram group. The bot returns a numeric **Chat ID** (often `-100...`). Set `ALLOWED_CHAT_IDS` in the env file, optionally adding your private DM ID, comma-separated:

```ini
ALLOWED_CHAT_IDS=-1001234567890,123456789
```

Then restart:

```bash
systemctl restart telegram-video-bot
```

Send an ordinary short YouTube URL, `/dl https://...`, or reply `/dl` to a message with a supported URL.

## Changing audio/video modes from Telegram

Each group or private chat starts in **video** mode. Use the following commands to configure that chat or override it for a single link:

| Command | Behavior |
| --- | --- |
| `/mode` | Display this chat's current default |
| `/mode mp3` | Switch automatic links in **this chat** to MP3-only |
| `/mode video` | Return to normal video downloads |
| `/dl URL` | Download with the chat's default mode |
| `/mp3 URL` | Download MP3 once without changing the chat's default |
| `/video URL` | Download video once without changing the chat's default |

`/dl`, `/mp3` and `/video` also work when sent as a reply to a message containing a supported video URL.

**Permissions:** Only Telegram group administrators may change a group's default mode, but any authorized chat member may use the one-off download commands. In private chats, each user controls their own default. Group admins posting anonymously cannot be verified and should temporarily post the mode command as themselves.

MP3 uses yt-dlp and FFmpeg to extract and transcode the best available audio stream into an MP3 (audio-quality setting `5`, VBR). It is sent through Telegram's music player using `sendAudio`, **not** as a voice message. MP3 and video filenames are based on the source media title (limited to 180 UTF-8 bytes and sanitized by yt-dlp), instead of generic `audio.mp3` / `video.mp4`. MP3 metadata tags are embedded during extraction, and the Telegram audio player's title uses the downloaded filename stem. The upload must stay below ~47 MB. Note that the original source audio has a 45 MiB download limit, so very long recordings might fail even if the converted MP3 would be smaller. Like videos, the MP3 files are deleted from the LXC after uploading.

The per-chat settings are stored in `/var/lib/telegram-video-bot/chat-modes.sqlite3` (override with `MODE_DB` in the env file). They persist across restarts and reinstallations, and one group's mode does not affect other groups or private chats. Back up this SQLite file if migrating to another LXC.

## Management

```bash
systemctl status telegram-video-bot
journalctl -u telegram-video-bot -n 100 --no-pager
systemctl restart telegram-video-bot
```

To update the application, replace the files in `/root/telegram-video-bot` and run `bash install.sh` again. The installer does **not** overwrite `/etc/telegram-video-bot.env`.

To update only the downloader, run:

```bash
/opt/telegram-video-bot/.venv/bin/pip install -U 'yt-dlp[default]'
systemctl restart telegram-video-bot
```

## Download history

Downloaded files are temporary and are deleted after each job, even if the upload fails. By default they are created beneath `/var/lib/telegram-video-bot/jobs/`.

The bot logs each accepted request and its eventual outcome to both systemd's journal and an append-only JSONL history:

```bash
# Live journal (each record includes the chat ID, Telegram source message ID and URL)
journalctl -u telegram-video-bot -f

# Persistent JSONL file, readable by root (or the service user)
tail -f /var/lib/telegram-video-bot/downloads.jsonl

# Show historical URLs and chat IDs with jq (install jq if necessary)
jq -r 'select(.event == "requested") | [.timestamp, .chat_id, .url] | @tsv' /var/lib/telegram-video-bot/downloads.jsonl
```

Events: `requested`, `rejected` (queue full), `uploaded` (with selected mode, quality, size and Telegram output message ID), and `failed` (with reason). Change the history path with `AUDIT_LOG` in `/etc/telegram-video-bot.env`. The existing credentials file is preserved during installation.

**Privacy:** These logs include the entire URL, including query parameters that may contain personal or access tokens. Treat the history as sensitive and restrict access. The systemd service uses `UMask=0077`, so newly created history files are accessible only to the service account; root can inspect them too. The JSONL file is not automatically rotated, so manage retention if the bot becomes busy.

## Behavior and limitations

* One URL per source message; in video mode `.mp4` is sent as an inline Telegram video, another format as a document. In MP3 mode audio is sent via Telegram's music player.
* Download jobs run one at a time with at most three outstanding jobs, to avoid excessive RAM/disk use. Files are deleted after upload, including failed downloads. Playlists are restricted to one entry.
* Only chat IDs listed in `ALLOWED_CHAT_IDS` can trigger downloads, unless it is set to `*` (all groups and private chats). A blank value disables downloads; everyone can use `/id` to look up their chat ID. For a public bot, prefer the explicit allowlist to prevent uncontrolled download jobs.
* Automatic and manual URLs must match `DEFAULT_DOMAINS` (in `bot.py`) or `EXTRA_DOMAINS` (in env). This is intentional; `yt-dlp` has a generic extractor and it is unsafe to blindly fetch every link submitted to a bot inside a homelab. Add sites explicitly to `EXTRA_DOMAINS`.
* Some sites require authenticated cookies or break extractors. This first version intentionally does **not** load browser cookies or accept arbitrary user-supplied proxy/cookie options.
* There is no Telegram file-ID cache, auto bitrate recompression, or self-hosted Bot API yet. They are natural later improvements.
* `drop_pending_updates=True` means that URLs posted while the bot is offline are not downloaded when the service restarts.
* Group chat access is restricted by chat ID, not by the identity of the member who posts the link.

## Testing

Inside a Python environment with dependencies installed:

```bash
python -m unittest discover -s tests -v
```
