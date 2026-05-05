# Boil Change IP

这是一个用于管理 IPPanel 换 IP 的 Telegram 机器人，支持查询设备当前公网 IP、点击设备立即换 IP，以及生成当前 IP 质量检测图片。

## 功能

- 查询 IPPanel 设备名称、状态、接口和当前公网 IP。
- 保持原有换 IP 逻辑，通过 IPPanel `/api/reconnect` 接口执行换 IP。
- Telegram 机器人菜单：
  - Bot 状态
  - 获取列表并点击设备立即换 IP
  - 获取当前 IP 质量图片
- VPS 全局命令：`boiltg`
  - 更新脚本
  - 修改配置
  - 卸载脚本
  - 查看脚本状态
- 支持版本号更新。菜单会比较本地 `VERSION` 和远程 `origin/main:VERSION`，远程版本更高时会备份 `.env` 用户配置、替换本地项目文件为远程版本、恢复配置、安装依赖并立即重启服务。

## VPS 一键安装

在 Linux VPS 上使用 root 执行。默认推荐 HTTPS 安装，不需要在 VPS 上配置 GitHub SSH Key：

```bash
apt-get update && apt-get install -y git curl && mkdir -p /opt && cd /opt && git clone https://github.com/DeraDream/boilChangeIP.git boil-change-ip && cd boil-change-ip && bash install.sh
```

如果你的 VPS 已经给 `root` 用户配置好了 GitHub SSH Key，也可以使用 SSH 安装：

```bash
apt-get update && apt-get install -y git curl && mkdir -p /opt && cd /opt && git clone git@github.com:DeraDream/boilChangeIP.git boil-change-ip && cd boil-change-ip && bash install.sh
```

如果 SSH 安装出现 `Permission denied (publickey)`，说明当前 VPS 用户没有可用的 GitHub SSH 私钥，请改用上面的 HTTPS 命令。

安装脚本会先检查所需依赖，缺少依赖时会自动依次安装。安装完成后会再次复查，确认 `bash`、`curl`、`git`、`python3`、`python3-venv`、`systemctl`、`ansilove` 都可用后，才会继续询问配置。

安装脚本随后会依次询问：

- IPPanel 账号
- IPPanel 密码
- Telegram Bot Token
- Telegram 用户 ID

安装完成后，Bot 服务会自动加入 systemd 并启动。

## 全局菜单

安装后，在 VPS 任意位置执行：

```bash
boiltg
```

菜单如下：

```text
1. 更新脚本
2. 修改配置
3. 卸载脚本
4. 查看脚本状态
0. 退出
```

修改配置的二级菜单：

```text
1. 修改 Telegram Bot Token
2. 修改 Telegram 用户 ID
3. 修改 IPPanel 账号
4. 修改 IPPanel 密码
0. 返回
```

每次修改配置后都会立即保存，并自动重启 Bot 服务使配置生效。

更新脚本会在终端输出每一步进度，并把日志保存到：

```bash
/var/log/boil-change-ip-update.log
```

## Telegram 命令

```text
/start
/help
/menu
/list
/ip_change
```

`/menu` 打开 Bot 菜单。

`获取列表/更换 IP` 会拉取和 `/list` 相同的设备列表，显示设备名称和当前 IP。点击设备按钮后会立即执行换 IP。

`获取当前 IP 质量` 会执行：

```bash
bash <(curl -sL IP.Check.Place) -4 -E
```

脚本会捕获 ANSI 输出，用 `ansilove` 生成 PNG 图片，并把图片发送回 Telegram。

## 服务命令

```bash
systemctl status boil-change-ip
systemctl restart boil-change-ip
journalctl -u boil-change-ip -f
```

## 文件说明

- `bot_main.py`：Telegram Bot 主程序。
- `api_client.py`：IPPanel API 客户端。
- `monitor_ip.sh`：IP 质量图片生成脚本，也支持可选 Telegram 通知。
- `install.sh`：交互式安装脚本。
- `scripts/boiltg.sh`：VPS 全局管理菜单。
- `.env`：本地运行配置，由安装脚本创建，不会提交到 Git。
- `VERSION`：项目版本号，用于判断是否需要更新。
