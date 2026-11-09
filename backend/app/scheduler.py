"""APScheduler 封装：每日固定时间执行 + 启动补跑。

补跑规则：应用启动时若「今天已过调度时刻」且「今天尚无任何运行记录」，
立即补跑一次（trigger=startup），避免容器重启/停机错过当日任务。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from .store import Store

log = logging.getLogger("picix.scheduler")


def parse_schedule(schedule_time: str) -> tuple[int, int]:
    try:
        hh, mm = schedule_time.strip().split(":")
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except Exception:
        raise ValueError(f"调度时间格式应为 HH:MM，当前值: {schedule_time!r}")


class Scheduler:
    def __init__(
        self,
        store: Store,
        job: Callable[[str], Awaitable[Any]],
    ):
        self.store = store
        self.job = job
        self._scheduler = AsyncIOScheduler()
        self._lock = asyncio.Lock()
        self._startup_check_done = False

    def apply_config(self) -> None:
        """按 store 当前配置重建 cron 触发器。"""
        cfg = self.store.get_config()
        hour, minute = parse_schedule(cfg["schedule_time"])
        tz = ZoneInfo(cfg["timezone"])
        if self._scheduler.get_job("daily"):
            self._scheduler.remove_job("daily")
        self._scheduler.add_job(
            self._run_scheduled,
            CronTrigger(hour=hour, minute=minute, timezone=tz),
            id="daily",
            name="picix daily tasks",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        log.info("调度已设置: %02d:%02d %s", hour, minute, cfg["timezone"])

    def start(self) -> None:
        self._scheduler.start()
        self._startup_check_done = True

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def next_run_time(self) -> str | None:
        job = self._scheduler.get_job("daily") if self._scheduler.running else None
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    async def _run_with_guard(self, trigger: str, *args: Any) -> Any:
        """执行任务并兜底 CancelledError：中断时把 running 行置为 failed。"""
        try:
            return await self.job(trigger, *args)
        except asyncio.CancelledError:
            for r in self.store.list_runs(limit=20):
                if r.get("status") == "running":
                    self.store.finish_run(
                        int(r["id"]), "failed", {"errors": ["任务被中断"]}
                    )
            log.warning("任务 %s 被中断，running 记录已标记 failed", trigger)
            raise

    async def _run_scheduled(self) -> None:
        async with self._lock:
            await self._run_with_guard("schedule")

    async def run_manual(self, force: bool = False) -> dict[str, Any]:
        if self._lock.locked():
            raise RuntimeError("已有任务正在执行，请稍后再试")
        async with self._lock:
            return await self._run_with_guard("manual", force)
    async def startup_catchup(self) -> None:
        """启动补跑：今天已过调度时刻且今天没有成功运行 → 立即执行一次。

        「已跑」判定只认 success/partial（PLAN-v3.md 1.10）：
        skipped/failed 都视为当天未完成，启动时补跑。
        同时清理上次进程可能残留的 running 记录。
        """
        if not self._startup_check_done:
            return
        interrupted = self.store.mark_interrupted_runs()
        if interrupted:
            log.info("已标记 %d 条上次进程残留的 running 记录为 failed", interrupted)
        cfg = self.store.get_config()
        hour, minute = parse_schedule(cfg["schedule_time"])
        tz = ZoneInfo(cfg["timezone"])
        now = datetime.now(tz)
        scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < scheduled_today:
            return  # 今天的调度还没到，等 cron 即可
        runs = self.store.list_runs(limit=30)
        for r in runs:
            if not r.get("finished_at"):
                continue
            finished_local = datetime.fromtimestamp(float(r["finished_at"]), tz)
            if finished_local.date() != now.date():
                break  # 列表按时间倒序，越过今天即无当天记录
            if r.get("status") in ("success", "partial"):
                return  # 今天已有成功/部分成功运行，不补
        log.info("启动补跑：检测到今天尚未成功执行")
        if self._lock.locked():
            log.info("启动补跑跳过：已有任务在执行")
            return
        async with self._lock:
            try:
                await self.job("startup")
            except Exception as e:  # noqa: BLE001 — 补跑失败仅记日志，不影响应用启动
                log.error("启动补跑失败: %r", e)
