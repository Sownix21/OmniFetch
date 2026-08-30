#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/Sownix21/OmniFetch.git"
INSTALL_DIR="/opt/omnifetch-bot"
BOT_SERVICE="omnifetch"
BOT_USER="omnifetch"
API_SERVICE="telegram-bot-api"
API_USER="telegram-bot-api"
API_SOURCE_DIR="/usr/local/src/telegram-bot-api"
API_DATA_DIR="/var/lib/telegram-bot-api"
API_ENV_FILE="/etc/omnifetch-bot-api.env"
API_URL="http://127.0.0.1:8081"

green='\033[0;32m'; blue='\033[0;34m'; red='\033[0;31m'; reset='\033[0m'
say() { printf '%b\n' "${blue}🌌 $*${reset}"; }
ok() { printf '%b\n' "${green}✅ $*${reset}"; }
die() { printf '%b\n' "${red}❌ $*${reset}" >&2; exit 1; }
TEMP_FILES=()
TEMP_DIRS=()
RESTORE_BOT_ON_FAILURE=false
cleanup_sensitive_temp() {
    local exit_status="$?" file directory
    for file in "${TEMP_FILES[@]}"; do rm -f -- "$file"; done
    for directory in "${TEMP_DIRS[@]}"; do rm -rf -- "$directory"; done
    if [[ "$exit_status" -ne 0 && "$RESTORE_BOT_ON_FAILURE" == true ]]; then
        systemctl start "$BOT_SERVICE" >/dev/null 2>&1 || true
    fi
}
trap cleanup_sensitive_temp EXIT

[[ ${EUID} -eq 0 ]] || die "Run this installer with sudo."
command -v apt-get >/dev/null || die "This installer supports Ubuntu 22.04+ and Debian 12+."

read_key() {
    local file="$1" key="$2"
    [[ -f "$file" ]] || return 0
    sed -n "s/^${key}=//p" "$file" | head -n 1
}

prompt_value() {
    local current="$1" prompt="$2" secret="${3:-false}"
    if [[ -z "$current" ]]; then
        [[ -r /dev/tty ]] || die "Missing required value: $prompt"
        if [[ "$secret" == true ]]; then
            read -r -s -p "$prompt: " current </dev/tty; printf '\n' >/dev/tty
        else
            read -r -p "$prompt: " current </dev/tty
        fi
    fi
    printf '%s' "$current"
}

EXISTING_BOT_TOKEN="$(read_key "$INSTALL_DIR/.env" BOT_TOKEN)"
EXISTING_ADMIN_ID="$(read_key "$INSTALL_DIR/.env" ADMIN_ID)"
EXISTING_API_ID="$(read_key "$API_ENV_FILE" TELEGRAM_API_ID)"
EXISTING_API_HASH="$(read_key "$API_ENV_FILE" TELEGRAM_API_HASH)"

BOT_TOKEN_VALUE="$(prompt_value "${BOT_TOKEN:-$EXISTING_BOT_TOKEN}" "🔑 Telegram bot token" true)"
ADMIN_ID_VALUE="$(prompt_value "${ADMIN_ID:-$EXISTING_ADMIN_ID}" "👑 Numeric Telegram admin ID")"
API_ID_VALUE="$(prompt_value "${TELEGRAM_API_ID:-$EXISTING_API_ID}" "🪪 Telegram API ID from my.telegram.org")"
API_HASH_VALUE="$(prompt_value "${TELEGRAM_API_HASH:-$EXISTING_API_HASH}" "🔐 Telegram API hash from my.telegram.org" true)"

[[ "$BOT_TOKEN_VALUE" =~ ^[0-9]+:[A-Za-z0-9_-]{20,}$ ]] || die "BOT_TOKEN does not look valid."
[[ "$ADMIN_ID_VALUE" =~ ^[0-9]+$ ]] || die "ADMIN_ID must contain digits only."
[[ "$API_ID_VALUE" =~ ^[0-9]+$ ]] || die "TELEGRAM_API_ID must contain digits only."
[[ "$API_HASH_VALUE" =~ ^[A-Fa-f0-9]{32}$ ]] || die "TELEGRAM_API_HASH must be exactly 32 hexadecimal characters."

say "Installing runtime and official Bot API build dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl ca-certificates nano unzip \
    cmake g++ make gperf zlib1g-dev libssl-dev
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || \
    die "Python 3.10+ is required."

