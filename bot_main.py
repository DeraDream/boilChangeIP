import html
import subprocess
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict

import schedule
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from api_client import IPPanelClient
from config import MONITOR_SCRIPT, get_version, load_env, parse_allowed_users
import ss_manager


env = load_env()
BOT_TOKEN = env.get("BOT_TOKEN", "")
ALLOWED_USERS = parse_allowed_users(env.get("ALLOWED_USERS", ""))
ACCOUNT = env.get("ACCOUNT", "")
PASSWORD = env.get("PASSWORD", "")

if not BOT_TOKEN:
    raise SystemExit("缺少 BOT_TOKEN，请运行 ./install.sh 或 boiltg 进行配置。")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
api = IPPanelClient(ACCOUNT, PASSWORD)
ss_manager.init_db()

user_states: Dict[int, Dict[str, Any]] = {}

BTN_STATUS = "1. Bot 状态"
BTN_DEVICES = "2. 获取列表/更换 IP"
BTN_QUALITY = "3. 获取当前 IP 质量"
BTN_CREATE_USER = "4. 生成用户"
BTN_USER_MGMT = "5. 用户管理"
BTN_DELETE_USER = "6. 删除用户"
BTN_NOTIFY = "7. TG 通知"
BTN_BIND_DOMAIN = "8. 绑定域名"
BTN_REQUEST_SS = "申请 SS 链接"
BTN_MY_SS = "我的链接"
BTN_CHANGE_IP = "更换 IP"

admin_states: Dict[int, Dict[str, Any]] = {}


def main_menu() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton(BTN_STATUS, callback_data="menu_status"),
        InlineKeyboardButton(BTN_DEVICES, callback_data="menu_devices"),
        InlineKeyboardButton(BTN_QUALITY, callback_data="menu_quality"),
        InlineKeyboardButton(BTN_CREATE_USER, callback_data="menu_create_user"),
        InlineKeyboardButton(BTN_USER_MGMT, callback_data="menu_user_mgmt"),
        InlineKeyboardButton(BTN_DELETE_USER, callback_data="menu_delete_user"),
        InlineKeyboardButton(BTN_NOTIFY, callback_data="menu_notify"),
        InlineKeyboardButton(BTN_BIND_DOMAIN, callback_data="menu_bind_domain"),
    )
    return markup


def reply_menu() -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton(BTN_STATUS),
        KeyboardButton(BTN_DEVICES),
        KeyboardButton(BTN_QUALITY),
        KeyboardButton(BTN_CREATE_USER),
        KeyboardButton(BTN_USER_MGMT),
        KeyboardButton(BTN_DELETE_USER),
        KeyboardButton(BTN_NOTIFY),
        KeyboardButton(BTN_BIND_DOMAIN),
    )
    return markup


def guest_menu() -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(KeyboardButton(BTN_REQUEST_SS))
    return markup


def user_menu() -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(KeyboardButton(BTN_MY_SS), KeyboardButton(BTN_CHANGE_IP))
    return markup


def is_admin(user_id: int) -> bool:
    return user_id in ALLOWED_USERS


def is_ss_user(user_id: int) -> bool:
    return ss_manager.get_user_by_tg(user_id) is not None


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


def check_admin(func: Callable[..., Any]) -> Callable[..., Any]:
    return check_permission(func)


