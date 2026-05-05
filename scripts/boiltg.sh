#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="boil-change-ip"
SCRIPT_PATH="$(readlink -f "$0")"
APP_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
VERSION_FILE="$APP_DIR/VERSION"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root: sudo boiltg" >&2
    exit 1
  fi
}

local_version() {
  [ -f "$VERSION_FILE" ] && cat "$VERSION_FILE" || echo "0.0.0"
}

remote_version() {
  git -C "$APP_DIR" fetch --quiet origin main
  git -C "$APP_DIR" show origin/main:VERSION 2>/dev/null || echo "0.0.0"
}

version_gt() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | tail -n1)" = "$1" ] && [ "$1" != "$2" ]
}

restart_service() {
  systemctl daemon-reload
  systemctl restart "$APP_NAME"
}

service_status() {
  echo "Local version: $(local_version)"
  echo
  systemctl --no-pager --full status "$APP_NAME" || true
}

update_script() {
  local current remote
  current="$(local_version)"
  remote="$(remote_version)"
  echo "Local version: $current"
  echo "Remote version: $remote"

  if version_gt "$remote" "$current"; then
    git -C "$APP_DIR" pull --ff-only origin main
    "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
    chmod +x "$APP_DIR/scripts/boiltg.sh" "$APP_DIR/monitor_ip.sh" "$APP_DIR/install.sh"
    restart_service
    echo "Updated to version $(local_version) and restarted."
  else
    echo "Already up to date."
  fi
}

set_env_value() {
  local key="$1"
  local value="$2"

  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

modify_config() {
  while true; do
    echo
    echo "Modify config"
    echo "1. Modify Telegram Bot Token"
    echo "2. Modify Telegram User ID"
    echo "3. Modify IPPanel account"
    echo "4. Modify IPPanel password"
    echo "0. Back"
    read -r -p "Choose: " choice

    case "$choice" in
      1)
        read -r -p "New Telegram Bot Token: " value
        set_env_value "BOT_TOKEN" "$value"
        restart_service
        echo "Applied and restarted."
        ;;
      2)
        read -r -p "New Telegram User ID, comma-separated if multiple: " value
        set_env_value "ALLOWED_USERS" "$value"
        restart_service
        echo "Applied and restarted."
        ;;
      3)
        read -r -p "New IPPanel account: " value
        set_env_value "ACCOUNT" "$value"
        restart_service
        echo "Applied and restarted."
        ;;
      4)
        read -r -s -p "New IPPanel password: " value
        echo
        set_env_value "PASSWORD" "$value"
        restart_service
        echo "Applied and restarted."
        ;;
      0) return ;;
      *) echo "Invalid choice." ;;
    esac
  done
}

uninstall_script() {
  read -r -p "Uninstall service and global command? [y/N]: " confirm
  case "$confirm" in
    y|Y|yes|YES)
      systemctl disable --now "$APP_NAME" || true
      rm -f "/etc/systemd/system/${APP_NAME}.service" "/usr/local/bin/boiltg"
      systemctl daemon-reload
      echo "Service and global command removed. Project files remain at: $APP_DIR"
      ;;
    *) echo "Canceled." ;;
  esac
}

main_menu() {
  while true; do
    echo
    echo "Boil Change IP"
    echo "1. Update script"
    echo "2. Modify config"
    echo "3. Uninstall script"
    echo "4. View script status"
    echo "0. Exit"
    read -r -p "Choose: " choice

    case "$choice" in
      1) update_script ;;
      2) modify_config ;;
      3) uninstall_script ;;
      4) service_status ;;
      0) exit 0 ;;
      *) echo "Invalid choice." ;;
    esac
  done
}

need_root
main_menu
