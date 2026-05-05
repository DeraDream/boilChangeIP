import base64
import json
import os
import random
import secrets
import shutil
import socket
import sqlite3
import subprocess
import calendar
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
                tg_user_id INTEGER UNIQUE,
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
                expire_disable_enabled INTEGER NOT NULL DEFAULT 1,
                traffic_limit_gb INTEGER NOT NULL DEFAULT 100,
                inbound_baseline_bytes INTEGER NOT NULL DEFAULT 0,
                outbound_baseline_bytes INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        migrate_db(conn)
        conn.commit()


def migrate_db(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(ss_users)").fetchall()
    }
    if "expire_disable_enabled" not in columns:
        conn.execute(
            "ALTER TABLE ss_users ADD COLUMN expire_disable_enabled INTEGER NOT NULL DEFAULT 1"
        )
    if "speed_limit" in columns:
        # Kept for old databases; new logic no longer reads or writes it.
        pass
    if "inbound_baseline_bytes" not in columns:
        conn.execute(
            "ALTER TABLE ss_users ADD COLUMN inbound_baseline_bytes INTEGER NOT NULL DEFAULT 0"
        )
    if "outbound_baseline_bytes" not in columns:
        conn.execute(
            "ALTER TABLE ss_users ADD COLUMN outbound_baseline_bytes INTEGER NOT NULL DEFAULT 0"
        )
    info = {
        row[1]: {"type": row[2], "notnull": row[3], "default": row[4], "pk": row[5]}
        for row in conn.execute("PRAGMA table_info(ss_users)").fetchall()
    }
    if info.get("tg_user_id", {}).get("notnull"):
        conn.execute("ALTER TABLE ss_users RENAME TO ss_users_old")
        conn.execute(
            """
            CREATE TABLE ss_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER UNIQUE,
                tg_username TEXT NOT NULL DEFAULT '未知',
                display_name TEXT NOT NULL,
                port INTEGER NOT NULL UNIQUE,
                method TEXT NOT NULL,
                password TEXT NOT NULL,
                expire_at TEXT NOT NULL,
                expire_disable_enabled INTEGER NOT NULL DEFAULT 1,
                traffic_limit_gb INTEGER NOT NULL DEFAULT 100,
                inbound_baseline_bytes INTEGER NOT NULL DEFAULT 0,
                outbound_baseline_bytes INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ss_users (
                id, tg_user_id, tg_username, display_name, port, method, password,
                expire_at, expire_disable_enabled, traffic_limit_gb,
                inbound_baseline_bytes, outbound_baseline_bytes,
                enabled, created_at, updated_at
            )
            SELECT
                id, tg_user_id, tg_username, display_name, port, method, password,
                expire_at, expire_disable_enabled, traffic_limit_gb,
                inbound_baseline_bytes, outbound_baseline_bytes,
                enabled, created_at, updated_at
            FROM ss_users_old
            """
        )
        conn.execute("DROP TABLE ss_users_old")


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
    tg_user_id: Optional[int],
    tg_username: str,
    display_name: str,
    port: Optional[int] = None,
    expire_at: Optional[str] = None,
    expire_disable_enabled: int = 1,
    traffic_limit_gb: int = DEFAULT_TRAFFIC_GB,
) -> dict[str, Any]:
    init_db()
    port = port or random_port()
    expire_at = expire_at or default_expire_date()
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO ss_users (
                tg_user_id, tg_username, display_name, port, method, password,
                expire_at, expire_disable_enabled, traffic_limit_gb, enabled, created_at, updated_at
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
                int(expire_disable_enabled),
                int(traffic_limit_gb),
                now_text(),
                now_text(),
            ),
        )
        user_id = int(cursor.lastrowid)
        conn.commit()
    if tg_user_id is not None:
        mark_request(tg_user_id, "approved", 0)
    render_singbox_config()
    restart_singbox()
    return get_user(user_id) or {}


def update_user(user_id: int, **updates: Any) -> None:
    allowed = {
        "expire_at",
        "expire_disable_enabled",
        "traffic_limit_gb",
        "display_name",
        "enabled",
    }
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


def add_one_month(date_text: str) -> str:
    source = datetime.strptime(date_text, "%Y-%m-%d")
    month = source.month + 1
    year = source.year
    if month > 12:
        month = 1
        year += 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day).strftime("%Y-%m-%d")


def roll_date_forward(date_text: str, today: str) -> str:
    next_date = date_text
    while next_date < today:
        next_date = add_one_month(next_date)
    return next_date