def check_ss_or_admin(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(message_or_call, *args, **kwargs):
        user_id = message_or_call.from_user.id
        chat_id = (
            message_or_call.message.chat.id
            if hasattr(message_or_call, "message")
            else message_or_call.chat.id
        )
        if is_admin(user_id) or is_ss_user(user_id):
            return func(message_or_call, *args, **kwargs)
        bot.send_message(chat_id, f"你还没有权限。你的 ID 是：{user_id}", reply_markup=guest_menu())
        return None

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
def send_welcome(message):
    user_id = message.from_user.id
    if is_admin(user_id):
        bot.send_message(message.chat.id, "管理员菜单已启用。", reply_markup=reply_menu())
        bot.send_message(message.chat.id, "也可以在这里选择操作：", reply_markup=main_menu())
        return
    if is_ss_user(user_id):
        bot.send_message(message.chat.id, "你的 SS 用户菜单已启用。", reply_markup=user_menu())
        return
    bot.send_message(message.chat.id, f"你的 ID 是：{user_id}", reply_markup=guest_menu())


@bot.message_handler(commands=["list"])
@check_admin
def handle_list(message):
    bot.reply_to(message, "正在获取设备数据，请稍候...")
    bot.send_message(message.chat.id, api.get_formatted_status())


@bot.message_handler(commands=["ip_change"])
@check_admin
def handle_ip_change(message):
    send_devices_for_change(message.chat.id)


def status_text() -> str:
    users = ss_manager.list_users()
    total_in = 0
    total_out = 0
    traffic_lines = []
    for user in users:
        usage = ss_manager.get_user_traffic(user)
        total_in += usage["inbound_bytes"]
        total_out += usage["outbound_bytes"]
        single = ss_manager.single_way_bytes(usage)
        traffic_lines.append(
            f"{user['display_name']}  入站 {ss_manager.format_bytes(usage['inbound_bytes'])} / "
            f"出站 {ss_manager.format_bytes(usage['outbound_bytes'])} / "
            f"单向 {ss_manager.format_bytes(single)}"
        )

    traffic_text = "\n".join(traffic_lines) if traffic_lines else "暂无用户流量。"
    return (
        "Bot 状态：运行中\n"
        f"版本：{get_version()}\n"
        f"授权用户：{', '.join(str(x) for x in ALLOWED_USERS) or '未配置'}\n"
        f"IPPanel 账号：{ACCOUNT or '未配置'}\n"
        f"SS 用户数：{len(users)}\n"
        f"总入站：{ss_manager.format_bytes(total_in)}\n"
        f"总出站：{ss_manager.format_bytes(total_out)}\n"
        f"总单向：{ss_manager.format_bytes(max(total_in, total_out))}\n\n"
        f"用户流量：\n{traffic_text}"
    )


@bot.message_handler(func=lambda message: message.text == BTN_STATUS)
@check_admin
def handle_reply_status(message):
    bot.send_message(message.chat.id, status_text(), reply_markup=reply_menu())


@bot.message_handler(func=lambda message: message.text == BTN_DEVICES)
@check_admin
def handle_reply_devices(message):
    send_devices_for_change(message.chat.id)


@bot.message_handler(func=lambda message: message.text == BTN_QUALITY)
@check_admin
def handle_reply_quality(message):
    bot.send_message(
        message.chat.id,
        "正在检测当前 IP 质量，可能需要等待一分钟...",
        reply_markup=reply_menu(),
    )
    send_ip_quality(message.chat.id)


@bot.message_handler(commands=["my_ss"])
@check_ss_or_admin
def handle_my_ss(message):
    send_my_ss(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda message: message.text == BTN_MY_SS)
@check_ss_or_admin
def handle_reply_my_ss(message):
    send_my_ss(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda message: message.text == BTN_CHANGE_IP)
@check_ss_or_admin
def handle_reply_user_change_ip(message):
    send_devices_for_change(message.chat.id)


@bot.message_handler(func=lambda message: message.text == BTN_REQUEST_SS)
def handle_request_button(message):
    handle_ss_request(message)


@bot.message_handler(func=lambda message: message.text == BTN_CREATE_USER)
@check_admin
def handle_reply_create_user(message):
    start_manual_create(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda message: message.text == BTN_USER_MGMT)
@check_admin
def handle_reply_user_mgmt(message):
    send_user_management(message.chat.id)


@bot.message_handler(func=lambda message: message.text == BTN_DELETE_USER)
@check_admin
def handle_reply_delete_user(message):
    send_delete_users(message.chat.id)


@bot.message_handler(func=lambda message: message.text == BTN_NOTIFY)
@check_admin
def handle_reply_notify(message):
    send_notify_menu(message.chat.id)


@bot.message_handler(func=lambda message: message.text == BTN_BIND_DOMAIN)
@check_admin
def handle_reply_bind_domain(message):
    start_bind_domain(message.chat.id, message.from_user.id)


def send_my_ss(chat_id: int, tg_user_id: int):
    user = ss_manager.get_user_by_tg(tg_user_id)
    if not user and is_admin(tg_user_id):
        bot.send_message(chat_id, "管理员没有绑定普通 SS 用户。")
        return
    if not user:
        bot.send_message(chat_id, f"你还没有权限。你的 ID 是：{tg_user_id}", reply_markup=guest_menu())
        return
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("更换 IP", callback_data="user_change_ip"))
    bot.send_message(chat_id, ss_manager.format_user(user, include_url=True), parse_mode="HTML", reply_markup=markup)


