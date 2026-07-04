import html
import ipaddress
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
import schedule
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from api_client import IPPanelClient
from config import MONITOR_SCRIPT, get_version, load_env, parse_allowed_users, set_env_value
import ss_manager


env = load_env()
BOT_TOKEN = env.get("BOT_TOKEN", "")
ALLOWED_USERS = parse_allowed_users(env.get("ALLOWED_USERS", ""))
IP_PANEL_TOKEN = env.get("IP_PANEL_TOKEN") or env.get("IPPANEL_TOKEN", "")
DDNS_DOMAIN = env.get("DDNS_DOMAIN") or env.get("SS_PUBLIC_HOST", "")
CHINA_TZ = ZoneInfo("Asia/Shanghai")

if not BOT_TOKEN:
    raise SystemExit("缺少 BOT_TOKEN，请运行 ./install.sh 或 boiltg 进行配置。")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
api = IPPanelClient(IP_PANEL_TOKEN)
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
BTN_API_TOKEN = "9. 配置 API Token"
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
        InlineKeyboardButton(BTN_API_TOKEN, callback_data="menu_api_token"),
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
        KeyboardButton(BTN_API_TOKEN),
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


def masked_token() -> str:
    if not IP_PANEL_TOKEN:
        return "未配置"
    if len(IP_PANEL_TOKEN) <= 8:
        return "*" * len(IP_PANEL_TOKEN)
    return f"{IP_PANEL_TOKEN[:4]}...{IP_PANEL_TOKEN[-4:]}"


