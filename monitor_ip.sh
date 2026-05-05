#!/usr/bin/env bash
set -Eeuo pipefail

export TZ="${TZ:-Asia/Shanghai}"

IP_LOG_FILE="${IP_LOG_FILE:-/var/lib/boil-change-ip/current_ip}"
IMAGE_DIR="${IMAGE_DIR:-/tmp/boil-change-ip}"
LOG_FILE="${LOG_FILE:-/var/log/boil-change-ip-monitor.log}"
FORCE=0
IMAGE_ONLY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
    --image-only) IMAGE_ONLY=1 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$(dirname "$IP_LOG_FILE")" "$IMAGE_DIR" "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

get_current_ip() {
  local endpoints=(
    "https://api.ipify.org"
    "https://icanhazip.com"
    "https://ifconfig.me"
    "https://ipinfo.io/ip"
  )
  local endpoint ip
  for endpoint in "${endpoints[@]}"; do
    ip="$(curl -fsS --connect-timeout 5 --max-time 10 -4 "$endpoint" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
      printf '%s\n' "$ip"
      return 0
    fi
  done
  return 1
}

render_png() {
  local ansi_file="$1"
  local png_file="$2"
  local processed_file="${ansi_file}.processed"

  if ! command -v ansilove >/dev/null 2>&1; then
    echo "生成 PNG 图片需要安装 ansilove。" >&2
    return 1
  fi

  grep -v -E "Map:|IP Checks Today:|Report Link:" "$ansi_file" > "$processed_file" || true
  ansilove -o "$png_file" "$processed_file" >/dev/null
  rm -f "$processed_file"
}

send_telegram() {
  local old_ip="$1"
  local current_ip="$2"
  local png_file="$3"

  if [ -z "${TG_BOT_TOKEN:-}" ] || [ -z "${TG_CHAT_ID:-}" ]; then
    log "Telegram 变量为空，跳过通知。"
    return 0
  fi

  curl -fsS -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendPhoto" \
    -F chat_id="${TG_CHAT_ID}" \
    -F parse_mode="HTML" \
    -F caption="<b>IP 质量报告</b>
旧 IP：<code>${old_ip:-无}</code>
新 IP：<code>${current_ip}</code>
时间：$(date '+%Y-%m-%d %H:%M:%S')
参数：<code>-4 -E</code>" \
    -F photo="@${png_file}" >/dev/null
}

CURRENT_IP="$(get_current_ip || true)"
if [ -z "$CURRENT_IP" ]; then
  log "获取当前公网 IPv4 失败。"
  echo "获取当前公网 IPv4 失败。" >&2
  exit 1
fi

LAST_IP=""
if [ -f "$IP_LOG_FILE" ]; then
  LAST_IP="$(cat "$IP_LOG_FILE" || true)"
fi

if [ "$FORCE" -ne 1 ] && [ "$CURRENT_IP" = "$LAST_IP" ]; then
  log "IP 未变化：$CURRENT_IP"
  exit 0
fi

printf '%s\n' "$CURRENT_IP" > "$IP_LOG_FILE"
log "IP 已变化或强制检测：${LAST_IP:-无} -> $CURRENT_IP"

ANSI_FILE="$(mktemp)"
PNG_FILE="${IMAGE_DIR}/ip_quality_$(date '+%Y%m%d_%H%M%S').png"

cleanup() {
  rm -f "$ANSI_FILE"
  if [ "$IMAGE_ONLY" -ne 1 ]; then
    rm -f "$PNG_FILE"
  fi
}
trap cleanup EXIT

log "执行 IP.Check.Place -4 -E"
bash <(curl -sL IP.Check.Place) -4 -E > "$ANSI_FILE" 2>&1

if [ ! -s "$ANSI_FILE" ]; then
  echo "IP.Check.Place 未返回有效输出。" >&2
  exit 1
fi

render_png "$ANSI_FILE" "$PNG_FILE"

if [ ! -f "$PNG_FILE" ]; then
  echo "PNG 图片生成失败。" >&2
  exit 1
fi

if [ "$IMAGE_ONLY" -eq 1 ]; then
  printf '%s\n' "$PNG_FILE"
  exit 0
fi

send_telegram "$LAST_IP" "$CURRENT_IP" "$PNG_FILE"
log "Telegram 通知已发送，临时文件已清理。"