def handle_ss_request(message):
    user_id = message.from_user.id
    if is_admin(user_id):
        bot.send_message(message.chat.id, "你是管理员，无需申请。", reply_markup=reply_menu())
        return
    if is_ss_user(user_id):
        bot.send_message(message.chat.id, "你已经有权限，请点击“我的链接”。", reply_markup=user_menu())
        return

    username = ss_manager.parse_tg_username(message.from_user)
    req = ss_manager.create_or_update_request(user_id, username)
    bot.send_message(message.chat.id, "申请已提交，请等待管理员审核。", reply_markup=guest_menu())
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("接受", callback_data=f"ssreq_accept_{user_id}"),
        InlineKeyboardButton("拒绝", callback_data=f"ssreq_reject_{user_id}"),
    )
    text = f"用户 {html.escape(username)} 申请 SS 链接\nID：{user_id}"
    for admin_id in ALLOWED_USERS:
        bot.send_message(admin_id, text, reply_markup=markup)


def approval_markup(draft: ss_manager.ApprovalDraft) -> InlineKeyboardMarkup:
    key = draft.key
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("确认创建", callback_data=f"draft_confirm_{key}"),
        InlineKeyboardButton("修改端口", callback_data=f"draft_edit_port_{key}"),
        InlineKeyboardButton("修改用户名", callback_data=f"draft_edit_name_{key}"),
        InlineKeyboardButton("修改到期日", callback_data=f"draft_edit_expire_{key}"),
        InlineKeyboardButton("切换到期禁用", callback_data=f"draft_toggle_expire_disable_{key}"),
        InlineKeyboardButton("修改流量", callback_data=f"draft_edit_traffic_{key}"),
        InlineKeyboardButton("取消", callback_data=f"draft_cancel_{key}"),
    )
    return markup


def send_draft(chat_id: int, draft: ss_manager.ApprovalDraft):
    bot.send_message(chat_id, draft.as_text(), reply_markup=approval_markup(draft))


def start_manual_create(chat_id: int, admin_id: int):
    admin_states[admin_id] = {"mode": "manual_tg_id"}
    bot.send_message(chat_id, "请输入要绑定的 TG ID；如果不需要绑定，直接发送 0 或 留空。")


def send_user_management(chat_id: int):
    users = ss_manager.list_users()
    if not users:
        bot.send_message(chat_id, "暂无用户。")
        return
    for user in users:
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("修改到期日", callback_data=f"user_edit_expire_{user['id']}"),
            InlineKeyboardButton("切换到期禁用", callback_data=f"user_toggle_expire_disable_{user['id']}"),
            InlineKeyboardButton("修改流量", callback_data=f"user_edit_traffic_{user['id']}"),
            InlineKeyboardButton("删除用户", callback_data=f"user_delete_{user['id']}"),
        )
        bot.send_message(chat_id, ss_manager.format_user(user, include_url=True), parse_mode="HTML", reply_markup=markup)


