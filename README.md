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
- 支持 sing-box SS 2022 用户管理，每个用户独立端口。
- 支持访客申请 SS 链接，管理员审核后创建用户。

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
- SS 公网地址或域名，可留空自动获取

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
5. 新增用户
6. 用户管理
7. 删除用户
8. 初始化脚本
9. TG通知
10. 绑定域名
0. 退出
```

## 修改配置

在 `boiltg` 菜单中选择 `2. 修改配置`，会进入二级菜单：

```text
1. 修改 Telegram Bot Token
2. 修改 Telegram 用户 ID
3. 修改 IPPanel 账号
4. 修改 IPPanel 密码
5. 修改 SS 公网地址/域名
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

`/menu` 会打开 Bot 菜单，并在 Telegram 输入框底部启用自定义按钮。管理员按钮超过 4 个时会自动按一行两个展示：

```text
1. Bot 状态
2. 获取列表/更换 IP
3. 获取当前 IP 质量
4. 生成用户
5. 用户管理
6. 删除用户
7. TG 通知
8. 绑定域名
```

说明：

- `Bot 状态`：查看 Bot 是否运行、当前版本、授权用户、IPPanel 账号、SS 用户数、整体入站/出站/单向流量，以及每个用户的入站/出站/单向流量。
- `获取列表/更换 IP`：显示设备名称和当前 IP，点击设备按钮后立即换 IP。
- `获取当前 IP 质量`：运行双栈完整 IP 质量检测脚本，生成 PNG 图片并发送到 Telegram。

这些按钮会同时出现在聊天消息里的内联菜单，以及 Telegram 输入框底部的自定义键盘中。

## SS 用户申请与授权

未授权用户点击 `/start` 后只会看到：

```text
你的 ID 是：xxxxxx
申请 SS 链接
```

用户点击 `申请 SS 链接` 后，Bot 会通知管理员：

```text
用户 xxx 申请 SS 链接
ID：xxxxxx
```

管理员可以选择 `接受` 或 `拒绝`。

管理员点击 `接受` 后会进入开通参数确认页：

- 端口：默认随机未占用端口，可修改
- 到期日：默认当前日期一个月后，可修改
- 到期禁用：默认开启，可关闭
- 月流量：默认 100GB 单向，可修改

到期禁用开启时，用户到期后会自动停用。到期禁用关闭时，用户不会停用，到期日会按月向后滚动，并从新的周期重新统计月单向流量。

确认创建后会：

1. 写入 SQLite 用户表。
2. 生成 sing-box Shadowsocks 2022 inbound。
3. 重载 `sing-box-boil` 服务。
4. 通知用户已开通。

管理员也可以主动选择 `生成用户` 创建 SS 用户。主动创建时 TG ID 可留空，留空后该用户只生成 SS 链接，不绑定 Telegram 账号；只有用户通过 Bot 申请 SS 链接时，才会强制绑定申请人的 TG ID。

已授权用户底部只有：

```text
我的链接
更换 IP
```

`我的链接` 等价于 `/my_ss`，会返回 SS 链接、到期日、到期禁用状态、月单向流量和状态。Telegram Bot 中展示的 SS 链接会以可点击链接形式高亮显示。

管理员进入 `用户管理` 时，每个用户详情也会带上当前 SS 链接。

## 绑定域名

管理员可以在 TG 管理菜单或 SSH `boiltg` 菜单中选择 `绑定域名`。输入域名后需要二次确认，确认时发送 `YES` 或 `yes` 均可。确认后脚本会写入 `SS_PUBLIC_HOST`、重载 `sing-box-boil`、重启 Bot 服务，并通知管理员：

```text
域名更新成功，所有链接已更新。
```

已绑定 TG ID 的用户会收到自己的最新 SS 链接；未绑定 TG ID 的手动用户会通知管理员。后续生成的所有 SS 链接都会优先使用当前绑定域名，未绑定域名时继续自动使用服务器公网 IP。

## TG 流量通知

管理员可以在 TG 管理员菜单或 SSH `boiltg` 菜单中进入：

```text
TG 通知
```

通知时间固定按中国大陆标准时间（北京时间，`Asia/Shanghai`）计算，和 VPS 所在时区无关。时间使用 `HH:MM` 格式，例如：

```text
21:30
```

设置后每天固定时间只通知管理员，内容包含：

- 整体入站流量
- 整体出站流量
- 整体单向计费流量
- 每个用户入站流量
- 每个用户出站流量
- 每个用户单向计费流量

单向流量口径为：分别统计入站和出站，额度使用取两者较大值，不把入站和出站相加。

当用户本周期单向流量达到月流量额度时，脚本会自动停用该用户，并重载 `sing-box-boil`。

## 数据保存与初始化

用户表和申请数据保存在：

```bash
/opt/boil-change-ip/data/boil_ss.db
```

更新脚本只更新逻辑代码，不会删除 `data/` 目录。

选择 `更新脚本` 时，即使当前已经是最新版本，也会执行一次运行环境维护：补齐新增依赖、更新 Python 依赖、刷新全局命令、刷新 `sing-box-boil` 配置并重启服务。脚本代码替换后会自动切换到新版维护流程，避免旧版更新器漏跑新迁移逻辑。

如需清空所有用户和申请数据，在 VPS 执行：

```bash
boiltg
```

选择：

```text
8. 初始化脚本
```

该操作需要二次确认，会清空所有 SS 用户、申请记录和 sing-box 用户配置。

## IP 质量检测

检测脚本会执行：

```bash
bash <(curl -Ls https://IP.Check.Place)
```

程序内部会使用 IPQuality 的 `-o` 参数把最终 ANSI 报告输出到临时文件，再用 Python/Pillow 渲染 PNG。这样截图取的是最终报告，而不是检测过程中的滚动输出。

执行流程：

1. 获取当前公网 IPv4。
2. 读取本地缓存的旧 IP。
3. 对比新旧 IP。
4. 需要检测时调用远程脚本。
5. 捕获 ANSI 输出。
6. 使用 Python/Pillow 生成 PNG 图片。
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
- `ss_manager.py`：SS 用户、申请、sing-box 配置管理模块。
- `scripts/ss_cli.py`：VPS 命令行 SS 用户管理工具。
- `install.sh`：交互式安装脚本。
- `scripts/boiltg.sh`：VPS 全局管理菜单。
- `.env`：用户配置文件，由安装脚本生成，不会提交到 Git。
- `VERSION`：版本号，用于更新判断。
