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
        """Login to IPPanel and keep cookies in the session."""
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
        """Fetch raw device JSON data."""
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
        """Return formatted text for /list."""
        if not self.login():
            return "Login failed. Please check IPPanel account and password."

        data = self.get_raw_data()
        if not data:
            return "Failed to fetch device data."

        limit = data.get("daily_limit", 0)
        used = data.get("daily_used", 0)

        report = "=== Device Status ===\n"
        report += f"Daily change-IP quota: used {used} / total {limit}\n\n"

        zone_items = data.get("zone_items", [])
        results = data.get("results", {})

        if not zone_items:
            return report + "No devices found."

        for item in zone_items:
            product_name = item.get("product_name") or "Unknown device"
            status = item.get("status") or "unknown"
            router_id = item.get("router_id")
            interface = item.get("interface")
            public_ip = results.get(router_id, {}).get(interface, "Unknown IP")
            status_text = "OK" if status == "ok" else f"Abnormal ({status})"

            report += f"Device: {product_name}\n"
            report += f"Status: {status_text}\n"
            report += f"Router/Interface: {router_id} / {interface}\n"
            report += f"Current public IP: {public_ip}\n"
            report += "-" * 30 + "\n"

        return report

    def get_devices_list(self) -> List[Dict[str, Any]]:
        """Return structured devices for Telegram inline buttons."""
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
            current_ip = results.get(router_id, {}).get(interface, "Unknown")
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
        """Call the original reconnect endpoint to change IP."""
        if not self.login():
            return False, "Login failed."

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
            return False, f"HTTP request failed: {exc}"
        except ValueError:
            return False, "Invalid JSON response from IPPanel."

        if result.get("ok"):
            return True, str(result.get("new_ip") or "Unknown")
        return False, str(result)