def send_delete_users(chat_id: int):
    users = ss_manager.list_users()
    if not users:
        bot.send_message(chat_id, "暂无用户。")
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for user in users:
        tg_label = user["tg_user_id"] or "未绑定"
        markup.add(InlineKeyboardButton(f"{user['id']}. {user['display_name']} | {tg_label}", callback_data=f"user_delete_{user['id']}"))
    bot.send_message(chat_id, "请选择要删除的用户：", reply_markup=markup)


def send_notify_menu(chat_id: int):
    current = ss_manager.get_setting("traffic_notify_time", "未设置")
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("09:00", callback_data="notify_set_09:00"),
        InlineKeyboardButton("12:00", callback_data="notify_set_12:00"),
        InlineKeyboardButton("18:00", callback_data="notify_set_18:00"),
        InlineKeyboardButton("21:00", callback_data="notify_set_21:00"),
        InlineKeyboardButton("自定义 HH:MM", callback_data="notify_custom"),
        InlineKeyboardButton("关闭通知", callback_data="notify_off"),
    )
    bot.send_message(chat_id, f"选择通知时间\n当前：{current}", reply_markup=markup)


def start_bind_domain(chat_id: int, admin_id: int):
    current = ss_manager.get_public_host()
    admin_states[admin_id] = {"mode": "bind_domain_input"}
    bot.send_message(chat_id, f"请输入要绑定的域名。\n当前：{current}")


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
@check_admin
def handle_menu_status(call):
    safe_edit(call, status_text(), reply_markup=main_menu())
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_devices")
@check_admin
def handle_menu_devices(call):
    bot.answer_callback_query(call.id, "正在获取设备...")
    send_devices_for_change(call.message.chat.id, call=call)


