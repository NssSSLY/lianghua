#!/usr/bin/env bash
# 在 Linux 服务器上安装：虚拟环境 + 依赖 + systemd 服务（看板常驻、09:20 盘中监控、15:45 盘后同步）
# 用法：在项目根目录用普通用户执行  bash deploy/install.sh   （需要 sudo 权限）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
if [ "$(id -u)" -eq 0 ]; then
    echo "请用普通用户运行（脚本里需要 root 的步骤会自己调用 sudo）"; exit 1
fi
case "$ROOT" in
    *" "*) echo "项目路径里不能有空格：$ROOT"; exit 1 ;;
esac
cd "$ROOT"

echo "== 1/4 Python 虚拟环境"
if [ ! -x .venv/bin/python ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv --python 3.12 .venv
    else
        PY="$(command -v python3.12 || command -v python3 || true)"
        [ -n "$PY" ] || { echo "没有找到 python3，请先安装 Python 3.10+"; exit 1; }
        "$PY" -m venv .venv
    fi
fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3, 10), "需要 Python 3.10 以上"'

echo "== 2/4 安装依赖"
if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -r requirements.txt
else
    .venv/bin/python -m pip install -q -U pip
    .venv/bin/python -m pip install -q -r requirements.txt
fi

echo "== 3/4 检查数据和目录"
mkdir -p logs data
chmod +x run_daily.sh
if [ ! -d data/daily ] || [ -z "$(ls -A data/daily 2>/dev/null)" ]; then
    echo "  提示：data/daily 是空的。可以从本地电脑上传 data/ 目录（见 deploy/DEPLOY.md），"
    echo "        或者稍后手动运行 .venv/bin/python update_data.py 全量下载（约一小时以上）"
fi

echo "== 4/4 安装 systemd 服务（用户 $RUN_USER，目录 $ROOT）"
for f in deploy/systemd/*; do
    sed -e "s#@ROOT@#$ROOT#g" -e "s#@USER@#$RUN_USER#g" "$f" | sudo tee "/etc/systemd/system/$(basename "$f")" > /dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now quant-dashboard.service quant-watch.timer quant-sync.timer

echo
systemctl list-timers 'quant-*' --no-pager || true
for i in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8686/ || true)"
    [ "$code" = "200" ] && break
    sleep 2
done
echo "看板状态：HTTP ${code:-无响应}（200 表示正常）"
echo
echo "完成。在你自己的电脑上运行下面这条命令，然后浏览器打开 http://127.0.0.1:8686"
echo "    ssh -N -L 8686:127.0.0.1:8686 $RUN_USER@<服务器IP>"
