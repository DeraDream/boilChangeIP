import html
import subprocess
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict

import schedule
import telebot
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from api_client import IPPanelClient
from config import MONITOR_SCRIPT, get_version, load_env, parse_allowed_users


env = load_env()
BOT_TOKEN = env.get("BOT_TOKEN", "")
ALLOWED_USERS = parse_allowed_users(env.get("ALLOWED_USERS", ""))
ACCOUNT = env.get("ACCOUNT", "")
PASSWORD = env.get("PASSWORD", "")

if not BOT_TOKEN:
    raise SystemExit("缺少 BOT_TOKEN，请运行 ./install.sh 或 boiltg 进行配置。")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
api = IPPanelClient(ACCOUNT, PASSWORD)

user_states: Dict[int, Dict[str, Any]] = {}

BTN_STATUS = "1. Bot 状态"
BTN_DEVICES = "2. 获取列表/更换 IP"
BTN_QUALITY = "3. 获取当前 IP 质量"


def main_menu() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(BTN_STATUS, callback_data="menu_status"),
        InlineKeyboardButton(BTN_DEVICES, callback_data="menu_devices"),
        InlineKeyboardButton(BTN_QUALITY, callback_data="menu_quality"),
    )
    return markup


def reply_menu() -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(
        KeyboardButton(BTN_STATUS),
        KeyboardButton(BTN_DEVICES),
        KeyboardButton(BTN_QUALITY),
    )
    return markup


def device_markup(devices: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    for idx, dev in enumerate(devices):
        label = f"{dev['name']} | {dev['current_ip']}"
        markup.add(InlineKeyboardButton(label[:64], callback_data=f"change_now_{idx}"))
    return markup


def check_permission(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(message_or_call, *args, **kwargs):
        if hasattr(message_or_call, "message"):
            user_id = message_or_call.from_user.id
            chat_id = message_or_call.message.chat.id
            is_call = True
        else:
            user_id = message_or_call.from_user.id
            chat_id = message_or_call.chat.id
            is_call = False

        if user_id not in ALLOWED_USERS:
            text = f"拒绝访问。你的 Telegram 用户 ID 是：{user_id}"
            if is_call:
                bot.answer_callback_query(message_or_call.id, text, show_alert=True)
            else:
                bot.send_message(chat_id, text)
            print(f"[安全] 已拦截未授权用户：{user_id}")
            return None

        return func(message_or_call, *args, **kwargs)

    return wrapper


def safe_edit(call, text: str, reply_markup=None, parse_mode=None):
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )


@bot.message_handler(commands=["start", "help", "menu"])
@check_permission
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "Boil Change IP Bot 已在线，底部菜单已启用。",
        reply_markup=reply_menu(),
    )
    bot.send_message(
        message.chat.id,
        "也可以在这里选择操作：",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["list"])
@check_permission
def handle_list(message):
    bot.reply_to(message, "正在获取设备数据，请稍候...")
    bot.send_message(message.chat.id, api.get_formatted_status())


@bot.message_handler(commands=["ip_change"])
@check_permission
def handle_ip_change(message):
    send_devices_for_change(message.chat.id)


def status_text() -> str:
    return (
        "Bot 状态：运行中\n"
        f"版本：{get_version()}\n"
        f"授权用户：{', '.join(str(x) for x in ALLOWED_USERS) or '未配置'}\n"
        f"IPPanel 账号：{ACCOUNT or '未配置'}"
    )


@bot.message_handler(func=lambda message: message.text == BTN_STATUS)
@check_permission
def handle_reply_status(message):
    bot.send_message(message.chat.id, status_text(), reply_markup=reply_menu())


@bot.message_handler(func=lambda message: message.text == BTN_DEVICES)
@check_permission
def handle_reply_devices(message):
    send_devices_for_change(message.chat.id)


@bot.message_handler(func=lambda message: message.text == BTN_QUALITY)
@check_permission
def handle_reply_quality(message):
    bot.send_message(
        message.chat.id,
        "正在检测当前 IP 质量，可能需要等待一分钟...",
        reply_markup=reply_menu(),
    )
    send_ip_quality(message.chat.id)