def disable_expired_users() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id FROM ss_users
            WHERE enabled = 1 AND expire_disable_enabled = 1 AND expire_at < ?
            """,
            (today,),
        ).fetchall()
        if rows:
            conn.execute(
                """
                UPDATE ss_users
                SET enabled = 0, updated_at = ?
                WHERE enabled = 1 AND expire_disable_enabled = 1 AND expire_at < ?
                """,
                (now_text(), today),
            )
            conn.commit()

    renew_monthly_users(today)
    if rows:
        render_singbox_config()
        restart_singbox()
    return len(rows)


def renew_monthly_users(today: str) -> int:
    renewed = 0
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM ss_users
            WHERE enabled = 1 AND expire_disable_enabled = 0 AND expire_at < ?
            """,
            (today,),
        ).fetchall()
        for row in rows:
            user = dict(row)
            raw = get_user_traffic_raw(user)
            conn.execute(
                """
                UPDATE ss_users
                SET expire_at = ?, inbound_baseline_bytes = ?,
                    outbound_baseline_bytes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    roll_date_forward(user["expire_at"], today),
                    raw["inbound_bytes"],
                    raw["outbound_bytes"],
                    now_text(),
                    user["id"],
                ),
            )
            renewed += 1
        conn.commit()
    return renewed


def reset_all() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    cache_dir = Path("/var/lib/boil-change-ip")
    if cache_dir.exists():
        for path in cache_dir.glob("*"):
            if path.is_file():
                path.unlink()
    clear_traffic_rules()
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
    ensure_traffic_rules()


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


def iptables_bins() -> list[str]:
    return [name for name in ("iptables", "ip6tables") if shutil.which(name)]


def ensure_chain(bin_name: str, chain: str, parent: str) -> None:
    subprocess.run([bin_name, "-w", "-N", chain], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check = subprocess.run([bin_name, "-w", "-C", parent, "-j", chain], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode != 0:
        subprocess.run([bin_name, "-w", "-I", parent, "1", "-j", chain], check=False)


def ensure_rule(bin_name: str, chain: str, port: int, direction: str, comment: str) -> None:
    port_flag = "--dport" if direction == "in" else "--sport"
    check = subprocess.run(
        [
            bin_name,
            "-w",
            "-C",
            chain,
            "-p",
            "tcp",
            port_flag,
            str(port),
            "-m",
            "comment",
            "--comment",
            comment,
            "-j",
            "RETURN",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        subprocess.run(
            [
                bin_name,
                "-w",
                "-A",
                chain,
                "-p",
                "tcp",
                port_flag,
                str(port),
                "-m",
                "comment",
                "--comment",
                comment,
                "-j",
                "RETURN",
            ],
            check=False,
        )


def ensure_traffic_rules() -> None:
    users = list_users()
    for bin_name in iptables_bins():
        ensure_chain(bin_name, "BOIL_SS_IN", "INPUT")
        ensure_chain(bin_name, "BOIL_SS_OUT", "OUTPUT")
        for user in users:
            ensure_rule(bin_name, "BOIL_SS_IN", int(user["port"]), "in", f"boil_ss_user_{user['id']}_in")
            ensure_rule(bin_name, "BOIL_SS_OUT", int(user["port"]), "out", f"boil_ss_user_{user['id']}_out")


def clear_traffic_rules() -> None:
    for bin_name in iptables_bins():
        for parent, chain in (("INPUT", "BOIL_SS_IN"), ("OUTPUT", "BOIL_SS_OUT")):
            while True:
                result = subprocess.run(
                    [bin_name, "-w", "-D", parent, "-j", chain],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode != 0:
                    break
            subprocess.run(
                [bin_name, "-w", "-F", chain],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [bin_name, "-w", "-X", chain],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def read_counter(comment: str) -> int:
    total = 0
    for bin_name in iptables_bins():
        save_bin = f"{bin_name}-save"
        if not shutil.which(save_bin):
            continue
        result = subprocess.run(
            [save_bin, "-c"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            if comment not in line:
                continue
            if not line.startswith("[") or "]" not in line:
                continue
            counters = line.split("]", 1)[0].strip("[")
            parts = counters.split(":")
            if len(parts) == 2 and parts[1].isdigit():
                total += int(parts[1])
    return total


def single_way_bytes(usage: dict[str, int]) -> int:
    return max(usage["inbound_bytes"], usage["outbound_bytes"])


def traffic_limit_bytes(user: dict[str, Any]) -> int:
    return int(user["traffic_limit_gb"]) * 1024**3


def traffic_text(user: dict[str, Any]) -> str:
    usage = get_user_traffic(user)
    limit_bytes = traffic_limit_bytes(user)
    single_way = max(usage["inbound_bytes"], usage["outbound_bytes"])
    percent = (single_way / limit_bytes * 100) if limit_bytes else 0
    return (
        f"入站：{format_bytes(usage['inbound_bytes'])}\n"
        f"出站：{format_bytes(usage['outbound_bytes'])}\n"
        f"单向计费：{format_bytes(single_way)} / {user['traffic_limit_gb']}GB "
        f"({percent:.2f}%)"
    )


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f}{unit}"
        size /= 1024


def get_user_traffic_raw(user: dict[str, Any]) -> dict[str, int]:
    ensure_traffic_rules()
    user_id = user["id"]
    return {
        "inbound_bytes": read_counter(f"boil_ss_user_{user_id}_in"),
        "outbound_bytes": read_counter(f"boil_ss_user_{user_id}_out"),
    }


def get_user_traffic(user: dict[str, Any]) -> dict[str, int]:
    raw = get_user_traffic_raw(user)
    return {
        "inbound_bytes": max(
            0, raw["inbound_bytes"] - int(user.get("inbound_baseline_bytes") or 0)
        ),
        "outbound_bytes": max(
            0, raw["outbound_bytes"] - int(user.get("outbound_baseline_bytes") or 0)
        ),
    }


def enforce_traffic_limits() -> int:
    disabled = 0
    users = [user for user in list_users() if int(user.get("enabled", 1))]
    with db() as conn:
        for user in users:
            limit_bytes = traffic_limit_bytes(user)
            if limit_bytes <= 0:
                continue
            usage = get_user_traffic(user)
            if single_way_bytes(usage) < limit_bytes:
                continue
            conn.execute(
                "UPDATE ss_users SET enabled = 0, updated_at = ? WHERE id = ?",
                (now_text(), user["id"]),
            )
            disabled += 1
        conn.commit()
    if disabled:
        render_singbox_config()
        restart_singbox()
    return disabled


def traffic_report() -> str:
    users = list_users()
    if not users:
        return "暂无 SS 用户。"
    total_in = 0
    total_out = 0
    lines = ["SS 流量日报", ""]
    for user in users:
        usage = get_user_traffic(user)
        total_in += usage["inbound_bytes"]
        total_out += usage["outbound_bytes"]
        single_way = single_way_bytes(usage)
        lines.append(
            f"用户 {user['id']}｜{user['display_name']}｜端口 {user['port']}\n"
            f"入站：{format_bytes(usage['inbound_bytes'])}，"
            f"出站：{format_bytes(usage['outbound_bytes'])}，"
            f"单向：{format_bytes(single_way)} / {user['traffic_limit_gb']}GB"
        )
    lines.insert(1, f"整体入站：{format_bytes(total_in)}")
    lines.insert(2, f"整体出站：{format_bytes(total_out)}")
    lines.insert(3, f"整体单向：{format_bytes(max(total_in, total_out))}")
    return "\n".join(lines)


def format_user(user: dict[str, Any], include_url: bool = False) -> str:
    text = (
        f"用户 ID：{user['id']}\n"
        f"TG ID：{user['tg_user_id'] or '未绑定'}\n"
        f"用户名：{user['tg_username'] or '未知'}\n"
        f"显示名：{user['display_name']}\n"
        f"端口：{user['port']}\n"
        f"到期：{user['expire_at']}\n"
        f"到期禁用：{'开启' if int(user.get('expire_disable_enabled', 1)) else '关闭'}\n"
        f"月流量：{user['traffic_limit_gb']}GB 单向\n"
        f"状态：{'启用' if user['enabled'] else '禁用'}\n"
        f"流量：{traffic_text(user)}"
    )
    if include_url:
        text += f"\n\nSS 链接：\n<code>{ss_url(user)}</code>"
    return text


@dataclass
class ApprovalDraft:
    tg_user_id: Optional[int]
    tg_username: str
    display_name: str
    port: int
    expire_at: str
    expire_disable_enabled: int
    traffic_limit_gb: int

    @property
    def key(self) -> str:
        return str(self.tg_user_id) if self.tg_user_id is not None else "manual"

    def as_text(self) -> str:
        return (
            "请确认开通参数：\n\n"
            f"TG ID：{self.tg_user_id if self.tg_user_id is not None else '未绑定'}\n"
            f"用户名：{self.tg_username}\n"
            f"显示名：{self.display_name}\n"
            f"端口：{self.port}\n"
            f"到期日：{self.expire_at}\n"
            f"到期禁用：{'开启' if self.expire_disable_enabled else '关闭'}\n"
            f"月流量：{self.traffic_limit_gb}GB 单向\n"
            "点击确认后将创建用户并重载 sing-box。"
        )


def make_draft(tg_user_id: int, tg_username: str) -> ApprovalDraft:
    return ApprovalDraft(
        tg_user_id=tg_user_id,
        tg_username=tg_username or "未知",
        display_name=(tg_username or f"user_{tg_user_id}").replace("@", ""),
        port=random_port(),
        expire_at=default_expire_date(),
        expire_disable_enabled=1,
        traffic_limit_gb=DEFAULT_TRAFFIC_GB,
    )


def make_manual_draft(tg_user_id: Optional[int], tg_username: str = "未知") -> ApprovalDraft:
    display_seed = tg_username.replace("@", "") if tg_username and tg_username != "未知" else ""
    if not display_seed:
        display_seed = f"tg_{tg_user_id}" if tg_user_id is not None else "manual_user"
    return ApprovalDraft(
        tg_user_id=tg_user_id,
        tg_username=tg_username or "未知",
        display_name=display_seed,
        port=random_port(),
        expire_at=default_expire_date(),
        expire_disable_enabled=1,
        traffic_limit_gb=DEFAULT_TRAFFIC_GB,
    )


def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else default


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()
