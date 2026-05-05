import html
import subprocess
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict

import schedule
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from api_client import IPPanelClient
from config import MONITOR_SCRIPT, get_version, load_env, parse_allowed_users


env = load_env()
BOT_TOKEN = env.get("BOT_TOKEN", "")
ALLOWED_USERS = parse_allowed_users(env.get("ALLOWED_USERS", ""))
ACCOUNT = env.get("ACCOUNT", "")
PASSWORD = env.get("PASSWORD", "")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is missing. Run ./install.sh or boiltg to configure.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
api = IPPanelClient(ACCOUNT, PASSWORD)

user_states: Dict[int, Dict[str, Any]] = {}


def main_menu() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("1. Bot status", callback_data="menu_status"),
        InlineKeyboardButton("2. Device list / change IP", callback_data="menu_devices"),
        InlineKeyboardButton("3. Current IP quality", callback_data="menu_quality"),
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
            text = f"Access denied. Your Telegram User ID is: {user_id}"
            if is_call:
                bot.answer_callback_query(message_or_call.id, text, show_alert=True)
            else:
                bot.send_message(chat_id, text)
            print(f"[security] blocked unauthorized user: {user_id}")
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
        "Boil Change IP bot is online. Choose an action:",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["list"])
@check_permission
def handle_list(message):
    bot.reply_to(message, "Fetching device data, please wait...")
    bot.send_message(message.chat.id, api.get_formatted_status())


@bot.message_handler(commands=["ip_change"])
@check_permission
def handle_ip_change(message):
    send_devices_for_change(message.chat.id)


def send_devices_for_change(chat_id: int, call=None):
    devices = api.get_devices_list()
    if not devices:
        text = "No devices were found, or IPPanel login failed."
        if call:
            safe_edit(call, text)
        else:
            bot.send_message(chat_id, text)
        return

    user_states[chat_id] = {"devices_cache": devices}
    lines = ["Device list. Click a device to change IP immediately.", ""]
    for idx, dev in enumerate(devices, start=1):
        lines.append(f"{idx}. {dev['name']}")
        lines.append(f"   Current IP: {dev['current_ip']}")
    text = "\n".join(lines)
    if call:
        safe_edit(call, text, reply_markup=device_markup(devices))
    else:
        bot.send_message(chat_id, text, reply_markup=device_markup(devices))


@bot.callback_query_handler(func=lambda call: call.data == "menu_status")
@check_permission
def handle_menu_status(call):
    text = (
        "Bot status: running\n"
        f"Version: {get_version()}\n"
        f"Allowed users: {', '.join(str(x) for x in ALLOWED_USERS) or 'none'}\n"
        f"IPPanel account: {ACCOUNT or 'not configured'}"
    )
    safe_edit(call, text, reply_markup=main_menu())
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_devices")
@check_permission
def handle_menu_devices(call):
    bot.answer_callback_query(call.id, "Fetching devices...")
    send_devices_for_change(call.message.chat.id, call=call)


@bot.callback_query_handler(func=lambda call: call.data == "menu_quality")
@check_permission
def handle_menu_quality(call):
    bot.answer_callback_query(call.id, "Running IP quality check...")
    safe_edit(call, "Running IP quality check. This may take a minute...")
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
        bot.answer_callback_query(call.id, "Device cache expired. Refreshing list.")
        send_devices_for_change(chat_id, call=call)
        return

    bot.answer_callback_query(call.id, "Changing IP...")
    safe_edit(call, f"Changing IP for {device['name']}...")
    execute_ip_change(chat_id, device)
    user_states.pop(chat_id, None)


def execute_ip_change(chat_id: int, device: dict[str, Any]):
    router_id = device["router_id"]
    interface = device["interface"]
    old_ip = device.get("current_ip", "Unknown")

    success, result = api.change_ip(router_id, interface)

    if success:
        text = (
            "<b>IP change succeeded</b>\n\n"
            f"Device: {html.escape(str(device['name']))}\n"
            f"Old IP: <code>{html.escape(str(old_ip))}</code>\n"
            f"New IP: <code>{html.escape(str(result))}</code>"
        )
    else:
        text = (
            "<b>IP change failed</b>\n\n"
            f"Device: {html.escape(str(device['name']))}\n"
            f"Reason: {html.escape(str(result))}"
        )

    bot.send_message(chat_id, text, parse_mode="HTML")
    return schedule.CancelJob


def send_ip_quality(chat_id: int):
    if not MONITOR_SCRIPT.exists():
        bot.send_message(chat_id, f"Monitor script not found: {MONITOR_SCRIPT}")
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
        bot.send_message(chat_id, "bash was not found. Please run this on a Linux VPS.")
        return
    except subprocess.TimeoutExpired:
        bot.send_message(chat_id, "IP quality check timed out.")
        return

    if result.returncode != 0:
        output = (result.stderr or result.stdout or "unknown error").strip()
        bot.send_message(chat_id, f"IP quality check failed:\n{output[-3500:]}")
        return

    png_path = Path(result.stdout.strip().splitlines()[-1])
    if not png_path.exists():
        bot.send_message(chat_id, "IP quality check finished, but no PNG was generated.")
        return

    try:
        with png_path.open("rb") as photo:
            bot.send_photo(chat_id, photo, caption="Current IP quality report")
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
    print(f"Boil Change IP bot started. Version: {get_version()}")
    bot.infinity_polling(skip_pending=True)
