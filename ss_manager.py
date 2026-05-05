import base64
import json
import os
import random
import secrets
import shutil
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests

from config import BASE_DIR, load_env


DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "boil_ss.db"
SINGBOX_CONFIG = Path("/etc/sing-box/boil-change-ip.json")
SINGBOX_SERVICE = Path("/etc/systemd/system/sing-box-boil.service")
SS_METHOD = "2022-blake3-aes-128-gcm"
DEFAULT_TRAFFIC_GB = 100
DEFAULT_SPEED = "不限速"
PORT_MIN = 30000
PORT_MAX = 60000


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_expire_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def parse_tg_username(user: Any) -> str:
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    full = f"{first} {last}".strip()
    return full or "未知"


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL UNIQUE,
                tg_username TEXT NOT NULL DEFAULT '未知',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ss_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL UNIQUE,
                tg_username TEXT NOT NULL DEFAULT '未知',
                display_name TEXT NOT NULL,
                port INTEGER NOT NULL UNIQUE,
                method TEXT NOT NULL,
                password TEXT NOT NULL,
                expire_at TEXT NOT NULL,
                traffic_limit_gb INTEGER NOT NULL DEFAULT 100,
                speed_limit TEXT NOT NULL DEFAULT '不限速',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def db() -> sqlite3.Connection:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row else None


def get_request(tg_user_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        return row_to_dict(
            conn.execute(
                "SELECT * FROM access_requests WHERE tg_user_id = ?", (tg_user_id,)
            ).fetchone()
        )


def create_or_update_request(tg_user_id: int, tg_username: str) -> dict[str, Any]:
    init_db()
    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM access_requests WHERE tg_user_id = ?", (tg_user_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE access_requests
                SET tg_username = ?, status = 'pending', created_at = ?,
                    reviewed_at = NULL, reviewed_by = NULL
                WHERE tg_user_id = ?
                """,
                (tg_username, now_text(), tg_user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO access_requests (tg_user_id, tg_username, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (tg_user_id, tg_username, now_text()),
            )
        conn.commit()
    return get_request(tg_user_id) or {}


def mark_request(tg_user_id: int, status: str, reviewed_by: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE access_requests
            SET status = ?, reviewed_at = ?, reviewed_by = ?
            WHERE tg_user_id = ?
            """,
            (status, now_text(), reviewed_by, tg_user_id),
        )
        conn.commit()


def get_user_by_tg(tg_user_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        return row_to_dict(
            conn.execute(
                "SELECT * FROM ss_users WHERE tg_user_id = ?", (tg_user_id,)
            ).fetchone()
        )


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM ss_users WHERE id = ?", (user_id,)).fetchone()
        )


def list_users() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM ss_users ORDER BY id").fetchall()
        return [dict(row) for row in rows]


def used_ports() -> set[int]:
    with db() as conn:
        return {int(row[0]) for row in conn.execute("SELECT port FROM ss_users")}


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def random_port() -> int:
    occupied = used_ports()
    for _ in range(1000):
        port = random.randint(PORT_MIN, PORT_MAX)
        if port not in occupied and is_port_free(port):
            return port
    raise RuntimeError("无法找到可用随机端口。")


def generate_password() -> str:
    return base64.b64encode(secrets.token_bytes(16)).decode("ascii")


def get_public_host() -> str:
    env = load_env()
    configured = env.get("SS_PUBLIC_HOST", "").strip()
    if configured:
        return configured
    for url in ("https://api.ipify.org", "https://ip.sb", "https://icanhazip.com"):
        try:
            value = requests.get(url, timeout=8).text.strip()
            if value:
                return value
        except requests.RequestException:
            continue
    return "YOUR_SERVER_IP"


def ss_url(user: dict[str, Any]) -> str:
    userinfo = f"{user['method']}:{user['password']}"
    encoded = base64.urlsafe_b64encode(userinfo.encode()).decode().rstrip("=")
    name = quote(str(user.get("display_name") or f"user{user['id']}"))
    return f"ss://{encoded}@{get_public_host()}:{user['port']}#{name}"


def create_user(
    tg_user_id: int,
    tg_username: str,
    display_name: str,
    port: Optional[int] = None,
    expire_at: Optional[str] = None,
    traffic_limit_gb: int = DEFAULT_TRAFFIC_GB,
    speed_limit: str = DEFAULT_SPEED,
) -> dict[str, Any]:
    init_db()
    port = port or random_port()
    expire_at = expire_at or default_expire_date()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ss_users (
                tg_user_id, tg_username, display_name, port, method, password,
                expire_at, traffic_limit_gb, speed_limit, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                tg_user_id,
                tg_username,
                display_name,
                port,
                SS_METHOD,
                generate_password(),
                expire_at,
                int(traffic_limit_gb),
                speed_limit or DEFAULT_SPEED,
                now_text(),
                now_text(),
            ),
        )
        conn.commit()
    mark_request(tg_user_id, "approved", 0)
    render_singbox_config()
    restart_singbox()
    return get_user_by_tg(tg_user_id) or {}


