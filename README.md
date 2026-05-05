# Boil Change IP

Telegram bot for querying IPPanel devices, changing device IPs, and generating current IP quality reports.

## Features

- Query IPPanel device name, status, interface, and current public IP.
- Change IP through the original IPPanel `/api/reconnect` endpoint.
- Telegram inline menu:
  - Bot status
  - Device list and click-to-change-IP
  - Current IP quality report PNG
- Global VPS command: `boiltg`
  - Update script
  - Modify config
  - Uninstall script
  - View script status
- Version-aware update. The menu compares local `VERSION` with remote `origin/main:VERSION`; if the remote version is newer, it pulls the latest code, installs dependencies, and restarts the service.

## One-command install on VPS

Run as root on a Linux VPS:

```bash
apt-get update && apt-get install -y git curl && mkdir -p /opt && cd /opt && git clone git@github.com:DeraDream/boilChangeIP.git boil-change-ip && cd boil-change-ip && bash install.sh
```

If your VPS only has HTTPS access to GitHub:

```bash
apt-get update && apt-get install -y git curl && mkdir -p /opt && cd /opt && git clone https://github.com/DeraDream/boilChangeIP.git boil-change-ip && cd boil-change-ip && bash install.sh
```

The installer will ask for:

- IPPanel account
- IPPanel password
- Telegram Bot Token
- Telegram User ID

After installation, the bot service starts automatically.

## Global menu

After installation:

```bash
boiltg
```

Menu:

```text
1. Update script
2. Modify config
3. Uninstall script
4. View script status
0. Exit
```

The config submenu:

```text
1. Modify Telegram Bot Token
2. Modify Telegram User ID
3. Modify IPPanel account
4. Modify IPPanel password
0. Back
```

Every config change is applied immediately and the service is restarted.

## Telegram commands

```text
/start
/help
/menu
/list
/ip_change
```

`/menu` opens the bot menu.

`Device list / change IP` fetches the same device list as `/list`, shows device names and current IPs, and changes IP immediately after a device button is clicked.

`Current IP quality` runs:

```bash
bash <(curl -sL IP.Check.Place) -4 -E
```

It captures ANSI output, renders a PNG with `ansilove`, and sends the image back to Telegram.

## Service commands

```bash
systemctl status boil-change-ip
systemctl restart boil-change-ip
journalctl -u boil-change-ip -f
```

## Files

- `bot_main.py`: Telegram bot entrypoint.
- `api_client.py`: IPPanel API client.
- `monitor_ip.sh`: IP quality image generator and optional Telegram notifier.
- `install.sh`: interactive installer.
- `scripts/boiltg.sh`: global management menu.
- `.env`: local runtime config, created by installer and ignored by Git.
- `VERSION`: project version used for update comparison.
