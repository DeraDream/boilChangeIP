#!/usr/bin/env python3
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ss_manager
from config import load_env, parse_allowed_users


def list_users():
    users = ss_manager.list_users()
    if not users:
        print("暂无用户。")
        return
    for user in users:
        print("-" * 40)
        print(ss_manager.format_user(user, include_url=True))


def add_user():
    tg_raw = input("请输入 TG 用户 ID，留空/0 表示不绑定：").strip()
    tg_user_id = None if tg_raw in ("", "0", "不绑定", "无") else int(tg_raw)
    tg_username = input("请输入 TG 用户名，未知可留空：").strip() or "未知"
    display_name = input("请输入显示名，留空自动生成：").strip() or tg_username.replace("@", "") or (f"user_{tg_user_id}" if tg_user_id is not None else "manual_user")
    port_raw = input("请输入端口，留空随机：").strip()
    expire_at = input(f"请输入到期日，留空默认 {ss_manager.default_expire_date()}：").strip() or ss_manager.default_expire_date()
    traffic_raw = input("请输入月流量 GB，留空默认 100：").strip() or "100"
    expire_disable_raw = input("到期后自动禁用？留空默认是，输入 n 关闭：").strip().lower()
    expire_disable_enabled = 0 if expire_disable_raw in ("n", "no", "0", "否") else 1
    user = ss_manager.create_user(
        tg_user_id=tg_user_id,
        tg_username=tg_username,
        display_name=display_name,
        port=int(port_raw) if port_raw else None,
        expire_at=expire_at,
        expire_disable_enabled=expire_disable_enabled,
        traffic_limit_gb=int(traffic_raw),
    )
    print("已创建用户：")
    print(ss_manager.format_user(user, include_url=True))


def delete_user():
    list_users()
    user_id = int(input("请输入要删除的用户 ID：").strip())
    user = ss_manager.delete_user(user_id)
    print(f"已删除：{user['display_name']}" if user else "用户不存在。")


def reset_all():
    confirm = input("危险操作：将清空所有 SS 用户和申请数据。请输入 RESET 确认：").strip()
    if confirm != "RESET":
        print("确认文本不匹配，已取消。")
        return
    confirm2 = input("再次确认，输入 YES：").strip()
    if confirm2 != "YES":
        print("二次确认不匹配，已取消。")
        return
    ss_manager.reset_all()
    print("已清空所有用户信息表和缓存数据，恢复到初始状态。")


def notify_time():
    current = ss_manager.get_setting("traffic_notify_time", "未设置")
    print(f"当前通知时间：{current or '未设置'}")
    value = input("请输入通知时间 HH:MM，输入 off 关闭：").strip()
    if value.lower() in ("off", "0", "关闭"):
        ss_manager.set_setting("traffic_notify_time", "")
        ss_manager.set_setting("traffic_notify_last_date", "")
        print("已关闭 TG 流量通知。")
        return
    import datetime as _dt

    _dt.datetime.strptime(value, "%H:%M")
    ss_manager.set_setting("traffic_notify_time", value)
    ss_manager.set_setting("traffic_notify_last_date", "")
    print(f"已设置 TG 流量通知时间：{value}")


def notify_domain_update(text: str):
    env = load_env()
    token = env.get("BOT_TOKEN", "")
    admins = parse_allowed_users(env.get("ALLOWED_USERS", ""))
    if not token:
        return
    import telebot

    bot = telebot.TeleBot(token, parse_mode=None)
    users = ss_manager.list_users()
    if not users:
        for admin_id in admins:
            bot.send_message(admin_id, text)
        return

    for user in users:
        message = f"{text}\n\n{ss_manager.format_user(user, include_url=True)}"
        tg_user_id = user.get("tg_user_id")
        targets = [int(tg_user_id)] if tg_user_id else admins
        for target in targets:
            try:
                bot.send_message(target, message, parse_mode="HTML")
            except Exception:
                pass


def bind_domain():
    current = ss_manager.get_public_host()
    print(f"当前 SS 域名/IP：{current}")
    value = input("请输入要绑定的域名：").strip()
    host = ss_manager.normalize_public_host(value)
    confirm = input(f"确认绑定域名 {host}？输入 YES 确认：").strip()
    if confirm.lower() != "yes":
        print("已取消绑定域名。")
        return
    ss_manager.bind_public_host(host)
    text = "域名更新成功，所有链接已更新。"
    print(text)
    notify_domain_update(text)
    subprocess.run(["systemctl", "restart", "boil-change-ip"], check=False)
    print("服务已重启。")


def main():
    if len(sys.argv) < 2:
        print("Usage: ss_cli.py list|add|delete|reset|notify|bind-domain")
        return 2
    cmd = sys.argv[1]
    if cmd == "list":
        list_users()
    elif cmd == "add":
        add_user()
    elif cmd == "delete":
        delete_user()
    elif cmd == "reset":
        reset_all()
    elif cmd == "notify":
        notify_time()
    elif cmd == "bind-domain":
        bind_domain()
    else:
        print(f"未知命令：{cmd}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
