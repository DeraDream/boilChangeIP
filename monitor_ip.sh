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

check_quality_dependencies() {
  QUALITY_MISSING=()
  command -v curl >/dev/null 2>&1 || QUALITY_MISSING+=("curl")
  command -v jq >/dev/null 2>&1 || QUALITY_MISSING+=("jq")
  command -v bc >/dev/null 2>&1 || QUALITY_MISSING+=("bc")
  command -v dig >/dev/null 2>&1 || QUALITY_MISSING+=("dnsutils/dig")
  command -v ip >/dev/null 2>&1 || QUALITY_MISSING+=("iproute2/ip")
  command -v nc >/dev/null 2>&1 || QUALITY_MISSING+=("netcat/nc")
  command -v ansilove >/dev/null 2>&1 || QUALITY_MISSING+=("ansilove")

  [ "${#QUALITY_MISSING[@]}" -eq 0 ]
}

install_quality_dependencies() {
  local pkg_manager="$1"
  case "$pkg_manager" in
    apt)
      log "正在安装 IP 质量检测依赖：curl jq bc dnsutils iproute2 netcat-openbsd ansilove"
      apt-get update >> "$LOG_FILE" 2>&1
      DEBIAN_FRONTEND=noninteractive apt-get install -y \
        curl jq bc dnsutils iproute2 netcat-openbsd ansilove >> "$LOG_FILE" 2>&1
      ;;
    dnf)
      log "正在安装 IP 质量检测依赖：curl jq bc bind-utils iproute nmap-ncat ansilove"
      dnf install -y curl jq bc bind-utils iproute nmap-ncat ansilove >> "$LOG_FILE" 2>&1
      ;;
    yum)
      log "正在安装 IP 质量检测依赖：curl jq bc bind-utils iproute nmap-ncat ansilove"
      yum install -y curl jq bc bind-utils iproute nmap-ncat ansilove >> "$LOG_FILE" 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

ensure_quality_dependencies() {
  local pkg_manager
  if check_quality_dependencies; then
    return 0
  fi

  log "发现 IP 质量检测依赖缺失：${QUALITY_MISSING[*]}"
  if [ "$(id -u)" -ne 0 ]; then
    echo "缺少 IP 质量检测依赖：${QUALITY_MISSING[*]}。当前用户不是 root，无法自动安装。" >&2
    return 1
  fi

  pkg_manager="$(detect_pkg_manager)"
  if ! install_quality_dependencies "$pkg_manager"; then
    echo "无法自动安装 IP 质量检测依赖，未识别包管理器：$pkg_manager" >&2
    return 1
  fi

  if check_quality_dependencies; then
    log "IP 质量检测依赖复查通过。"
    return 0
  fi

  echo "安装后仍缺少 IP 质量检测依赖：${QUALITY_MISSING[*]}" >&2
  return 1
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

  if ! command -v ansilove >/dev/null 2>&1; then
    echo "生成 PNG 图片需要安装 ansilove。" >&2
    return 1
  fi

  ansilove -o "$png_file" "$ansi_file" >/dev/null
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
    -F caption="<b>IP 质量双栈完整报告</b>
旧 IP：<code>${old_ip:-无}</code>
新 IP：<code>${current_ip}</code>
时间：$(date '+%Y-%m-%d %H:%M:%S')
命令：<code>bash &lt;(curl -Ls https://IP.Check.Place)</code>" \
    -F photo="@${png_file}" >/dev/null
}

ensure_quality_dependencies

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

TEMP_DIR="$(mktemp -d)"
ANSI_FILE="${TEMP_DIR}/ipquality.ansi"
RUN_LOG_FILE="$(mktemp)"
PNG_FILE="${IMAGE_DIR}/ip_quality_$(date '+%Y%m%d_%H%M%S').png"

cleanup() {
  rm -rf "$TEMP_DIR"
  rm -f "$RUN_LOG_FILE"
  if [ "$IMAGE_ONLY" -ne 1 ]; then
    rm -f "$PNG_FILE"
  fi
}
trap cleanup EXIT

log "执行 https://IP.Check.Place 双栈完整检测，并输出最终 ANSI 报告"
set +e
bash <(curl -fsSL https://IP.Check.Place) -o "$ANSI_FILE" > "$RUN_LOG_FILE" 2>&1
CHECK_STATUS=$?
set -e

if [ ! -s "$ANSI_FILE" ]; then
  echo "IP.Check.Place 未生成最终 ANSI 报告。" >&2
  if [ "$CHECK_STATUS" -ne 0 ]; then
    echo "远程检测脚本退出码：$CHECK_STATUS" >&2
  fi
  echo "检测过程输出最后 80 行：" >&2
  tail -n 80 "$RUN_LOG_FILE" >&2 || true
  exit 1
fi

if [ "$CHECK_STATUS" -ne 0 ]; then
  log "IP.Check.Place 退出码为 $CHECK_STATUS，但已生成最终 ANSI 报告，继续生成图片。"
fi

if ! render_png "$ANSI_FILE" "$PNG_FILE"; then
  echo "PNG 图片生成失败。" >&2
  echo "最终 ANSI 报告最后 40 行：" >&2
  tail -n 40 "$ANSI_FILE" >&2 || true
  exit 1
fi

if [ ! -f "$PNG_FILE" ]; then
  echo "PNG 图片生成失败。" >&2
  echo "最终 ANSI 报告最后 40 行：" >&2
  tail -n 40 "$ANSI_FILE" >&2 || true
  exit 1
fi

if [ "$IMAGE_ONLY" -eq 1 ]; then
  printf '%s\n' "$PNG_FILE"
  exit 0
fi

send_telegram "$LAST_IP" "$CURRENT_IP" "$PNG_FILE"
log "Telegram 通知已发送，临时文件已清理。"
