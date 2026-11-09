#!/usr/bin/env bash
# 每日盘后任务（Linux 版 run_daily.bat）：归档涨停池/龙虎榜 → 更新日线、ETF、业绩 → 生成次日计划
# 某一步失败也继续执行后面的步骤，最后以失败状态退出，方便 systemd 记录
cd "$(dirname "$0")" || exit 1
mkdir -p logs
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
PY=.venv/bin/python
LOG=logs/daily.log
status=0
echo "==================== $(date '+%F %T')" >> "$LOG"
for step in archive_pools.py update_data.py daily_pick.py; do  # 先归档：这些池子几周后就取不到了
    echo "---- $step" >> "$LOG"
    "$PY" -u "$step" >> "$LOG" 2>&1 || { echo "!! $step 失败，退出码 $?" >> "$LOG"; status=1; }
done
exit $status
