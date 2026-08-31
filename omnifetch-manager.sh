#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/omnifetch-bot"
BOT_SERVICE="omnifetch"
API_SERVICE="telegram-bot-api"
API_ENV_FILE="/etc/omnifetch-bot-api.env"
API_SOURCE_DIR="/usr/local/src/telegram-bot-api"
API_DATA_DIR="/var/lib/telegram-bot-api"
DEFAULT_API_URL="http://127.0.0.1:18081"

if [[ ${EUID} -ne 0 ]]; then exec sudo "$0" "$@"; fi
pause() { read -r -p "Press Enter to continue…" </dev/tty || true; }
TEMP_FILES=()
cleanup_sensitive_temp() {
    local file
    for file in "${TEMP_FILES[@]}"; do rm -f -- "$file"; done
}
trap cleanup_sensitive_temp EXIT
read_key() {
    local file="$1" key="$2"
    [[ -f "$file" ]] || return 0
    sed -n "s/^${key}=//p" "$file" | head -n 1
}
check_install() {
    [[ -d "$INSTALL_DIR/.git" \
        && -f "/etc/systemd/system/${BOT_SERVICE}.service" \
        && -f "/etc/systemd/system/${API_SERVICE}.service" \
        && -x /usr/local/bin/telegram-bot-api ]] || {
        echo "❌ OmniFetch or its private Bot API service is not fully installed."
        echo "Run: curl -fsSL https://raw.githubusercontent.com/Sownix21/OmniFetch/main/install.sh | sudo bash"
        exit 1
    }
}

wait_for_api() {
    local token response curl_config api_url
    token="$(read_key "$INSTALL_DIR/.env" BOT_TOKEN)"
    api_url="$(read_key "$INSTALL_DIR/.env" BOT_API_URL)"
    api_url="${api_url:-$DEFAULT_API_URL}"
    [[ -n "$token" ]] || { echo "❌ BOT_TOKEN is missing."; return 1; }
    curl_config="$(mktemp)"; TEMP_FILES+=("$curl_config"); chmod 600 "$curl_config"
    printf 'url = "%s/bot%s/getMe"\nsilent\nshow-error\n' "$api_url" "$token" >"$curl_config"
    for _ in $(seq 1 120); do
        response="$(curl --config "$curl_config" 2>&1 || true)"
        if [[ "$response" == *'"ok":true'* ]]; then rm -f "$curl_config"; return 0; fi
        sleep 2
    done
    rm -f "$curl_config"
    response="${response//$token/[BOT_TOKEN_REDACTED]}"
    echo "❌ The private Bot API did not become ready: ${response:-no response}"
    echo "Inspect: sudo omnifetch api-logs"
    return 1
}

start_all() {
    check_install
    systemctl start "$API_SERVICE"
    wait_for_api
    systemctl start "$BOT_SERVICE"
}

stop_all() {
    check_install
    systemctl stop "$BOT_SERVICE"
    systemctl stop "$API_SERVICE"
}

restart_all() {
    check_install
    systemctl restart "$API_SERVICE"
    wait_for_api
    systemctl restart "$BOT_SERVICE"
}

status_all() {
    check_install
    systemctl status "$API_SERVICE" "$BOT_SERVICE" --no-pager || true
}

api_logs() {
    check_install
    local file_log="$API_DATA_DIR/telegram-bot-api.log"
    if [[ -f "$file_log" ]]; then
        echo "📜 Following $file_log (Ctrl+C to return)"
        tail -n 100 -F "$file_log"
    else
        journalctl -u "$API_SERVICE" -f
    fi
}

update_bot() {
    check_install
    echo "⬇️ Updating OmniFetch"
    deno upgrade
    systemctl stop "$BOT_SERVICE"
    if ! git -c safe.directory="$INSTALL_DIR" -C "$INSTALL_DIR" pull --ff-only; then
        systemctl start "$BOT_SERVICE"; echo "❌ Git update failed; the existing bot was restarted."; return 1
    fi
    if ! "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"; then
        systemctl start "$BOT_SERVICE"; echo "❌ Dependency update failed; the existing bot was restarted."; return 1
    fi
    install -m 0755 "$INSTALL_DIR/omnifetch-manager.sh" /usr/local/bin/omnifetch
    chown -R omnifetch:omnifetch "$INSTALL_DIR"
    chmod 600 "$INSTALL_DIR/.env"
    systemctl restart "$BOT_SERVICE"
    echo "✅ Updated and restarted."
}