@bot.callback_query_handler(func=lambda call: call.data == "menu_quality")
@check_admin
def handle_menu_quality(call):
    bot.answer_callback_query(call.id, "正在检测 IP 质量...")
    safe_edit(call, "正在检测当前 IP 质量，可能需要等待一分钟...")
    send_ip_quality(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_create_user")
@check_admin
def handle_menu_create_user(call):
    bot.answer_callback_query(call.id)
    start_manual_create(call.message.chat.id, call.from_user.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_user_mgmt")
@check_admin
def handle_menu_user_mgmt(call):
    bot.answer_callback_query(call.id)
    send_user_management(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_delete_user")
@check_admin
def handle_menu_delete_user(call):
    bot.answer_callback_query(call.id)
    send_delete_users(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_notify")
@check_admin
def handle_menu_notify(call):
    bot.answer_callback_query(call.id)
    send_notify_menu(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_bind_domain")
@check_admin
def handle_menu_bind_domain(call):
    bot.answer_callback_query(call.id)
    start_bind_domain(call.message.chat.id, call.from_user.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("notify_"))
@check_admin
def handle_notify_actions(call):
    if call.data == "notify_custom":
        admin_states[call.from_user.id] = {"mode": "notify_time"}
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "请输入通知时间，格式 HH:MM，例如 21:30")
        return
    if call.data == "notify_off":
        ss_manager.set_setting("traffic_notify_time", "")
        bot.answer_callback_query(call.id, "已关闭。")
        safe_edit(call, "TG 流量通知已关闭。")
        return
    value = call.data.replace("notify_set_", "")
    ss_manager.set_setting("traffic_notify_time", value)
    ss_manager.set_setting("traffic_notify_last_date", "")
    bot.answer_callback_query(call.id, "已设置。")
    safe_edit(call, f"TG 流量通知时间已设置为：{value}")


@bot.callback_query_handler(func=lambda call: call.data == "user_change_ip")
@check_ss_or_admin
def handle_user_change_ip(call):
    bot.answer_callback_query(call.id, "正在获取设备...")
    send_devices_for_change(call.message.chat.id, call=call)


@bot.callback_query_handler(func=lambda call: call.data.startswith("change_now_"))
@check_ss_or_admin
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


@bot.callback_query_handler(func=lambda call: call.data.startswith("ssreq_accept_"))
@check_admin
def handle_request_accept(call):
    tg_user_id = int(call.data.rsplit("_", 1)[1])
    req = ss_manager.get_request(tg_user_id)
    if not req:
        bot.answer_callback_query(call.id, "申请不存在。", show_alert=True)
        return
    draft = ss_manager.make_draft(tg_user_id, req.get("tg_username") or "未知")
    admin_states[call.from_user.id] = {"mode": "draft", "draft": draft}
    bot.answer_callback_query(call.id)
    send_draft(call.message.chat.id, draft)


@bot.callback_query_handler(func=lambda call: call.data.startswith("ssreq_reject_"))
@check_admin
def handle_request_reject(call):
    tg_user_id = int(call.data.rsplit("_", 1)[1])
    ss_manager.mark_request(tg_user_id, "rejected", call.from_user.id)
    bot.answer_callback_query(call.id, "已拒绝。")
    safe_edit(call, f"已拒绝用户 {tg_user_id} 的 SS 申请。")
    try:
        bot.send_message(tg_user_id, "你的 SS 链接申请已被拒绝。", reply_markup=guest_menu())
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("draft_"))
@check_admin
def handle_draft_actions(call):
    parts = call.data.split("_")
    action = parts[1]
    draft_key = parts[-1]
    state = admin_states.get(call.from_user.id) or {}
    draft = state.get("draft")
    if not draft or draft.key != draft_key:
        bot.answer_callback_query(call.id, "草稿已过期，请重新接受申请。", show_alert=True)
        return

    if action == "confirm":
        user = ss_manager.create_user(
            tg_user_id=draft.tg_user_id,
            tg_username=draft.tg_username,
            display_name=draft.display_name,
            port=draft.port,
            expire_at=draft.expire_at,
            expire_disable_enabled=draft.expire_disable_enabled,
            traffic_limit_gb=draft.traffic_limit_gb,
        )
        admin_states.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, "已创建。")
        safe_edit(call, "已创建用户：\n\n" + ss_manager.format_user(user))
        if draft.tg_user_id is not None:
            try:
                bot.send_message(draft.tg_user_id, "你的 SS 链接已开通，请点击底部“我的链接”查看。", reply_markup=user_menu())
            except Exception:
                pass
        return

    if action == "cancel":
        admin_states.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, "已取消。")
        safe_edit(call, "已取消创建。")
        return

    if action == "toggle":
        draft.expire_disable_enabled = 0 if draft.expire_disable_enabled else 1
        admin_states[call.from_user.id] = {"mode": "draft", "draft": draft}
        bot.answer_callback_query(call.id, "已切换。")
        send_draft(call.message.chat.id, draft)
        return

    field = parts[2]
    admin_states[call.from_user.id] = {"mode": f"draft_edit_{field}", "draft": draft}
    prompt = {
        "port": "请输入新端口：",
        "name": "请输入自定义用户名/显示名：",
        "expire": "请输入到期日，例如 2026-06-05 或 30d：",
        "traffic": "请输入月流量 GB，例如 100：",
    }.get(field, "请输入新值：")
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, prompt)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("user_edit_")
    or call.data.startswith("user_delete_")
    or call.data.startswith("user_toggle_")
)
@check_admin
def handle_user_actions(call):
    if call.data.startswith("user_delete_"):
        user_id = int(call.data.rsplit("_", 1)[1])
        user = ss_manager.delete_user(user_id)
        bot.answer_callback_query(call.id, "已删除。" if user else "用户不存在。")
        safe_edit(call, f"已删除用户：{user['display_name']}" if user else "用户不存在。")
        return

    if call.data.startswith("user_toggle_expire_disable_"):
        user_id = int(call.data.rsplit("_", 1)[1])
        user = ss_manager.get_user(user_id)
        if not user:
            bot.answer_callback_query(call.id, "用户不存在。")
            return
        new_value = 0 if int(user.get("expire_disable_enabled", 1)) else 1
        ss_manager.update_user(user_id, expire_disable_enabled=new_value)
        user = ss_manager.get_user(user_id)
        bot.answer_callback_query(call.id, "已切换。")
        safe_edit(call, "已更新用户：\n\n" + ss_manager.format_user(user))
        return

    _prefix, _edit, field, raw_user_id = call.data.split("_")
    user_id = int(raw_user_id)
    admin_states[call.from_user.id] = {"mode": f"user_edit_{field}", "user_id": user_id}
    prompt = {
        "expire": "请输入新的到期日，例如 2026-06-05 或 30d：",
        "traffic": "请输入新的月流量 GB，例如 100：",
    }.get(field, "请输入新值：")
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, prompt)


