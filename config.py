from pathlib import Path
from typing import Dict, List


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
VERSION_FILE = BASE_DIR / "VERSION"
MONITOR_SCRIPT = BASE_DIR / "monitor_ip.sh"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    value = value.strip().strip('"').strip("'")
    return key.strip(), value


def load_env() -> Dict[str, str]:
    data: Dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(line)
            if parsed:
                key, value = parsed
                data[key] = value
    return data


def parse_allowed_users(raw: str) -> List[int]:
    users: List[int] = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            users.append(int(item))
        except ValueError:
            continue
    return users


def get_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.0.0"