update_api() {
    check_install
    echo "⬇️ Updating and rebuilding the official Telegram Bot API"
    git -c safe.directory="$API_SOURCE_DIR" -C "$API_SOURCE_DIR" pull --ff-only
    git -c safe.directory="$API_SOURCE_DIR" -C "$API_SOURCE_DIR" submodule update --init --recursive
    cmake -S "$API_SOURCE_DIR" -B "$API_SOURCE_DIR/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local
    cmake --build "$API_SOURCE_DIR/build" --target install --parallel "${BOT_API_BUILD_JOBS:-2}"
    restart_all
    echo "✅ Local Bot API updated and both services restarted."
}

edit_bot_config() {
    check_install
    local backup
    backup="$(mktemp)"; TEMP_FILES+=("$backup"); cp -a "$INSTALL_DIR/.env" "$backup"
    "${EDITOR:-nano}" "$INSTALL_DIR/.env"
    chown omnifetch:omnifetch "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    if ! (cd "$INSTALL_DIR" && "$INSTALL_DIR/venv/bin/python" -c 'import bot'); then
        cp -a "$backup" "$INSTALL_DIR/.env"
        echo "❌ Invalid bot configuration; the previous file was restored."
        return 1
    fi
    systemctl restart "$BOT_SERVICE"
    sleep 2
    systemctl is-active --quiet "$BOT_SERVICE" || {
        cp -a "$backup" "$INSTALL_DIR/.env"
        chown omnifetch:omnifetch "$INSTALL_DIR/.env"; chmod 600 "$INSTALL_DIR/.env"
        systemctl restart "$BOT_SERVICE"
        echo "❌ The bot did not stay active; the previous configuration was restored."
        return 1
    }
}

edit_api_config() {
    check_install
    local backup api_id api_hash
    backup="$(mktemp)"; TEMP_FILES+=("$backup"); cp -a "$API_ENV_FILE" "$backup"
    "${EDITOR:-nano}" "$API_ENV_FILE"
    api_id="$(read_key "$API_ENV_FILE" TELEGRAM_API_ID)"
    api_hash="$(read_key "$API_ENV_FILE" TELEGRAM_API_HASH)"
    if [[ ! "$api_id" =~ ^[0-9]+$ || ! "$api_hash" =~ ^[A-Fa-f0-9]{32}$ ]]; then
        cp -a "$backup" "$API_ENV_FILE"
        echo "❌ Invalid API ID/hash; the previous credential file was restored."
        return 1
    fi
    chown root:root "$API_ENV_FILE"
    chmod 600 "$API_ENV_FILE"
    if ! restart_all; then
        cp -a "$backup" "$API_ENV_FILE"
        chown root:root "$API_ENV_FILE"; chmod 600 "$API_ENV_FILE"
        restart_all || true
        echo "❌ The new API credentials failed; the previous credentials were restored."
        return 1
    fi
}

health_check() {
    check_install
    "$INSTALL_DIR/venv/bin/python" -m py_compile "$INSTALL_DIR/bot.py"
    deno --version | head -n 1
    systemctl is-active --quiet "$API_SERVICE" || { echo "❌ $API_SERVICE is not active."; return 1; }
    systemctl is-active --quiet "$BOT_SERVICE" || { echo "❌ $BOT_SERVICE is not active."; return 1; }
    wait_for_api
    df -h "$INSTALL_DIR" "$API_SOURCE_DIR" | awk 'NR == 1 || !seen[$6]++'
    echo "✅ Python, both services, local authentication, and storage checks passed."
}