def parse_expire_value(value: str) -> str:
    value = value.strip()
    if value.endswith("d") and value[:-1].isdigit():
        return (datetime.now() + timedelta(days=int(value[:-1]))).strftime("%Y-%m-%d")
    datetime.strptime(value, "%Y-%m-%d")
    return value


@bot.message_handler(func=lambda message: message.from_user.id in admin_states)
@check_admin
def handle_admin_state_input(message):
    state = admin_states.get(message.from_user.id) or {}
    mode = state.get("mode", "")
    value = (message.text or "").strip()
    try:
        if mode == "bind_domain_input":
            host = ss_manager.normalize_public_host(value)
            admin_states[message.from_user.id] = {
                "mode": "bind_domain_confirm",
                "domain": host,
            }
            bot.send_message(
                message.chat.id,
                f"确认绑定域名：<code>{html.escape(host)}</code>\n发送 YES 确认，发送其他内容取消。",
                parse_mode="HTML",
            )
            return

        if mode == "bind_domain_confirm":
            host = state["domain"]
            admin_states.pop(message.from_user.id, None)
            if value.lower() != "yes":
                bot.send_message(message.chat.id, "已取消绑定域名。")
                return
            apply_domain_binding(message.chat.id, host)
            return

        if mode == "manual_tg_id":
            tg_user_id = None if value in ("", "0", "不绑定", "无") else int(value)
            draft = ss_manager.make_manual_draft(tg_user_id)
            admin_states[message.from_user.id] = {"mode": "draft", "draft": draft}
            send_draft(message.chat.id, draft)
            return

        if mode.startswith("draft_edit_"):
            draft = state["draft"]
            field = mode.replace("draft_edit_", "")
            if field == "port":
                port = int(value)
                if port in ss_manager.used_ports() or not ss_manager.is_port_free(port):
                    bot.send_message(message.chat.id, "端口不可用，请重新输入。")
                    return
                draft.port = port
            elif field == "name":
                draft.display_name = value or draft.display_name
            elif field == "expire":
                draft.expire_at = parse_expire_value(value)
            elif field == "traffic":
                draft.traffic_limit_gb = int(value)
            admin_states[message.from_user.id] = {"mode": "draft", "draft": draft}
            send_draft(message.chat.id, draft)
            return

        if mode.startswith("user_edit_"):
            user_id = int(state["user_id"])
            field = mode.replace("user_edit_", "")
            updates: dict[str, Any] = {}
            if field == "expire":
                updates["expire_at"] = parse_expire_value(value)
            elif field == "traffic":
                updates["traffic_limit_gb"] = int(value)
            ss_manager.update_user(user_id, **updates)
            admin_states.pop(message.from_user.id, None)
            user = ss_manager.get_user(user_id)
            bot.send_message(message.chat.id, "已更新用户：\n\n" + ss_manager.format_user(user))
            return
        if mode == "notify_time":
            datetime.strptime(value, "%H:%M")
            ss_manager.set_setting("traffic_notify_time", value)
            ss_manager.set_setting("traffic_notify_last_date", "")
            admin_states.pop(message.from_user.id, None)
            bot.send_message(message.chat.id, f"TG 流量通知时间已设置为：{value}")
            return
    except Exception as exc:
        bot.send_message(message.chat.id, f"输入无效：{exc}")