def is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(str(value).strip())
        return True
    except ipaddress.AddressValueError:
        return False


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
        return call.message
    except Exception:
        return bot.send_message(
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
        bot.send_message(message.chat.id, "你的 SS 用户已启用，可发送 /my_ss 查看链接。", reply_markup=ReplyKeyboardRemove())
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


@bot.message_handler(commands=["api_token"])
@check_admin
def handle_api_token_command(message):
    start_api_token_config(message.chat.id, message.from_user.id)


def status_text() -> str:
    users = ss_manager.list_users()
    total_in = 0
    total_out = 0
    traffic_lines = []
    for user in users:
        usage = ss_manager.get_user_traffic(user)
        total_in += usage["inbound_bytes"]
        total_out += usage["outbound_bytes"]
        billable = ss_manager.billable_bytes(usage)
        traffic_lines.append(
            f"{user['display_name']}  入站 {ss_manager.format_bytes(usage['inbound_bytes'])} / "
            f"出站 {ss_manager.format_bytes(usage['outbound_bytes'])} / "
            f"计费 {ss_manager.format_bytes(billable)}"
        )

    traffic_text = "\n".join(traffic_lines) if traffic_lines else "暂无用户流量。"
    token_text = masked_token()
    domain_text = ddns_domain() or "未配置"
    return (
        "Bot 状态：运行中\n"
        f"版本：{get_version()}\n"
        f"授权用户：{', '.join(str(x) for x in ALLOWED_USERS) or '未配置'}\n"
        f"IPPanel API Token：{token_text}\n"
        f"DDNS 监听域名：{domain_text}\n"
        f"SS 用户数：{len(users)}\n"
        f"总入站：{ss_manager.format_bytes(total_in)}\n"
        f"总出站：{ss_manager.format_bytes(total_out)}\n"
        f"总计费：{ss_manager.format_bytes(total_out)}\n\n"
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


@bot.message_handler(func=lambda message: message.text == BTN_API_TOKEN)
@check_admin
def handle_reply_api_token(message):
    start_api_token_config(message.chat.id, message.from_user.id)


def send_my_ss(chat_id: int, tg_user_id: int):
    user = ss_manager.get_user_by_tg(tg_user_id)
    if not user and is_admin(tg_user_id):
        bot.send_message(chat_id, "管理员没有绑定普通 SS 用户。")
        return
    if not user:
        bot.send_message(chat_id, f"你还没有权限。你的 ID 是：{tg_user_id}", reply_markup=guest_menu())
        return
    bot.send_message(
        chat_id,
        ss_manager.format_user(user, include_url=True),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


def handle_ss_request(message):
    user_id = message.from_user.id
    if is_admin(user_id):
        bot.send_message(message.chat.id, "你是管理员，无需申请。", reply_markup=reply_menu())
        return
    if is_ss_user(user_id):
        bot.send_message(message.chat.id, "你已经有权限，可发送 /my_ss 查看链接。", reply_markup=ReplyKeyboardRemove())
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
        InlineKeyboardButton("选择加密", callback_data=f"draft_method_{key}"),
        InlineKeyboardButton("修改密码", callback_data=f"draft_edit_password_{key}"),
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
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("给我本人创建", callback_data="manual_self"),
        InlineKeyboardButton("创建不绑定 TG 的用户", callback_data="manual_unbound"),
        InlineKeyboardButton("输入指定 TG ID", callback_data="manual_custom"),
    )
    bot.send_message(chat_id, "请选择要创建的用户类型：", reply_markup=markup)


def send_method_menu(chat_id: int, admin_id: int, draft: ss_manager.ApprovalDraft):
    methods = ss_manager.methods_for_protocol(draft.protocol)
    lines = ["请选择加密方式，发送序号：", ""]
    for idx, method in enumerate(methods, start=1):
        current = "（当前）" if method == draft.method else ""
        lines.append(f"{idx}. {method}{current}")
    admin_states[admin_id] = {"mode": "draft_method_input", "draft": draft}
    bot.send_message(chat_id, "\n".join(lines))


def begin_draft_wizard(chat_id: int, admin_id: int, draft: ss_manager.ApprovalDraft):
    admin_states[admin_id] = {"mode": "draft_protocol", "draft": draft}
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("普通 SS", callback_data=f"draft_proto_ss_{draft.key}"),
        InlineKeyboardButton("SS2022", callback_data=f"draft_proto_ss2022_{draft.key}"),
    )
    bot.send_message(chat_id, "请选择要创建的类型：", reply_markup=markup)


def ask_draft_port(chat_id: int, admin_id: int, draft: ss_manager.ApprovalDraft):
    admin_states[admin_id] = {"mode": "draft_wizard_port", "draft": draft}
    bot.send_message(chat_id, "请输入端口；发送 0 或 random 表示随机。")


def ask_draft_password(chat_id: int, admin_id: int, draft: ss_manager.ApprovalDraft):
    admin_states[admin_id] = {"mode": "draft_wizard_password", "draft": draft}
    bot.send_message(chat_id, "请输入密码；发送 0 或 random 表示随机。")


def ask_draft_method(chat_id: int, admin_id: int, draft: ss_manager.ApprovalDraft):
    methods = ss_manager.methods_for_protocol(draft.protocol)
    lines = [f"请选择 {'SS2022' if draft.protocol == 'ss2022' else '普通 SS'} 加密方式，发送序号：", ""]
    for idx, method in enumerate(methods, start=1):
        lines.append(f"{idx}. {method}")
    admin_states[admin_id] = {
        "mode": "draft_wizard_method",
        "draft": draft,
        "methods": methods,
    }
    bot.send_message(chat_id, "\n".join(lines))


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
            InlineKeyboardButton("切换启用状态", callback_data=f"user_toggle_enabled_{user['id']}"),
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
    bot.send_message(chat_id, f"选择通知时间（北京时间）\n当前：{current}", reply_markup=markup)


def start_bind_domain(chat_id: int, admin_id: int):
    current = ss_manager.get_public_host()
    admin_states[admin_id] = {"mode": "bind_domain_input"}
    bot.send_message(chat_id, f"请输入要绑定的域名。\n当前：{current}")


def start_api_token_config(chat_id: int, admin_id: int):
    admin_states[admin_id] = {"mode": "api_token_input"}
    bot.send_message(
        chat_id,
        "请输入新的 IPPanel API Token。\n"
        "Token 会写入 .env 的 IP_PANEL_TOKEN，保存后会自动重启 Bot 服务。\n"
        f"当前：{masked_token()}",
    )


def send_devices_for_change(chat_id: int, call=None):
    if not IP_PANEL_TOKEN:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(BTN_API_TOKEN, callback_data="menu_api_token"))
        text = "IPPanel API Token 未配置。请先配置 Token 后再获取当前 IP。"
        if call:
            safe_edit(call, text, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)
        return

    devices = api.get_devices_list()
    if not devices:
        text = "未获取到当前 IP，请检查 IPPanel API Token 是否正确。"
        if call:
            safe_edit(call, text)
        else:
            bot.send_message(chat_id, text)
        return

    user_states[chat_id] = {"devices_cache": devices}
    lines = ["当前 IP 如下，点击按钮后会通过官方 API 执行换 IP。", ""]
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


