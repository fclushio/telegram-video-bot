#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) != 0 ]]; then
  echo 'Run as root: bash install.sh' >&2
  exit 1
fi
if [[ $(uname -m) != x86_64 ]]; then
  echo 'This install script currently supports x86_64 Debian 13 only.' >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ffmpeg ca-certificates curl unzip

# Deno for current YouTube JS challenges (yt-dlp-ejs comes from pip extras).
if ! command -v deno >/dev/null 2>&1; then
  td=$(mktemp -d)
  trap 'rm -rf "$td"' EXIT
  deno_archive=deno-x86_64-unknown-linux-gnu.zip
  curl --fail --location --silent --show-error \
    "https://github.com/denoland/deno/releases/latest/download/$deno_archive" \
    --output "$td/$deno_archive"
  curl --fail --location --silent --show-error \
    "https://github.com/denoland/deno/releases/latest/download/$deno_archive.sha256sum" \
    --output "$td/$deno_archive.sha256sum"
  (cd "$td" && sha256sum -c "$deno_archive.sha256sum")
  unzip -q "$td/$deno_archive" -d "$td"
  install -m 0755 "$td/deno" /usr/local/bin/deno
fi

auser=telegram-video-bot
if ! id "$auser" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/telegram-video-bot --create-home --shell /usr/sbin/nologin "$auser"
fi
install -d -m 0750 -o "$auser" -g "$auser" /var/lib/telegram-video-bot
install -d -m 0750 -o "$auser" -g "$auser" /var/lib/telegram-video-bot/jobs
install -d -m 0755 /opt/telegram-video-bot
cp -f bot.py url_helpers.py requirements.txt /opt/telegram-video-bot/
python3 -m venv /opt/telegram-video-bot/.venv
/opt/telegram-video-bot/.venv/bin/pip install --upgrade pip
/opt/telegram-video-bot/.venv/bin/pip install --upgrade -r /opt/telegram-video-bot/requirements.txt

if [[ ! -e /etc/telegram-video-bot.env ]]; then
  install -m 0600 telegram-video-bot.env.example /etc/telegram-video-bot.env
fi
chmod 0600 /etc/telegram-video-bot.env
install -m 0644 systemd/telegram-video-bot.service /etc/systemd/system/telegram-video-bot.service
systemctl daemon-reload
systemctl enable telegram-video-bot.service

echo
/opt/telegram-video-bot/.venv/bin/yt-dlp --version
deno --version | head -n 1 || true
echo 'Installed. Edit /etc/telegram-video-bot.env and then:'
echo '  systemctl restart telegram-video-bot'
echo '  journalctl -u telegram-video-bot -f'