uninstall_all() {
    check_install
    local confirmation
    cat <<'WARNING'
⚠️  FULL OMNIFETCH UNINSTALL

This permanently removes:
  • the bot and private Bot API services
  • /opt/omnifetch-bot, including its database and temporary downloads
  • private API credentials and all local Bot API session/data files
  • the compiled Bot API binary/source, Linux manager, and service users

Shared packages and runtimes (Python, FFmpeg, Git, CMake, and Deno) are kept.
This action cannot be undone unless you have your own backup.
WARNING
    [[ -r /dev/tty ]] || { echo "❌ Uninstall requires an interactive terminal."; return 1; }
    read -r -p "Type UNINSTALL to permanently continue: " confirmation </dev/tty
    if [[ "$confirmation" != "UNINSTALL" ]]; then
        echo "✅ Uninstall cancelled; nothing was removed."
        return 0
    fi
    [[ "$(realpath -m "$INSTALL_DIR")" == /opt/omnifetch-bot \
        && "$(realpath -m "$API_SOURCE_DIR")" == /usr/local/src/telegram-bot-api \
        && "$(realpath -m "$API_DATA_DIR")" == /var/lib/telegram-bot-api ]] || {
        echo "❌ Safety check failed; no files were removed."
        return 1
    }

    echo "⏹️ Stopping and disabling OmniFetch services"
    systemctl disable --now "$BOT_SERVICE" "$API_SERVICE" 2>/dev/null || true

    rm -f -- \
        "/etc/systemd/system/${BOT_SERVICE}.service" \
        "/etc/systemd/system/${API_SERVICE}.service" \
        "$API_ENV_FILE" \
        /usr/local/bin/telegram-bot-api \
        /usr/local/bin/omnifetch
    rm -rf -- "$INSTALL_DIR" "$API_SOURCE_DIR" "$API_DATA_DIR"

    id omnifetch >/dev/null 2>&1 && userdel omnifetch || true
    id telegram-bot-api >/dev/null 2>&1 && userdel telegram-bot-api || true
    systemctl daemon-reload
    systemctl reset-failed "$BOT_SERVICE" "$API_SERVICE" 2>/dev/null || true
    echo "✅ OmniFetch and its private Telegram Bot API were completely removed."
}

show_menu() {
    check_install
    while true; do
        clear
        cat <<'MENU'
╭─────────────────────────────────────────╮
│          🌌 OmniFetch Manager 🌌         │
├─────────────────────────────────────────┤
│  1  ▶️  Start bot + private API          │
│  2  ⏹️  Stop bot + private API           │
│  3  🔄 Restart both services            │
│  4  📊 Show both service statuses       │
│  5  📜 Follow bot logs                  │
│  6  🛰️  Follow local API logs           │
│  7  🔧 Edit bot configuration           │
│  8  🔑 Edit private API credentials     │
│  9  ⬆️  Update bot + dependencies       │
│ 10  🧱 Update official local Bot API    │
│ 11  🩺 Run complete health check        │
│ 12  🗑️  Fully uninstall OmniFetch       │
│ 13  🚪 Exit                             │
╰─────────────────────────────────────────╯
MENU
        read -r -p "Choose [1-13]: " choice </dev/tty
        case "$choice" in
            1) start_all; echo "✅ Started both services."; sleep 1 ;;
            2) stop_all; echo "✅ Stopped both services."; sleep 1 ;;
            3) restart_all; echo "✅ Restarted both services."; sleep 1 ;;
            4) status_all; pause ;;
            5) journalctl -u "$BOT_SERVICE" -f || true ;;
            6) api_logs || true ;;
            7) edit_bot_config || true; pause ;;
            8) edit_api_config || true; pause ;;
            9) update_bot; pause ;;
            10) update_api; pause ;;
            11) health_check || true; pause ;;
            12) uninstall_all || true; [[ -d "$INSTALL_DIR" ]] || exit 0; pause ;;
            13) exit 0 ;;
            *) echo "⚠️ Choose a number from 1 to 13."; sleep 1 ;;
        esac
    done
}

case "${1:-menu}" in
    start) start_all ;;
    stop) stop_all ;;
    restart) restart_all ;;
    status) status_all ;;
    logs) check_install; journalctl -u "$BOT_SERVICE" -f ;;
    api-logs) api_logs ;;
    update) update_bot ;;
    update-api) update_api ;;
    config) edit_bot_config ;;
    api-config) edit_api_config ;;
    health) health_check ;;
    uninstall) uninstall_all ;;
    menu) show_menu ;;
    *) echo "Usage: omnifetch [start|stop|restart|status|logs|api-logs|update|update-api|config|api-config|health|uninstall]"; exit 2 ;;
esac