@bot.callback_query_handler(func=lambda call: call.data == "menu_api_token")
@check_admin
def handle_menu_api_token(call):
    bot.answer_callback_query(call.id)
    start_api_token_config(call.message.chat.id, call.from_user.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("manual_"))
@check_admin
def handle_manual_create_choice(call):
    if call.data == "manual_self":
        existing = ss_manager.get_user_by_tg(call.from_user.id)
        if existing:
            bot.answer_callback_query(call.id, "你已经绑定过 SS 用户。", show_alert=True)
            return
        username = ss_manager.parse_tg_username(call.from_user)
        draft = ss_manager.make_manual_draft(call.from_user.id, username)
        bot.answer_callback_query(call.id)
        safe_edit(call, "已选择给本人创建。")
        begin_draft_wizard(call.message.chat.id, call.from_user.id, draft)
        return
    if call.data == "manual_unbound":
        draft = ss_manager.make_manual_draft(None)
        bot.answer_callback_query(call.id)
        safe_edit(call, "已选择创建不绑定 TG 的用户。")
        begin_draft_wizard(call.message.chat.id, call.from_user.id, draft)
        return
    if call.data == "manual_custom":
        admin_states[call.from_user.id] = {"mode": "manual_tg_id"}
        bot.answer_callback_query(call.id)
        safe_edit(call, "请输入要绑定的 TG ID；如果不需要绑定，直接发送 0 或 留空。")


@bot.callback_query_handler(func=lambda call: call.data.startswith("draft_proto_"))
@check_admin
def handle_draft_protocol(call):
    protocol = "ss2022" if call.data.startswith("draft_proto_ss2022_") else "ss"
    state = admin_states.get(call.from_user.id) or {}
    draft = state.get("draft")
    draft_key = call.data.rsplit("_", 1)[1]
    if not draft or draft.key != draft_key:
        bot.answer_callback_query(call.id, "草稿已过期，请重新创建。", show_alert=True)
        return
    draft.protocol = protocol
    methods = ss_manager.methods_for_protocol(protocol)
    draft.method = methods[0]
    draft.password = ss_manager.generate_password_for_method(draft.method)
    bot.answer_callback_query(call.id)
    safe_edit(call, f"已选择 {'SS2022' if protocol == 'ss2022' else '普通 SS'}。")
    ask_draft_port(call.message.chat.id, call.from_user.id, draft)


