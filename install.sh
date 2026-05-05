#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="boil-change-ip"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
BIN_FILE="/usr/local/bin/boiltg"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 执行：sudo bash install.sh" >&2
    exit 1
  fi
}

prompt_value() {
  local label="$1"
  local secret="${2:-0}"
  local value
  if [ "$secret" = "1" ]; then
    read -r -s -p "$label: " value
    echo >&2
  else
    read -r -p "$label: " value
  fi
  printf '%s' "$value"
}

write_env() {
  local account password bot_token tg_user_id
  account="$(prompt_value '请输入 IPPanel 账号')"
  password="$(prompt_value '请输入 IPPanel 密码' 1)"
  bot_token="$(prompt_value 'Telegram Bot Token')"
  tg_user_id="$(prompt_value '请输入 Telegram 用户 ID')"

  cat > "$ENV_FILE" <<EOF_ENV
BOT_TOKEN=$bot_token
ALLOWED_USERS=$tg_user_id
ACCOUNT=$account
PASSWORD=$password
EOF_ENV
  chmod 600 "$ENV_FILE"
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3 python3-venv python3-pip curl git ansilove
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip curl git ansilove
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip curl git ansilove
  else
    echo "未识别包管理器，请手动安装 python3、pip、curl、git 和 ansilove。" >&2
  fi
}

install_python_deps() {
  python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install --upgrade pip
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
}

install_service() {
  cat > "$SERVICE_FILE" <<EOF_SERVICE
[Unit]
Description=Boil Change IP Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/bot_main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF_SERVICE

  systemctl daemon-reload
  systemctl enable --now "$APP_NAME"
}

install_global_menu() {
  ln -sf "$APP_DIR/scripts/boiltg.sh" "$BIN_FILE"
  chmod +x "$APP_DIR/scripts/boiltg.sh" "$APP_DIR/monitor_ip.sh"
}

need_root
write_env
install_packages
install_python_deps
install_global_menu
install_service

echo "安装完成。请使用 'boiltg' 打开全局菜单。"
