import threading
from typing import Any, Dict, List, Optional, Tuple

import requests


class IPPanelClient:
    def __init__(self, account: str, password: str, timeout: int = 20):
        self.base_url = "https://ippanel.boil.network"
        self.account = account
        self.password = password
        self.timeout = timeout
        self._lock = threading.Lock()
        self.session = self._new_session()

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            }
        )
        return session

    def login(self) -> bool:
        """登录 IPPanel，并在 session 中保存 cookie。"""
        payload = {"account": self.account, "password": self.password}
        try:
            with self._lock:
                resp = self.session.post(
                    f"{self.base_url}/login", data=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                return "session" in self.session.cookies.get_dict()
        except requests.RequestException:
            return False

    def get_raw_data(self) -> Optional[Dict[str, Any]]:
        """获取原始设备 JSON 数据。"""
        try:
            with self._lock:
                resp = self.session.post(
                    f"{self.base_url}/api/query_all", timeout=self.timeout
                )
                resp.raise_for_status()
                return resp.json()
        except (requests.RequestException, ValueError):
            return None

    def get_formatted_status(self) -> str:
        """返回 /list 使用的中文格式化文本。"""
        if not self.login():
            return "登录失败，请检查 IPPanel 账号和密码。"

        data = self.get_raw_data()
        if not data:
            return "获取设备数据失败。"

        limit = data.get("daily_limit", 0)
        used = data.get("daily_used", 0)

        report = "=== 设备状态 ===\n"
        report += f"今日换 IP 额度：已用 {used} / 总共 {limit}\n\n"

        zone_items = data.get("zone_items", [])
        results = data.get("results", {})

        if not zone_items:
            return report + "未找到设备。"

        for item in zone_items:
            product_name = item.get("product_name") or "未知设备"
            status = item.get("status") or "未知"
            router_id = item.get("router_id")
            interface = item.get("interface")
            public_ip = results.get(router_id, {}).get(interface, "未知 IP")
            status_text = "正常" if status == "ok" else f"异常（{status}）"

            report += f"设备：{product_name}\n"
            report += f"状态：{status_text}\n"
            report += f"路由/接口：{router_id} / {interface}\n"
            report += f"当前公网 IP：{public_ip}\n"
            report += "-" * 30 + "\n"

        return report

    def get_devices_list(self) -> List[Dict[str, Any]]:
        """返回结构化设备列表，用于 Telegram 按钮。"""
        if not self.login():
            return []

        data = self.get_raw_data()
        if not data:
            return []

        devices = []
        results = data.get("results", {})
        for item in data.get("zone_items", []):
            router_id = item.get("router_id")
            interface = item.get("interface")
            current_ip = results.get(router_id, {}).get(interface, "未知")
            if not router_id or not interface:
                continue
            devices.append(
                {
                    "name": item.get("product_name") or f"{router_id}/{interface}",
                    "router_id": router_id,
                    "interface": interface,
                    "current_ip": current_ip,
                }
            )
        return devices

    def change_ip(self, router_id: str, interface: str) -> Tuple[bool, str]:
        """调用原有 reconnect 接口执行换 IP。"""
        if not self.login():
            return False, "登录失败。"

        payload = {"router_id": router_id, "interface": interface}
        try:
            with self._lock:
                resp = self.session.post(
                    f"{self.base_url}/api/reconnect",
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                result = resp.json()
        except requests.RequestException as exc:
            return False, f"HTTP 请求失败：{exc}"
        except ValueError:
            return False, "IPPanel 返回了无效 JSON。"

        if result.get("ok"):
            return True, str(result.get("new_ip") or "未知")
        return False, str(result)
