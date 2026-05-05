#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="boil-change-ip"
SCRIPT_PATH="$(readlink -f "$0")"
APP_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
VERSION_FILE="$APP_DIR/VERSION"

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

version_gt() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | tail -n1)" = "$1" ] && [ "$1" != "$2" ]
}

restart_service() {
  systemctl daemon-reload
  systemctl restart "$APP_NAME"
}

service_status() {
  echo "本地版本：$(local_version)"
  echo
  systemctl --no-pager --full status "$APP_NAME" || true
}

update_script() {
  local current remote
  current="$(local_version)"
  remote="$(remote_version)"
  echo "本地版本：$current"
  echo "远程版本：$remote"

  if version_gt "$remote" "$current"; then
    git -C "$APP_DIR" pull --ff-only origin main
    "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
    chmod +x "$APP_DIR/scripts/boiltg.sh" "$APP_DIR/monitor_ip.sh" "$APP_DIR/install.sh"
    restart_service
    echo "已更新到版本 $(local_version)，并已重启服务。"
  else
    echo "当前已是最新版本。"
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