def apply_domain_binding(chat_id: int, host: str):
    ss_manager.bind_public_host(host)
    success_text = "域名更新成功，所有链接已更新。"
    bot.send_message(chat_id, success_text)
    notify_domain_update(success_text)
    threading.Timer(2, restart_bot_service).start()


def notify_domain_update(title: str):
    users = ss_manager.list_users()
    if not users:
        for admin_id in ALLOWED_USERS:
            bot.send_message(admin_id, title)
        return

    for user in users:
        text = f"{title}\n\n{ss_manager.format_user(user, include_url=True)}"
        tg_user_id = user.get("tg_user_id")
        targets = [int(tg_user_id)] if tg_user_id else ALLOWED_USERS
        for target in targets:
            try:
                bot.send_message(target, text, parse_mode="HTML")
            except Exception:
                pass


def restart_bot_service():
    subprocess.run(["systemctl", "restart", "boil-change-ip"], check=False)


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

    send_quality_images(chat_id, png_path)


def split_image_for_telegram(png_path: Path) -> list[Path]:
    from PIL import Image

    output_paths: list[Path] = []
    with Image.open(png_path) as image:
        image = image.convert("RGB")
        width, height = image.size

        if width > 4096:
            new_height = max(1, int(height * (4096 / width)))
            image = image.resize((4096, new_height))
            width, height = image.size

        max_chunk_height = max(1000, min(4000, 9000 - width))
        if height <= max_chunk_height and width + height < 9500:
            fixed_path = png_path.with_name(f"{png_path.stem}_telegram.png")
            image.save(fixed_path, "PNG")
            return [fixed_path]

        index = 1
        top = 0
        while top < height:
            bottom = min(height, top + max_chunk_height)
            chunk = image.crop((0, top, width, bottom))
            chunk_path = png_path.with_name(f"{png_path.stem}_part_{index}.png")
            chunk.save(chunk_path, "PNG")
            output_paths.append(chunk_path)
            top = bottom
            index += 1

    return output_paths


def send_quality_images(chat_id: int, png_path: Path):
    generated_paths: list[Path] = []
    try:
        generated_paths = split_image_for_telegram(png_path)
        total = len(generated_paths)

        for idx, image_path in enumerate(generated_paths, start=1):
            caption = "当前 IP 质量双栈完整报告" if total == 1 else f"当前 IP 质量双栈完整报告（{idx}/{total}）"
            try:
                with image_path.open("rb") as photo:
                    bot.send_photo(chat_id, photo, caption=caption)
            except ApiTelegramException as exc:
                if "PHOTO_INVALID_DIMENSIONS" not in str(exc):
                    raise
                with image_path.open("rb") as document:
                    bot.send_document(chat_id, document, caption=f"{caption}（图片尺寸过大，已按文件发送）")
    except Exception as exc:
        bot.send_message(chat_id, f"IP 质量图片发送失败：{html.escape(str(exc))}")
    finally:
        for path in [png_path, *generated_paths]:
            try:
                path.unlink()
            except OSError:
                pass


def run_scheduler():
    while True:
        ss_manager.disable_expired_users()
        ss_manager.enforce_traffic_limits()
        send_scheduled_traffic_report()
        schedule.run_pending()
        time.sleep(60)


def send_scheduled_traffic_report():
    notify_time = ss_manager.get_setting("traffic_notify_time", "").strip()
    if not notify_time:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    current_time = datetime.now().strftime("%H:%M")
    if current_time != notify_time:
        return
    if ss_manager.get_setting("traffic_notify_last_date", "") == today:
        return
    report = ss_manager.traffic_report()
    for admin_id in ALLOWED_USERS:
        try:
            bot.send_message(admin_id, report)
        except Exception:
            pass
    ss_manager.set_setting("traffic_notify_last_date", today)


if __name__ == "__main__":
    threading.Thread(target=run_scheduler, daemon=True).start()
    print(f"Boil Change IP Bot 已启动，版本：{get_version()}")
    bot.infinity_polling(skip_pending=True)
