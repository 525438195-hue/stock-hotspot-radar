"""APScheduler jobs for v0.3 auto reports."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_auto_report() -> None:
    result = subprocess.run(
        [sys.executable, "src/main.py", "--mode", "auto"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr or result.stdout)
    else:
        print(result.stdout)


def main() -> None:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
    except Exception as exc:
        raise RuntimeError("缺少 APScheduler，请先安装 requirements.txt") from exc

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(run_auto_report, "cron", hour=8, minute=40, id="premarket_report")
    scheduler.add_job(run_auto_report, "cron", hour=15, minute=30, id="close_report")
    scheduler.add_job(run_auto_report, "cron", hour=21, minute=30, id="announcement_risk_scan")
    print("定时器已启动：08:40 盘前报告，15:30 收盘报告，21:30 公告风险扫描。")
    scheduler.start()


if __name__ == "__main__":
    main()