def send_devices_for_change(chat_id: int, call=None):
    devices = api.get_devices_list()
    if not devices:
        text = "未获取到设备，或 IPPanel 登录失败。"
        if call:
            safe_edit(call, text)
        else:
            bot.send_message(chat_id, text)
        return

    user_states[chat_id] = {"devices_cache": devices}
    lines = ["设备列表如下，点击设备按钮后会立即执行换 IP。", ""]
    for idx, dev in enumerate(devices, start=1):
        lines.append(f"{idx}. {dev['name']}")
        lines.append(f"   当前 IP：{dev['current_ip']}")
    text = "\n".join(lines)
    if call:
        safe_edit(call, text, reply_markup=device_markup(devices))
    else:
        bot.send_message(chat_id, text, reply_markup=device_markup(devices))


@bot.callback_query_handler(func=lambda call: call.data == "menu_status")
@check_permission
def handle_menu_status(call):
    safe_edit(call, status_text(), reply_markup=main_menu())
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_devices")
@check_permission
def handle_menu_devices(call):
    bot.answer_callback_query(call.id, "正在获取设备...")
    send_devices_for_change(call.message.chat.id, call=call)


@bot.callback_query_handler(func=lambda call: call.data == "menu_quality")
@check_permission
def handle_menu_quality(call):
    bot.answer_callback_query(call.id, "正在检测 IP 质量...")
    safe_edit(call, "正在检测当前 IP 质量，可能需要等待一分钟...")
    send_ip_quality(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("change_now_"))
@check_permission
def handle_change_now(call):
    chat_id = call.message.chat.id
    state = user_states.get(chat_id) or {}
    devices = state.get("devices_cache") or []

    try:
        dev_idx = int(call.data.rsplit("_", 1)[1])
        device = devices[dev_idx]
    except (ValueError, IndexError, KeyError, TypeError):
        bot.answer_callback_query(call.id, "设备缓存已过期，正在刷新列表。")
        send_devices_for_change(chat_id, call=call)
        return

    bot.answer_callback_query(call.id, "正在更换 IP...")
    safe_edit(call, f"正在为 {device['name']} 更换 IP...")
    execute_ip_change(chat_id, device)
    user_states.pop(chat_id, None)


def execute_ip_change(chat_id: int, device: dict[str, Any]):
    router_id = device["router_id"]
    interface = device["interface"]
    old_ip = device.get("current_ip", "未知")

    success, result = api.change_ip(router_id, interface)

    if success:
        text = (
            "<b>IP 更换成功</b>\n\n"
            f"设备：{html.escape(str(device['name']))}\n"
            f"旧 IP：<code>{html.escape(str(old_ip))}</code>\n"
            f"新 IP：<code>{html.escape(str(result))}</code>"
        )
    else:
        text = (
            "<b>IP 更换失败</b>\n\n"
            f"设备：{html.escape(str(device['name']))}\n"
            f"原因：{html.escape(str(result))}"
        )

    bot.send_message(chat_id, text, parse_mode="HTML")
    return schedule.CancelJob


def send_ip_quality(chat_id: int):
    if not MONITOR_SCRIPT.exists():
        bot.send_message(chat_id, f"未找到检测脚本：{MONITOR_SCRIPT}")
        return

    try:
        result = subprocess.run(
            ["bash", str(MONITOR_SCRIPT), "--force", "--image-only"],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        bot.send_message(chat_id, "未找到 bash，请在 Linux VPS 上运行。")
        return
    except subprocess.TimeoutExpired:
        bot.send_message(chat_id, "IP 质量检测超时。")
        return

    if result.returncode != 0:
        log_tail = ""
        log_file = Path("/var/log/boil-change-ip-monitor.log")
        if log_file.exists():
            try:
                log_tail = "\n\n最近日志：\n" + "\n".join(
                    log_file.read_text(encoding="utf-8", errors="replace")
                    .splitlines()[-20:]
                )
            except OSError:
                log_tail = ""

        output = (result.stderr or result.stdout or "检测脚本没有返回错误详情。").strip()
        output = f"{output}{log_tail}"
        bot.send_message(chat_id, f"IP 质量检测失败：\n{output[-3500:]}")
        return

    output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not output_lines:
        bot.send_message(chat_id, "IP 质量检测已结束，但检测脚本没有返回图片路径。")
        return

    png_path = Path(output_lines[-1])
    if not png_path.exists():
        bot.send_message(chat_id, "IP 质量检测已结束，但未生成 PNG 图片。")
        return

    try:
        with png_path.open("rb") as photo:
            bot.send_photo(chat_id, photo, caption="当前 IP 质量报告")
    finally:
        try:
            png_path.unlink()
        except OSError:
            pass


def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    threading.Thread(target=run_scheduler, daemon=True).start()
    print(f"Boil Change IP Bot 已启动，版本：{get_version()}")
    bot.infinity_polling(skip_pending=True)