@bot.callback_query_handler(func=lambda call: call.data.startswith("notify_"))
@check_admin
def handle_notify_actions(call):
    if call.data == "notify_custom":
        admin_states[call.from_user.id] = {"mode": "notify_time"}
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "请输入北京时间通知时间，格式 HH:MM，例如 21:30")
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
    safe_edit(call, f"TG 流量通知时间已设置为北京时间：{value}")


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
    status_message = safe_edit(call, f"正在为 {device['name']} 更换 IP...")
    threading.Thread(
        target=run_ip_change_flow,
        args=(chat_id, device, status_message),
        daemon=True,
    ).start()
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
    bot.answer_callback_query(call.id)
    begin_draft_wizard(call.message.chat.id, call.from_user.id, draft)


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
            method=draft.method,
            password=draft.password,
            expire_at=draft.expire_at,
            expire_disable_enabled=draft.expire_disable_enabled,
            traffic_limit_gb=draft.traffic_limit_gb,
        )
        admin_states.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, "已创建。")
        safe_edit(call, "已创建用户：\n\n" + ss_manager.format_user(user))
        if draft.tg_user_id is not None:
            try:
                bot.send_message(
                    draft.tg_user_id,
                    "你的 SS 链接已开通，可发送 /my_ss 查看。",
                    reply_markup=ReplyKeyboardRemove(),
                )
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

    if action == "method":
        bot.answer_callback_query(call.id)
        send_method_menu(call.message.chat.id, call.from_user.id, draft)
        return

    if action == "setmethod":
        try:
            method = ss_manager.methods_for_protocol(draft.protocol)[int(parts[2])]
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "加密方式不存在。", show_alert=True)
            return
        draft.method = method
        draft.password = ss_manager.generate_password_for_method(method)
        admin_states[call.from_user.id] = {"mode": "draft", "draft": draft}
        bot.answer_callback_query(call.id, "已切换加密并重新生成随机密码。")
        send_draft(call.message.chat.id, draft)
        return

    field = parts[2]
    admin_states[call.from_user.id] = {"mode": f"draft_edit_{field}", "draft": draft}
    prompt = {
        "port": "请输入新端口：",
        "password": "请输入新密码；发送 0 或 random 可重新随机生成：",
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

    if call.data.startswith("user_toggle_enabled_"):
        user_id = int(call.data.rsplit("_", 1)[1])
        user = ss_manager.get_user(user_id)
        if not user:
            bot.answer_callback_query(call.id, "用户不存在。")
            return
        new_value = 0 if int(user.get("enabled", 1)) else 1
        ss_manager.update_user(user_id, enabled=new_value)
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
        if mode == "api_token_input":
            if not value:
                bot.send_message(message.chat.id, "Token 不能为空，请重新发送。")
                return
            set_env_value("IP_PANEL_TOKEN", value)
            admin_states.pop(message.from_user.id, None)
            bot.send_message(message.chat.id, "IPPanel API Token 已保存，Bot 服务即将重启。")
            threading.Timer(2, restart_bot_service).start()
            return

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
            begin_draft_wizard(message.chat.id, message.from_user.id, draft)
            return

        if mode == "draft_wizard_port":
            draft = state["draft"]
            if value.lower() in ("0", "random", "随机"):
                draft.port = ss_manager.random_port()
            else:
                port = int(value)
                if port in ss_manager.used_ports() or not ss_manager.is_port_free(port):
                    bot.send_message(message.chat.id, "端口不可用，请重新输入；也可以发送 0 或 random 随机。")
                    return
                draft.port = port
            ask_draft_password(message.chat.id, message.from_user.id, draft)
            return

        if mode == "draft_wizard_password":
            draft = state["draft"]
            draft.password = "" if value.lower() in ("0", "random", "随机") else value
            ask_draft_method(message.chat.id, message.from_user.id, draft)
            return

        if mode == "draft_wizard_method":
            draft = state["draft"]
            methods = state.get("methods") or ss_manager.methods_for_protocol(draft.protocol)
            index = int(value) - 1
            if index < 0 or index >= len(methods):
                bot.send_message(message.chat.id, "序号无效，请重新输入列表中的序号。")
                return
            draft.method = methods[index]
            if not draft.password:
                draft.password = ss_manager.generate_password_for_method(draft.method)
            admin_states[message.from_user.id] = {"mode": "draft", "draft": draft}
            send_draft(message.chat.id, draft)
            return

        if mode == "draft_method_input":
            draft = state["draft"]
            methods = ss_manager.methods_for_protocol(draft.protocol)
            index = int(value) - 1
            if index < 0 or index >= len(methods):
                bot.send_message(message.chat.id, "序号无效，请重新输入列表中的序号。")
                return
            draft.method = methods[index]
            admin_states[message.from_user.id] = {"mode": "draft", "draft": draft}
            send_draft(message.chat.id, draft)
            return

        if mode.startswith("draft_edit_"):
            draft = state["draft"]
            field = mode.replace("draft_edit_", "")
            if field == "port":
                if value.lower() in ("0", "random", "随机"):
                    draft.port = ss_manager.random_port()
                    admin_states[message.from_user.id] = {"mode": "draft", "draft": draft}
                    send_draft(message.chat.id, draft)
                    return
                port = int(value)
                if port in ss_manager.used_ports() or not ss_manager.is_port_free(port):
                    bot.send_message(message.chat.id, "端口不可用，请重新输入。")
                    return
                draft.port = port
            elif field == "password":
                draft.password = (
                    ss_manager.generate_password_for_method(draft.method)
                    if value.lower() in ("0", "random", "随机")
                    else value
                )
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
            bot.send_message(message.chat.id, f"TG 流量通知时间已设置为北京时间：{value}")
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


def delete_status_message(message) -> None:
    if not message:
        return
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass


def replace_status_message(chat_id: int, previous_message, text: str):
    delete_status_message(previous_message)
    return bot.send_message(chat_id, text, parse_mode="HTML")


def normalize_domain(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.hostname or value
    value = value.split("/", 1)[0].split(":", 1)[0].strip().strip(".")
    return value


def ddns_domain() -> str:
    return normalize_domain(DDNS_DOMAIN)


def should_wait_for_ddns(domain: str) -> bool:
    return bool(domain) and not is_ipv4(domain)


def resolve_domain_ipv4(domain: str) -> list[str]:
    ips: list[str] = []
    try:
        resp = requests.get(
            "https://cloudflare-dns.com/dns-query",
            params={"name": domain, "type": "A"},
            headers={"accept": "application/dns-json"},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            for answer in data.get("Answer", []) or []:
                ip = str(answer.get("data", "")).strip()
                if is_ipv4(ip):
                    ips.append(ip)
    except Exception:
        pass

    if ips:
        return sorted(set(ips))

    try:
        for item in socket.getaddrinfo(domain, None, socket.AF_INET):
            ip = item[4][0]
            if is_ipv4(ip):
                ips.append(ip)
    except OSError:
        pass
    return sorted(set(ips))


def wait_for_new_ip(old_ip: str, timeout_seconds: int = 300, interval: int = 10) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    last_result = ""
    while time.monotonic() < deadline:
        ok, current_ip = api.get_current_ip()
        last_result = current_ip
        if ok and current_ip and current_ip != old_ip:
            return True, current_ip
        time.sleep(interval)
    return False, last_result or "等待 BOIL 返回新 IP 超时"


def wait_for_domain_ip(domain: str, expected_ip: str, timeout_seconds: int = 600, interval: int = 10) -> tuple[bool, list[str]]:
    deadline = time.monotonic() + timeout_seconds
    last_ips: list[str] = []
    while time.monotonic() < deadline:
        last_ips = resolve_domain_ipv4(domain)
        if expected_ip in last_ips:
            return True, last_ips
        time.sleep(interval)
    return False, last_ips


def run_ip_change_flow(chat_id: int, device: dict[str, Any], status_message=None):
    try:
        execute_ip_change(chat_id, device, status_message=status_message)
    except Exception as exc:
        replace_status_message(
            chat_id,
            status_message,
            f"<b>换 IP 流程异常</b>\n\n{html.escape(str(exc))}",
        )


def execute_ip_change(chat_id: int, device: dict[str, Any], status_message=None):
    old_ip = device.get("current_ip", "未知")
    domain = ddns_domain()
    should_check_ddns = should_wait_for_ddns(domain)
    old_domain_ips = resolve_domain_ipv4(domain) if should_check_ddns else []

    status_message = replace_status_message(
        chat_id,
        status_message,
        (
            "<b>正在执行更换 IP</b>\n\n"
            f"目标：{html.escape(str(device['name']))}\n"
            f"旧 IP：<code>{html.escape(str(old_ip))}</code>"
        ),
    )
    success, result = api.change_ip()
    if not success:
        replace_status_message(
            chat_id,
            status_message,
            (
                "<b>IP 更换失败</b>\n\n"
                f"目标：{html.escape(str(device['name']))}\n"
                f"原因：{html.escape(str(result))}"
            ),
        )
        return schedule.CancelJob

    status_message = replace_status_message(
        chat_id,
        status_message,
        (
            "<b>BOIL 已受理换 IP</b>\n\n"
            f"<code>{html.escape(str(result))}</code>\n\n"
            "正在轮询 BOIL 当前 IP..."
        ),
    )
    got_new_ip, new_ip = wait_for_new_ip(str(old_ip))
    if not got_new_ip:
        replace_status_message(
            chat_id,
            status_message,
            (
                "<b>换 IP 状态未确认</b>\n\n"
                f"旧 IP：<code>{html.escape(str(old_ip))}</code>\n"
                f"最后查询结果：<code>{html.escape(str(new_ip))}</code>\n"
                "BOIL 已受理请求，但在等待时间内没有查询到新 IP。"
            ),
        )
        return schedule.CancelJob

    if not should_check_ddns:
        reason = (
            "未配置 DDNS_DOMAIN 或 SS_PUBLIC_HOST"
            if not domain
            else "DDNS_DOMAIN 或 SS_PUBLIC_HOST 当前是 IP 地址，不是域名"
        )
        replace_status_message(
            chat_id,
            status_message,
            (
                "<b>换 IP 成功</b>\n\n"
                f"旧 IP：<code>{html.escape(str(old_ip))}</code>\n"
                f"新 IP：<code>{html.escape(str(new_ip))}</code>\n\n"
                f"{html.escape(reason)}，已跳过 DDNS 解析确认。"
            ),
        )
        return schedule.CancelJob

    old_domain_text = ", ".join(old_domain_ips) if old_domain_ips else "未解析到 A 记录"
    status_message = replace_status_message(
        chat_id,
        status_message,
        (
            "<b>换 IP 成功</b>\n\n"
            f"旧 IP：<code>{html.escape(str(old_ip))}</code>\n"
            f"新 IP：<code>{html.escape(str(new_ip))}</code>\n"
            f"域名：<code>{html.escape(domain)}</code>\n"
            f"原解析：<code>{html.escape(old_domain_text)}</code>\n\n"
            "正在等待 DDNS-GO 刷新域名解析..."
        ),
    )

    ddns_ok, domain_ips = wait_for_domain_ip(domain, str(new_ip))
    domain_text = ", ".join(domain_ips) if domain_ips else "未解析到 A 记录"
    if ddns_ok:
        text = (
            "<b>DDNS-GO 已刷新完成</b>\n\n"
            f"域名：<code>{html.escape(domain)}</code>\n"
            f"旧 IP：<code>{html.escape(str(old_ip))}</code>\n"
            f"新 IP：<code>{html.escape(str(new_ip))}</code>\n"
            f"当前解析：<code>{html.escape(domain_text)}</code>"
        )
    else:
        text = (
            "<b>换 IP 成功，但 DDNS 解析未确认</b>\n\n"
            f"域名：<code>{html.escape(domain)}</code>\n"
            f"旧 IP：<code>{html.escape(str(old_ip))}</code>\n"
            f"新 IP：<code>{html.escape(str(new_ip))}</code>\n"
            f"当前解析：<code>{html.escape(domain_text)}</code>\n"
            "DDNS-GO 可能还没刷新，或 DNS 缓存还没生效。"
        )
    replace_status_message(chat_id, status_message, text)
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
    china_now = datetime.now(CHINA_TZ)
    today = china_now.strftime("%Y-%m-%d")
    current_time = china_now.strftime("%H:%M")
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
