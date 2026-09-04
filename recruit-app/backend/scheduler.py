"""M7：定时搜索调度器。daemon 线程每 ~60s 扫描 search_profiles。

命中规则：every_hours > 0 且 enabled=1，且距 last_run_at（或创建时间）已到间隔，
且当前无 running 的 run → start_run(trigger="scheduled")。应用启动时由 lifespan 拉起。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from . import db
from . import run_service

log = logging.getLogger("recruit.scheduler")

_POLL_SECONDS = 60
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def _due(profile: dict, now: datetime) -> bool:
    interval_h = float(profile.get("every_hours") or 0)
    if interval_h <= 0:
        return False
    last = profile.get("last_run_at") or profile.get("created_at")
    if not last:
        return True  # 从未跑过，到点即跑
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now - last_dt).total_seconds() >= interval_h * 3600


def _tick():
    if db.running_runs():
        return  # 已有搜索在跑，跳过本轮（防叠加）
    now = datetime.now()
    for sp in db.list_search_profiles():
        if not int(sp.get("enabled", 1)):
            continue
        if not _due(sp, now):
            continue
        log.info("调度触发搜索方案 %s (id=%s)", sp.get("name"), sp.get("id"))
        try:
            run_service.start_run(sp["id"], trigger="scheduled")
        except RuntimeError as e:
            log.warning("调度跳过 %s: %s", sp.get("id"), e)


def _loop():
    log.info("定时搜索调度器启动（每 %ss 扫描）", _POLL_SECONDS)
    while not _STOP.wait(_POLL_SECONDS):
        try:
            _tick()
        except Exception:  # noqa: BLE001 —— 调度器常驻，单轮异常不影响后续
            log.exception("调度 tick 异常")


def start() -> None:
    """幂等启动调度线程。"""
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="scheduler", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()
    if _THREAD:
        _THREAD.join(timeout=_POLL_SECONDS)
