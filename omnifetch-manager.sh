#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/omnifetch-bot"
SERVICE="omnifetch"

if [[ ${EUID} -ne 0 ]]; then exec sudo "$0" "$@"; fi
pause() { read -r -p "Press Enter to continue…" </dev/tty || true; }
check_install() {
    [[ -d "$INSTALL_DIR/.git" && -f "/etc/systemd/system/${SERVICE}.service" ]] || {
        echo "❌ OmniFetch is not installed."
        echo "Run: curl -fsSL https://raw.githubusercontent.com/Sownix21/OmniFetch/main/install.sh | sudo bash"
        exit 1
    }
}

update_bot() {
    check_install
    echo "⬇️ Updating OmniFetch"
    git -c safe.directory="$INSTALL_DIR" -C "$INSTALL_DIR" pull --ff-only
    "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
    install -m 0755 "$INSTALL_DIR/omnifetch-manager.sh" /usr/local/bin/omnifetch
    chown -R omnifetch:omnifetch "$INSTALL_DIR"
    systemctl restart "$SERVICE"
    echo "✅ Updated and restarted."
}

edit_config() {
    check_install
    "${EDITOR:-nano}" "$INSTALL_DIR/.env"
    chown omnifetch:omnifetch "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    systemctl restart "$SERVICE"
}

show_menu() {
    check_install
    while true; do
        clear
        cat <<'MENU'
╭────────────────────────────────────╮
│       🌌 OmniFetch Manager 🌌       │
├────────────────────────────────────┤
│  1  ▶️  Start bot                   │
│  2  ⏹️  Stop bot                    │
│  3  🔄 Restart bot                 │
│  4  📊 Service status              │
│  5  📜 Follow live logs            │
│  6  🔐 Edit configuration          │
│  7  ⬆️  Update bot + dependencies  │
│  8  🩺 Run health check            │
│  9  🚪 Exit                        │
╰────────────────────────────────────╯
MENU
        read -r -p "Choose [1-9]: " choice </dev/tty
        case "$choice" in
            1) systemctl start "$SERVICE"; echo "✅ Started."; sleep 1 ;;
            2) systemctl stop "$SERVICE"; echo "✅ Stopped."; sleep 1 ;;
            3) systemctl restart "$SERVICE"; echo "✅ Restarted."; sleep 1 ;;
            4) systemctl status "$SERVICE" --no-pager || true; pause ;;
            5) journalctl -u "$SERVICE" -f || true ;;
            6) edit_config ;;
            7) update_bot; pause ;;
            8) "$INSTALL_DIR/venv/bin/python" -m py_compile "$INSTALL_DIR/bot.py" && systemctl is-active "$SERVICE"; pause ;;
            9) exit 0 ;;
            *) echo "⚠️ Choose a number from 1 to 9."; sleep 1 ;;
        esac
    done
}

case "${1:-menu}" in
    start|stop|restart) check_install; systemctl "$1" "$SERVICE" ;;
    status) check_install; systemctl status "$SERVICE" --no-pager ;;
    logs) check_install; journalctl -u "$SERVICE" -f ;;
    update) update_bot ;;
    config) edit_config ;;
    menu) show_menu ;;
    *) echo "Usage: omnifetch [start|stop|restart|status|logs|update|config]"; exit 2 ;;
esac
