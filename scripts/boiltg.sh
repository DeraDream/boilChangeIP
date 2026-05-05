#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="boil-change-ip"
SCRIPT_PATH="$(readlink -f "$0")"
APP_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
VERSION_FILE="$APP_DIR/VERSION"
UPDATE_LOG_FILE="/var/log/boil-change-ip-update.log"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 执行：sudo boiltg" >&2
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

log_msg() {
  mkdir -p "$(dirname "$UPDATE_LOG_FILE")"
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$UPDATE_LOG_FILE"
}

version_gt() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | tail -n1)" = "$1" ] && [ "$1" != "$2" ]
}

restart_service() {
  systemctl daemon-reload
  systemctl restart "$APP_NAME"
}

refresh_service_file() {
  cat > "/etc/systemd/system/${APP_NAME}.service" <<EOF_SERVICE
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
}

service_status() {
  echo "本地版本：$(local_version)"
  echo
  systemctl --no-pager --full status "$APP_NAME" || true
}

update_script() {
  local current remote env_backup
  current="$(local_version)"
  remote="$(remote_version)"
  log_msg "开始检查更新。"
  log_msg "本地版本：$current"
  log_msg "远程版本：$remote"

  if version_gt "$remote" "$current"; then
    env_backup="$(mktemp)"

    if [ -f "$ENV_FILE" ]; then
      cp "$ENV_FILE" "$env_backup"
      chmod 600 "$env_backup"
      log_msg "已备份用户配置：$ENV_FILE"
    else
      log_msg "未发现 .env 配置文件，更新后不会生成空配置。"
    fi

    log_msg "正在拉取远程 main 分支。"
    git -C "$APP_DIR" fetch --prune origin main 2>&1 | tee -a "$UPDATE_LOG_FILE"

    log_msg "正在清理本地旧文件，用户配置 .env 和虚拟环境 .venv 会保留。"
    git -C "$APP_DIR" clean -fd 2>&1 | tee -a "$UPDATE_LOG_FILE"

    log_msg "正在替换本地项目文件为远程版本。"
    git -C "$APP_DIR" checkout -B main origin/main 2>&1 | tee -a "$UPDATE_LOG_FILE"
    git -C "$APP_DIR" reset --hard origin/main 2>&1 | tee -a "$UPDATE_LOG_FILE"
    git -C "$APP_DIR" clean -fd 2>&1 | tee -a "$UPDATE_LOG_FILE"

    if [ -s "$env_backup" ]; then
      cp "$env_backup" "$ENV_FILE"
      chmod 600 "$ENV_FILE"
      log_msg "已恢复用户配置：$ENV_FILE"
    fi
    rm -f "$env_backup"

    log_msg "正在安装/更新 Python 依赖。"
    if [ ! -x "$APP_DIR/.venv/bin/pip" ]; then
      log_msg "未找到虚拟环境，正在重新创建 .venv。"
      python3 -m venv "$APP_DIR/.venv" 2>&1 | tee -a "$UPDATE_LOG_FILE"
    fi
    "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" 2>&1 | tee -a "$UPDATE_LOG_FILE"

    log_msg "正在刷新可执行权限和全局命令。"
    chmod +x "$APP_DIR/scripts/boiltg.sh" "$APP_DIR/monitor_ip.sh" "$APP_DIR/install.sh"
    ln -sf "$APP_DIR/scripts/boiltg.sh" /usr/local/bin/boiltg

    log_msg "正在刷新 systemd 服务文件。"
    refresh_service_file
    systemctl enable "$APP_NAME" >/dev/null 2>&1 || true

    log_msg "正在重启服务。"
    restart_service

    log_msg "更新完成。当前版本：$(local_version)。服务已重启。"
    log_msg "更新日志已保存到：$UPDATE_LOG_FILE"
  else
    log_msg "当前已是最新版本，无需更新。"
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
    echo "修改配置"
    echo "1. 修改 Telegram Bot Token"
    echo "2. 修改 Telegram 用户 ID"
    echo "3. 修改 IPPanel 账号"
    echo "4. 修改 IPPanel 密码"
    echo "0. 返回"
    read -r -p "请选择： " choice

    case "$choice" in
      1)
        read -r -p "请输入新的 Telegram Bot Token： " value
        set_env_value "BOT_TOKEN" "$value"
        restart_service
        echo "已保存配置并重启服务。"
        ;;
      2)
        read -r -p "请输入新的 Telegram 用户 ID，多个用户用英文逗号分隔： " value
        set_env_value "ALLOWED_USERS" "$value"
        restart_service
        echo "已保存配置并重启服务。"
        ;;
      3)
        read -r -p "请输入新的 IPPanel 账号： " value
        set_env_value "ACCOUNT" "$value"
        restart_service
        echo "已保存配置并重启服务。"
        ;;
      4)
        read -r -s -p "请输入新的 IPPanel 密码： " value
        echo
        set_env_value "PASSWORD" "$value"
        restart_service
        echo "已保存配置并重启服务。"
        ;;
      0) return ;;
      *) echo "无效选择。" ;;
    esac
  done
}

uninstall_script() {
  read -r -p "确定卸载服务和全局命令吗？[y/N]: " confirm
  case "$confirm" in
    y|Y|yes|YES)
      systemctl disable --now "$APP_NAME" || true
      rm -f "/etc/systemd/system/${APP_NAME}.service" "/usr/local/bin/boiltg"
      systemctl daemon-reload
      echo "服务和全局命令已删除。项目文件仍保留在：$APP_DIR"
      ;;
    *) echo "已取消。" ;;
  esac
}

main_menu() {
  while true; do
    echo
    echo "Boil Change IP"
    echo "1. 更新脚本"
    echo "2. 修改配置"
    echo "3. 卸载脚本"
    echo "4. 查看脚本状态"
    echo "0. 退出"
    read -r -p "请选择： " choice

    case "$choice" in
      1) update_script ;;
      2) modify_config ;;
      3) uninstall_script ;;
      4) service_status ;;
      0) exit 0 ;;
      *) echo "无效选择。" ;;
    esac
  done
}

need_root
main_menu
