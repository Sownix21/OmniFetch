#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/Sownix21/OmniFetch.git"
INSTALL_DIR="/opt/omnifetch-bot"
SERVICE="omnifetch"
SERVICE_USER="omnifetch"

green='\033[0;32m'; blue='\033[0;34m'; red='\033[0;31m'; reset='\033[0m'
say() { printf '%b\n' "${blue}🌌 $*${reset}"; }
ok() { printf '%b\n' "${green}✅ $*${reset}"; }
die() { printf '%b\n' "${red}❌ $*${reset}" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "Run this installer with sudo."
command -v apt-get >/dev/null || die "This installer currently supports Debian and Ubuntu (apt)."

prompt_secret() {
    local value="${BOT_TOKEN:-}"
    if [[ -z "$value" ]]; then
        [[ -r /dev/tty ]] || die "Set BOT_TOKEN when running non-interactively."
        read -r -s -p "🔑 Telegram bot token: " value </dev/tty
        printf '\n' >/dev/tty
    fi
    printf '%s' "$value"
}

prompt_admin() {
    local value="${ADMIN_ID:-}"
    if [[ -z "$value" ]]; then
        [[ -r /dev/tty ]] || die "Set ADMIN_ID when running non-interactively."
        read -r -p "👑 Numeric Telegram admin ID: " value </dev/tty
    fi
    [[ "$value" =~ ^[0-9]+$ ]] || die "ADMIN_ID must contain digits only."
    printf '%s' "$value"
}

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl ca-certificates nano nodejs
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10+ is required (use Ubuntu 22.04+ or Debian 12+)."

BOT_TOKEN_VALUE="$(prompt_secret)"
ADMIN_ID_VALUE="$(prompt_admin)"
[[ "$BOT_TOKEN_VALUE" == *:* ]] || die "The Telegram bot token does not look valid."

say "Installing OmniFetch from ${REPO_URL}"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -c safe.directory="$INSTALL_DIR" -C "$INSTALL_DIR" fetch --quiet origin
    git -c safe.directory="$INSTALL_DIR" -C "$INSTALL_DIR" pull --ff-only
elif [[ -e "$INSTALL_DIR" ]]; then
    die "$INSTALL_DIR already exists but is not a Git checkout. Move it aside and rerun."
else
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

say "Creating the Python environment"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet --upgrade pip wheel
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

if [[ -f "$INSTALL_DIR/.env" ]]; then cp -a "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"; fi
umask 077
printf 'BOT_TOKEN=%s\nADMIN_ID=%s\nMAX_UPLOAD_MB=49\nMAX_PLAYLIST_ITEMS=10\nMAX_CONCURRENT_DOWNLOADS=2\n' \
    "$BOT_TOKEN_VALUE" "$ADMIN_ID_VALUE" >"$INSTALL_DIR/.env"

install -m 0755 "$INSTALL_DIR/omnifetch-manager.sh" /usr/local/bin/omnifetch
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

say "Creating the systemd service"
cat >"/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=OmniFetch Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/bot.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=${INSTALL_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE"
ok "OmniFetch is installed and running. Type 'sudo omnifetch' for the management menu."