def update_user(user_id: int, **updates: Any) -> None:
    allowed = {"expire_at", "traffic_limit_gb", "speed_limit", "display_name", "enabled"}
    items = [(key, value) for key, value in updates.items() if key in allowed]
    if not items:
        return
    assignments = ", ".join([f"{key} = ?" for key, _value in items])
    values = [value for _key, value in items]
    values.extend([now_text(), user_id])
    with db() as conn:
        conn.execute(
            f"UPDATE ss_users SET {assignments}, updated_at = ? WHERE id = ?", values
        )
        conn.commit()
    render_singbox_config()
    restart_singbox()


def delete_user(user_id: int) -> Optional[dict[str, Any]]:
    user = get_user(user_id)
    if not user:
        return None
    with db() as conn:
        conn.execute("DELETE FROM ss_users WHERE id = ?", (user_id,))
        conn.commit()
    render_singbox_config()
    restart_singbox()
    return user


def disable_expired_users() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute(
            "SELECT id FROM ss_users WHERE enabled = 1 AND expire_at < ?", (today,)
        ).fetchall()
        if not rows:
            return 0
        conn.execute(
            "UPDATE ss_users SET enabled = 0, updated_at = ? WHERE enabled = 1 AND expire_at < ?",
            (now_text(), today),
        )
        conn.commit()
    render_singbox_config()
    restart_singbox()
    return len(rows)


def reset_all() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    cache_dir = Path("/var/lib/boil-change-ip")
    if cache_dir.exists():
        for path in cache_dir.glob("*"):
            if path.is_file():
                path.unlink()
    init_db()
    render_singbox_config()
    restart_singbox()


def singbox_config(users: list[dict[str, Any]]) -> dict[str, Any]:
    inbounds = []
    for user in users:
        if not int(user.get("enabled", 1)):
            continue
        inbounds.append(
            {
                "type": "shadowsocks",
                "tag": f"ss-user-{user['id']}",
                "listen": "::",
                "listen_port": int(user["port"]),
                "method": user["method"],
                "password": user["password"],
            }
        )
    return {
        "log": {"level": "info", "timestamp": True},
        "inbounds": inbounds,
        "outbounds": [{"type": "direct", "tag": "direct"}],
    }


def render_singbox_config() -> None:
    SINGBOX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    config = singbox_config(list_users())
    tmp = SINGBOX_CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.move(str(tmp), str(SINGBOX_CONFIG))
    write_singbox_service()


def write_singbox_service() -> None:
    SINGBOX_SERVICE.write_text(
        f"""[Unit]
Description=Boil Change IP sing-box Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env sing-box run -c {SINGBOX_CONFIG}
Restart=always
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
""",
        encoding="utf-8",
    )


def restart_singbox() -> None:
    if not shutil.which("systemctl"):
        return
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "enable", "sing-box-boil"], check=False)
    subprocess.run(["systemctl", "restart", "sing-box-boil"], check=False)


def traffic_text(user: dict[str, Any]) -> str:
    return "暂未启用端口流量计数"


def format_user(user: dict[str, Any], include_url: bool = False) -> str:
    text = (
        f"用户 ID：{user['id']}\n"
        f"TG ID：{user['tg_user_id']}\n"
        f"用户名：{user['tg_username'] or '未知'}\n"
        f"显示名：{user['display_name']}\n"
        f"端口：{user['port']}\n"
        f"到期：{user['expire_at']}\n"
        f"月流量：{user['traffic_limit_gb']}GB 单向\n"
        f"速率：{user['speed_limit']}\n"
        f"状态：{'启用' if user['enabled'] else '禁用'}\n"
        f"流量：{traffic_text(user)}"
    )
    if include_url:
        text += f"\n\nSS 链接：\n<code>{ss_url(user)}</code>"
    return text


@dataclass
class ApprovalDraft:
    tg_user_id: int
    tg_username: str
    display_name: str
    port: int
    expire_at: str
    traffic_limit_gb: int
    speed_limit: str

    def as_text(self) -> str:
        return (
            "请确认开通参数：\n\n"
            f"TG ID：{self.tg_user_id}\n"
            f"用户名：{self.tg_username}\n"
            f"显示名：{self.display_name}\n"
            f"端口：{self.port}\n"
            f"到期日：{self.expire_at}\n"
            f"月流量：{self.traffic_limit_gb}GB 单向\n"
            f"速率：{self.speed_limit}\n\n"
            "点击确认后将创建用户并重载 sing-box。"
        )


def make_draft(tg_user_id: int, tg_username: str) -> ApprovalDraft:
    return ApprovalDraft(
        tg_user_id=tg_user_id,
        tg_username=tg_username or "未知",
        display_name=(tg_username or f"user_{tg_user_id}").replace("@", ""),
        port=random_port(),
        expire_at=default_expire_date(),
        traffic_limit_gb=DEFAULT_TRAFFIC_GB,
        speed_limit=DEFAULT_SPEED,
    )
