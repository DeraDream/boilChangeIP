#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="boil-change-ip"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
BIN_FILE="/usr/local/bin/boiltg"
MISSING_DEPS=()

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

log_msg() {
  printf '%s\n' "$*"
}

detect_pkg_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "apt"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
  else
    echo "unknown"
  fi
}

install_os_packages() {
  local pkg_manager="$1"

  case "$pkg_manager" in
    apt)
      log_msg "正在更新 apt 软件源..."
      apt-get update
      log_msg "正在安装系统依赖：python3 python3-venv python3-pip curl git jq bc dnsutils iproute2 netcat-openbsd fonts-noto-cjk"
      DEBIAN_FRONTEND=noninteractive apt-get install -y \
        python3 python3-venv python3-pip curl git jq bc dnsutils iproute2 netcat-openbsd fonts-noto-cjk
      ;;
    dnf)
      log_msg "正在安装系统依赖：python3 python3-pip curl git jq bc bind-utils iproute nmap-ncat google-noto-sans-cjk-fonts"
      dnf install -y python3 python3-pip curl git jq bc bind-utils iproute nmap-ncat google-noto-sans-cjk-fonts
      ;;
    yum)
      log_msg "正在安装系统依赖：python3 python3-pip curl git jq bc bind-utils iproute nmap-ncat google-noto-sans-cjk-fonts"
      yum install -y python3 python3-pip curl git jq bc bind-utils iproute nmap-ncat google-noto-sans-cjk-fonts
      ;;
    *)
      log_msg "未识别包管理器，无法自动安装系统依赖。"
      return 1
      ;;
  esac
}

check_dependencies() {
  MISSING_DEPS=()

  command -v bash >/dev/null 2>&1 || MISSING_DEPS+=("bash")
  command -v curl >/dev/null 2>&1 || MISSING_DEPS+=("curl")
  command -v git >/dev/null 2>&1 || MISSING_DEPS+=("git")
  command -v python3 >/dev/null 2>&1 || MISSING_DEPS+=("python3")
  command -v systemctl >/dev/null 2>&1 || MISSING_DEPS+=("systemctl")
  command -v sing-box >/dev/null 2>&1 || MISSING_DEPS+=("sing-box")
  command -v jq >/dev/null 2>&1 || MISSING_DEPS+=("jq")
  command -v bc >/dev/null 2>&1 || MISSING_DEPS+=("bc")
  command -v dig >/dev/null 2>&1 || MISSING_DEPS+=("dnsutils/dig")
  command -v ip >/dev/null 2>&1 || MISSING_DEPS+=("iproute2/ip")
  command -v nc >/dev/null 2>&1 || MISSING_DEPS+=("netcat/nc")
  if ! find /usr/share/fonts -iname '*Noto*Sans*CJK*' -o -iname '*wqy*' 2>/dev/null | grep -q .; then
    MISSING_DEPS+=("fonts-noto-cjk")
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import venv, ensurepip" >/dev/null 2>&1 || MISSING_DEPS+=("python3-venv")
  fi

  if [ "${#MISSING_DEPS[@]}" -eq 0 ]; then
    return 0
  fi
  return 1
}

install_singbox() {
  if command -v sing-box >/dev/null 2>&1; then
    return 0
  fi
  log_msg "正在安装 sing-box..."
  bash <(curl -fsSL https://sing-box.app/install.sh)
}

print_missing_dependencies() {
  local item
  log_msg "仍缺少以下依赖，安装已停止："
  for item in "${MISSING_DEPS[@]}"; do
    log_msg " - $item"
  done
}

ensure_dependencies() {
  local pkg_manager

  log_msg "正在检查运行依赖..."
  if check_dependencies; then
    log_msg "依赖检查通过。"
    return 0
  fi

  log_msg "发现缺失依赖：${MISSING_DEPS[*]}"
  pkg_manager="$(detect_pkg_manager)"
  install_os_packages "$pkg_manager" || {
    print_missing_dependencies
    exit 1
  }

  log_msg "系统依赖安装完成，正在复查..."
  install_singbox || true
  if check_dependencies; then
    log_msg "依赖复查通过，继续安装。"
    return 0
  fi

  print_missing_dependencies
  log_msg "请先手动安装以上依赖后，再重新执行 bash install.sh。"
  exit 1
}

write_env() {
  local account password bot_token tg_user_id ss_public_host
  account="$(prompt_value '请输入 IPPanel 账号')"
  password="$(prompt_value '请输入 IPPanel 密码' 1)"
  bot_token="$(prompt_value 'Telegram Bot Token')"
  tg_user_id="$(prompt_value '请输入 Telegram 用户 ID')"
  ss_public_host="$(prompt_value '请输入 SS 公网地址或域名，留空自动获取')"

  cat > "$ENV_FILE" <<EOF_ENV
BOT_TOKEN=$bot_token
ALLOWED_USERS=$tg_user_id
ACCOUNT=$account
PASSWORD=$password
SS_PUBLIC_HOST=$ss_public_host
EOF_ENV
  chmod 600 "$ENV_FILE"
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
  chmod +x "$APP_DIR/scripts/boiltg.sh" "$APP_DIR/monitor_ip.sh" "$APP_DIR/scripts/ss_cli.py"
}

init_singbox() {
  "$APP_DIR/.venv/bin/python" -c "import ss_manager; ss_manager.init_db(); ss_manager.render_singbox_config(); ss_manager.restart_singbox()"
}

need_root
ensure_dependencies
write_env
install_python_deps
install_global_menu
init_singbox
install_service

echo "安装完成。请使用 'boiltg' 打开全局菜单。"
