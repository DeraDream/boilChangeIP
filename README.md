# Boil Change IP

Boil Change IP 是一个用于管理 IPPanel 换 IP 的 Telegram Bot。它可以查看设备当前公网 IP，点击设备立即换 IP，并生成当前 IP 的质量检测图片。

项目地址：

```text
https://github.com/DeraDream/boilChangeIP.git
```

## 功能

- 查看 IPPanel 设备列表、设备名称、状态、接口和当前公网 IP。
- 点击 Telegram Bot 按钮立即执行换 IP。
- 保留原有换 IP 逻辑，通过 IPPanel `/api/reconnect` 接口执行。
- 生成当前 IP 质量检测图片，并发送到 Telegram。
- 提供 VPS 全局管理命令 `boiltg`。
- 支持版本号更新，更新时保留用户 `.env` 配置。

## 安装

在 Linux VPS 上使用 `root` 执行下面这一条命令即可：

```bash
apt-get update && apt-get install -y git curl && mkdir -p /opt && cd /opt && git clone https://github.com/DeraDream/boilChangeIP.git boil-change-ip && cd boil-change-ip && bash install.sh
```

安装脚本会先检查依赖。如果缺少依赖，会自动安装并再次检查。依赖检查通过后，才会继续询问配置。

会检查的依赖包括：

- `bash`
- `curl`
- `git`
- `python3`
- `python3-venv`
- `systemctl`
- `jq`
- `bc`
- `dnsutils`
- `iproute2`
- `netcat-openbsd`
- `fonts-noto-cjk`

Python 依赖会自动安装，其中包含 `Pillow`，用于渲染中文 ANSI 报告，并把过高的 IP 质量长图切成 Telegram 可发送的多张图片。

安装过程中会依次询问：

- IPPanel 账号
- IPPanel 密码
- Telegram Bot Token
- Telegram 用户 ID

安装完成后会自动创建并启动 systemd 服务。

## 全局菜单

安装完成后，在 VPS 任意位置执行：

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

## 修改配置

在 `boiltg` 菜单中选择 `2. 修改配置`，会进入二级菜单：

```text
1. 修改 Telegram Bot Token
2. 修改 Telegram 用户 ID
3. 修改 IPPanel 账号
4. 修改 IPPanel 密码
0. 返回
```

修改后会立即保存配置，并自动重启服务。

## 更新脚本

在 `boiltg` 菜单中选择 `1. 更新脚本`。

更新逻辑：

1. 比较本地 `VERSION` 和远程 `origin/main:VERSION`。
2. 远程版本更高时才更新。
3. 更新前备份 `.env` 用户配置。
4. 拉取远程最新代码。
5. 强制替换本地项目文件为远程版本。
6. 恢复 `.env` 用户配置。
7. 安装或更新 Python 依赖。
8. 刷新全局命令和 systemd 服务文件。
9. 立即重启服务。

更新日志会同时输出到终端，并保存到：

```bash
/var/log/boil-change-ip-update.log
```

## Telegram Bot 菜单

Bot 支持以下命令：

```text
/start
/help
/menu
/list
/ip_change
```

`/menu` 会打开 Bot 菜单，并在 Telegram 输入框底部启用自定义按钮：

```text
1. Bot 状态
2. 获取列表/更换 IP
3. 获取当前 IP 质量
```

说明：

- `Bot 状态`：查看 Bot 是否运行、当前版本、授权用户和 IPPanel 账号。
- `获取列表/更换 IP`：显示设备名称和当前 IP，点击设备按钮后立即换 IP。
- `获取当前 IP 质量`：运行双栈完整 IP 质量检测脚本，生成 PNG 图片并发送到 Telegram。

这些按钮会同时出现在聊天消息里的内联菜单，以及 Telegram 输入框底部的自定义键盘中。

## IP 质量检测

检测脚本会执行：

```bash
bash <(curl -Ls https://IP.Check.Place)
```

程序内部会使用 IPQuality 的 `-o` 参数把最终 ANSI 报告输出到临时文件，再用 `ansilove` 渲染 PNG。这样截图取的是最终报告，而不是检测过程中的滚动输出。

执行流程：

1. 获取当前公网 IPv4。
2. 读取本地缓存的旧 IP。
3. 对比新旧 IP。
4. 需要检测时调用远程脚本。
5. 捕获 ANSI 输出。
6. 使用 `ansilove` 生成 PNG 图片。
7. 发送 Telegram 通知或返回图片给 Bot。
8. 清理临时文件。

## 服务管理

查看状态：

```bash
systemctl status boil-change-ip
```

重启服务：

```bash
systemctl restart boil-change-ip
```

查看日志：

```bash
journalctl -u boil-change-ip -f
```

## 卸载

执行：

```bash
boiltg
```

选择：

```text
3. 卸载脚本
```

卸载会移除 systemd 服务和全局命令 `boiltg`。项目文件会保留在 `/opt/boil-change-ip`。

## 文件说明

- `bot_main.py`：Telegram Bot 主程序。
- `api_client.py`：IPPanel API 客户端。
- `monitor_ip.sh`：IP 质量图片生成脚本。
- `install.sh`：交互式安装脚本。
- `scripts/boiltg.sh`：VPS 全局管理菜单。
- `.env`：用户配置文件，由安装脚本生成，不会提交到 Git。
- `VERSION`：版本号，用于更新判断。