deno_version="$(deno --version 2>/dev/null | awk 'NR == 1 {print $2}' || true)"
if [[ -z "$deno_version" || "$(printf '%s\n' 2.3.0 "$deno_version" | sort -V | head -n 1)" != "2.3.0" ]]; then
    say "Installing a checksum-verified Deno runtime for current yt-dlp extractors"
    case "$(uname -m)" in
        x86_64|amd64) deno_target="x86_64-unknown-linux-gnu" ;;
        aarch64|arm64) deno_target="aarch64-unknown-linux-gnu" ;;
        *) die "Deno automatic installation supports x86_64 and ARM64 VPS hosts." ;;
    esac
    deno_tag="$(curl -fsSL https://api.github.com/repos/denoland/deno/releases/latest | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
    [[ "$deno_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Could not determine a trusted Deno release version."
    deno_archive="deno-${deno_target}.zip"
    deno_temp="$(mktemp -d)"; TEMP_DIRS+=("$deno_temp")
    curl -fsSL "https://github.com/denoland/deno/releases/download/${deno_tag}/${deno_archive}" -o "$deno_temp/$deno_archive"
    curl -fsSL "https://github.com/denoland/deno/releases/download/${deno_tag}/${deno_archive}.sha256sum" -o "$deno_temp/${deno_archive}.sha256sum"
    (cd "$deno_temp" && sha256sum -c "${deno_archive}.sha256sum") || die "Deno checksum verification failed."
    unzip -q "$deno_temp/$deno_archive" -d "$deno_temp/unpacked"
    install -m 0755 "$deno_temp/unpacked/deno" /usr/local/bin/deno
fi
deno --version >/dev/null || die "Deno installation failed."

say "Installing OmniFetch from ${REPO_URL}"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    systemctl is-active --quiet "$BOT_SERVICE" 2>/dev/null && RESTORE_BOT_ON_FAILURE=true
    systemctl stop "$BOT_SERVICE" 2>/dev/null || true
    git -c safe.directory="$INSTALL_DIR" -C "$INSTALL_DIR" fetch --quiet origin
    git -c safe.directory="$INSTALL_DIR" -C "$INSTALL_DIR" pull --ff-only
elif [[ -e "$INSTALL_DIR" ]]; then
    die "$INSTALL_DIR exists but is not a Git checkout. Move it aside and rerun."
else
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

if ! id "$BOT_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$BOT_USER"
fi
if ! id "$API_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$API_DATA_DIR" --shell /usr/sbin/nologin "$API_USER"
fi

say "Building the official Telegram Bot API server (this can take several minutes)"
install -d -m 0755 /usr/local/src
if [[ -d "$API_SOURCE_DIR/.git" ]]; then
    git -c safe.directory="$API_SOURCE_DIR" -C "$API_SOURCE_DIR" fetch --quiet origin
    git -c safe.directory="$API_SOURCE_DIR" -C "$API_SOURCE_DIR" pull --ff-only
    git -c safe.directory="$API_SOURCE_DIR" -C "$API_SOURCE_DIR" submodule update --init --recursive
elif [[ -e "$API_SOURCE_DIR" ]]; then
    die "$API_SOURCE_DIR exists but is not a Git checkout. Move it aside and rerun."
else
    git clone --recursive --depth 1 --shallow-submodules https://github.com/tdlib/telegram-bot-api.git "$API_SOURCE_DIR"
fi
cmake -S "$API_SOURCE_DIR" -B "$API_SOURCE_DIR/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build "$API_SOURCE_DIR/build" --target install --parallel "${BOT_API_BUILD_JOBS:-2}"
[[ -x /usr/local/bin/telegram-bot-api ]] || die "Telegram Bot API build completed without installing the binary."

say "Creating isolated service storage and credentials"
install -d -m 0700 -o "$API_USER" -g "$API_USER" "$API_DATA_DIR" "$API_DATA_DIR/tmp"
umask 077
printf 'TELEGRAM_API_ID=%s\nTELEGRAM_API_HASH=%s\n' "$API_ID_VALUE" "$API_HASH_VALUE" >"$API_ENV_FILE"
chown root:root "$API_ENV_FILE"; chmod 600 "$API_ENV_FILE"

cat >"/etc/systemd/system/${API_SERVICE}.service" <<EOF
[Unit]
Description=Telegram Local Bot API for OmniFetch
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${API_USER}
Group=${API_USER}
EnvironmentFile=${API_ENV_FILE}
ExecStart=/usr/local/bin/telegram-bot-api --local --http-ip-address=127.0.0.1 --http-port=8081 --dir=${API_DATA_DIR} --temp-dir=${API_DATA_DIR}/tmp
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
LimitNOFILE=65536
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths=${API_DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF

say "Creating Python environment"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet --upgrade pip wheel
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

if [[ -f "$INSTALL_DIR/.env" ]]; then
    cp -a "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"
    chmod 600 "$INSTALL_DIR/.env.backup"
fi
OLD_ENV="$INSTALL_DIR/.env.backup"
NEW_ENV="$INSTALL_DIR/.env.new"
printf 'BOT_TOKEN=%s\nADMIN_ID=%s\nBOT_API_URL=%s\nMAX_UPLOAD_MB=1900\nMAX_DOWNLOAD_MB=1900\n' \
    "$BOT_TOKEN_VALUE" "$ADMIN_ID_VALUE" "$API_URL" >"$NEW_ENV"
copy_or_default() {
    local key="$1" default="$2" value
    value="$(read_key "$OLD_ENV" "$key")"
    printf '%s=%s\n' "$key" "${value:-$default}" >>"$NEW_ENV"
}
copy_or_default MAX_PLAYLIST_ITEMS 10
copy_or_default MAX_CONCURRENT_DOWNLOADS 2
copy_or_default REQUEST_TIMEOUT 20
copy_or_default TRANSFER_TIMEOUT 3600
copy_or_default MIN_FREE_DISK_MB 2048
copy_or_default LOG_LEVEL INFO
copy_or_default GITHUB_TOKEN ''
copy_or_default COOKIES_FILE ''
mv "$NEW_ENV" "$INSTALL_DIR/.env"
chown -R "$BOT_USER:$BOT_USER" "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env"
install -m 0755 "$INSTALL_DIR/omnifetch-manager.sh" /usr/local/bin/omnifetch

cat >"/etc/systemd/system/${BOT_SERVICE}.service" <<EOF
[Unit]
Description=OmniFetch Telegram Bot
After=network-online.target ${API_SERVICE}.service
Wants=network-online.target
Requires=${API_SERVICE}.service

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_USER}
WorkingDirectory=${INSTALL_DIR}
Environment=DENO_DIR=${INSTALL_DIR}/.deno-cache
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/bot.py
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=60
LimitNOFILE=8192
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths=${INSTALL_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$API_SERVICE" "$BOT_SERVICE"

BOT_ID="${BOT_TOKEN_VALUE%%:*}"
MIGRATION_MARKER="$API_DATA_DIR/.omnifetch-migrated-${BOT_ID}"
if [[ ! -f "$MIGRATION_MARKER" ]]; then
    say "Migrating the bot from Telegram's hosted API to the private local API"
    systemctl stop "$BOT_SERVICE" 2>/dev/null || true
    logout_config="$(mktemp)"; TEMP_FILES+=("$logout_config"); chmod 600 "$logout_config"
    printf 'url = "https://api.telegram.org/bot%s/logOut"\nrequest = "POST"\nsilent\nshow-error\nfail\n' "$BOT_TOKEN_VALUE" >"$logout_config"
    logout_response="$(curl --config "$logout_config")" || { rm -f "$logout_config"; die "Telegram logOut migration failed."; }
    rm -f "$logout_config"
    [[ "$logout_response" == *'"ok":true'* ]] || die "Telegram rejected the hosted-to-local migration: $logout_response"
fi

systemctl restart "$API_SERVICE"
for _ in $(seq 1 30); do
    if curl -sS --max-time 2 "$API_URL" >/dev/null 2>&1; then break; fi
    sleep 1
done
systemctl is-active --quiet "$API_SERVICE" || die "Local Bot API service failed. Run: journalctl -u $API_SERVICE"

health_config="$(mktemp)"; TEMP_FILES+=("$health_config"); chmod 600 "$health_config"
printf 'url = "%s/bot%s/getMe"\nsilent\nshow-error\nfail\n' "$API_URL" "$BOT_TOKEN_VALUE" >"$health_config"
for _ in $(seq 1 30); do
    health_response="$(curl --config "$health_config" 2>/dev/null || true)"
    [[ "$health_response" == *'"ok":true'* ]] && break
    sleep 2
done
rm -f "$health_config"
[[ "${health_response:-}" == *'"ok":true'* ]] || die "Local Bot API started but could not authenticate the bot. Check API ID/hash and service logs."
touch "$MIGRATION_MARKER"; chown "$API_USER:$API_USER" "$MIGRATION_MARKER"

systemctl restart "$BOT_SERVICE"
systemctl is-active --quiet "$BOT_SERVICE" || die "OmniFetch failed to start. Run: journalctl -u $BOT_SERVICE"
RESTORE_BOT_ON_FAILURE=false
ok "OmniFetch is running through a loopback-only local Bot API with intact uploads up to 1900 MB."
ok "Use 'sudo omnifetch' for management."
