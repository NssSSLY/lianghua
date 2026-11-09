# 部署到 Linux 服务器

服务器上常驻运行看板，交易日 09:20 自动启动盘中封板监控，15:45 自动同步数据并生成次日计划。你在自己的电脑上通过 SSH 隧道访问看板。

**实盘下单（银河 MiniQMT）只支持 Windows，仍在你自己的电脑上运行，服务器不碰实盘。**

## 1. 服务器

- 选国内的云服务器（阿里云、腾讯云等）：数据源都在国内，海外服务器可能取不到数据
- 2 核 4G 够用（看板约占 1 GB 内存，回测任务另占一些）；要在服务器上做长历史研究建议 8G
- 系统：Ubuntu 22.04 / 24.04
- 安全组 / 防火墙只开放 22 端口（SSH），**不要开放 8686**
- 用 SSH 密钥登录，关闭密码登录

时区：服务和定时器里已经写死北京时间，系统时区不改也能按时运行。改成北京时间看日志更方便：

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

## 2. 安装

```bash
sudo apt update && sudo apt install -y git curl python3-venv
git clone https://github.com/NssSSLY/lianghua.git ~/lianghua
```

Ubuntu 22.04 自带 Python 3.10，可以直接用；想用 3.12 可以先装 uv（`curl -LsSf https://astral.sh/uv/install.sh | sh`），安装脚本会自动用它。

## 3. 上传数据

仓库里不含 `data/`（行情数据、业绩、盘中记录都只在本地）。在你自己的电脑上，项目目录里运行：

```bash
scp -r data 用户名@服务器IP:~/lianghua/
```

约 460 MB。也可以不传，在服务器上运行 `.venv/bin/python update_data.py` 重新全量下载，但要一个小时以上，东财还可能限流。

## 4. 一键安装服务

```bash
cd ~/lianghua
bash deploy/install.sh
```

脚本会：建虚拟环境、装依赖、安装并启动 3 个 systemd 单元，最后检查看板能不能打开。

| 单元 | 作用 |
|---|---|
| `quant-dashboard.service` | 看板，常驻，崩溃后 10 秒自动重启；只监听 127.0.0.1:8686 |
| `quant-watch.timer` → `quant-watch.service` | 周一到周五 09:20 启动盘中监控，15:01 自己结束；非交易日自己退出 |
| `quant-sync.timer` → `quant-sync.service` | 周一到周五 15:45 运行 `run_daily.sh`：归档 → 更新日线/ETF/业绩 → 次日计划；服务器当时关机的话开机后补跑 |

## 5. 从你的电脑访问

先在自己电脑上打开隧道（窗口保持开着）：

```bash
ssh -N -L 8686:127.0.0.1:8686 用户名@服务器IP
```

然后浏览器打开 http://127.0.0.1:8686 。手机或想免隧道访问，可以服务器和电脑都装 Tailscale 组内网。

**不要把 8686 端口直接开放到公网**：看板能上传并执行策略脚本，也能触发同步和回测，开放出去等于把服务器交给别人。

## 6. 日常维护

```bash
systemctl status quant-dashboard          # 看板状态
systemctl list-timers 'quant-*'           # 下次运行时间
sudo systemctl start quant-sync           # 立刻手动同步一次
tail -f ~/lianghua/logs/daily.log         # 盘后同步日志
tail -f ~/lianghua/logs/board_watch.log   # 盘中监控日志
tail -f ~/lianghua/logs/dashboard.log     # 看板日志
```

更新代码：

```bash
cd ~/lianghua && git pull && bash deploy/install.sh
```

备份盘中提示积累的数据（只在服务器上有）：

```bash
scp -r 用户名@服务器IP:~/lianghua/data/watch ./data/
```

停用全部服务：

```bash
sudo systemctl disable --now quant-dashboard quant-watch.timer quant-sync.timer
```
