#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ss_manager


def list_users():
    users = ss_manager.list_users()
    if not users:
        print("暂无用户。")
        return
    for user in users:
        print("-" * 40)
        print(ss_manager.format_user(user, include_url=False))


def add_user():
    tg_user_id = int(input("请输入 TG 用户 ID：").strip())
    tg_username = input("请输入 TG 用户名，未知可留空：").strip() or "未知"
    display_name = input("请输入显示名，留空自动生成：").strip() or tg_username.replace("@", "") or f"user_{tg_user_id}"
    port_raw = input("请输入端口，留空随机：").strip()
    expire_at = input(f"请输入到期日，留空默认 {ss_manager.default_expire_date()}：").strip() or ss_manager.default_expire_date()
    traffic_raw = input("请输入月流量 GB，留空默认 100：").strip() or "100"
    speed_limit = input("请输入速率，留空/0 为不限速：").strip()
    if speed_limit in ("", "0"):
        speed_limit = "不限速"
    user = ss_manager.create_user(
        tg_user_id=tg_user_id,
        tg_username=tg_username,
        display_name=display_name,
        port=int(port_raw) if port_raw else None,
        expire_at=expire_at,
        traffic_limit_gb=int(traffic_raw),
        speed_limit=speed_limit,
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


def main():
    if len(sys.argv) < 2:
        print("Usage: ss_cli.py list|add|delete|reset")
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
    else:
        print(f"未知命令：{cmd}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
