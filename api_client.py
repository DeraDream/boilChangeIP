import threading
import ipaddress
from typing import Any, Dict, List, Optional, Tuple

import requests


class IPPanelClient:
    def __init__(self, token: str, timeout: int = 20):
        self.base_url = "https://ippanel.boil.network"
        self.token = token.strip()
        self.timeout = timeout
        self._lock = threading.Lock()
        self.session = self._new_session()

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
            }
        )
        return session

    def _post(self, path: str) -> Tuple[bool, Dict[str, Any] | str]:
        if not self.token:
            return False, "IPPanel API Token 未配置。"

        try:
            with self._lock:
                resp = self.session.post(
                    f"{self.base_url}{path}",
                    timeout=self.timeout,
                )
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
                if resp.ok:
                    return True, data
                if isinstance(data, dict) and data.get("error"):
                    return False, str(data["error"])
                return False, f"HTTP {resp.status_code}: {resp.text[:500]}"
        except requests.RequestException as exc:
            return False, f"HTTP 请求失败：{exc}"

    def get_raw_data(self) -> Optional[Dict[str, Any]]:
        ok, result = self._post("/api/v1/getIP")
        if ok and isinstance(result, dict):
            return result
        return None

    def get_current_ip(self) -> Tuple[bool, str]:
        ok, result = self._post("/api/v1/getIP")
        if not ok:
            return False, str(result)
        if not isinstance(result, dict):
            return False, "IPPanel 返回了无效 JSON。"
        if result.get("ok"):
            ip = str(result.get("ip") or "").strip()
            try:
                ipaddress.IPv4Address(ip)
            except ipaddress.AddressValueError:
                return False, f"IPPanel 未返回有效 IPv4：{result}"
            return True, ip
        return False, str(result.get("error") or result)

    def get_formatted_status(self) -> str:
        ok, result = self.get_current_ip()
        if not ok:
            return f"获取当前 IP 失败：{result}"
        return f"=== IPPanel 状态 ===\n当前公网 IP：{result}"

    def get_devices_list(self) -> List[Dict[str, Any]]:
        ok, result = self.get_current_ip()
        if not ok:
            return []
        return [
            {
                "name": "IPPanel API",
                "current_ip": result,
            }
        ]

    def change_ip(self) -> Tuple[bool, str]:
        ok, result = self._post("/api/v1/changeIP/")
        if not ok:
            return False, str(result)
        if not isinstance(result, dict):
            return False, "IPPanel 返回了无效 JSON。"
        if result.get("ok"):
            message = str(result.get("message") or "正在执行更换 IP")
            uses_left = result.get("uses_left")
            next_allowed_at = result.get("next_allowed_at")
            extras = []
            if uses_left is not None:
                extras.append(f"剩余次数：{uses_left}")
            if next_allowed_at is not None:
                extras.append(f"下次可用时间戳：{next_allowed_at}")
            return True, "\n".join([message, *extras])
        return False, str(result.get("error") or result)
