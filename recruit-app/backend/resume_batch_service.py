"""手动批量抓取简历：后台线程复用浏览器会话逐条抓取。

镜像 invite_service 的并发模式：同一时刻只允许一个批次（_RESUME_BATCH_LOCK）；
批次在专用 daemon 线程内直接 new LiepinSession（非 FastAPI 线程池，greenlet 安全，
无需 _run_in_dedicated_thread）。run 运行时禁止手动批量（run 自身含自动抓取段，
双浏览器同 profile 会被 adapter 全局锁挡死）。

逐条落库 + 重评分：中途停止/异常时已抓部分保留，未抓的 resume_fetched=0 下次重试。
"""
from __future__ import annotations

import logging
import threading

from . import db
from .liepin import adapter as lp
from .run_service import apply_resume_to_candidate, mark_resume_failed

log = logging.getLogger("recruit.resume_batch")

_RESUME_BATCH_LOCK = threading.Lock()
_RESUME_BATCH_STOP = threading.Event()
# 前端轮询读的状态。int/str/bool 写入原子，不加锁（与 invite 一致的简化约定）
_STATE = {"running": False, "run_id": None, "total": 0, "done": 0, "error": None}


def is_active() -> bool:
    """是否有批量抓取在跑（run_service.start_run 互斥检查用）。"""
    return bool(_STATE["running"])


def get_status(run_id: int | None = None) -> dict:
    """返回批量抓取状态。指定 run_id 且不匹配时视为不在跑（前端只关心自己的 run）。"""
    s = dict(_STATE)
    if run_id is not None and s["run_id"] != run_id:
        return {"running": False, "run_id": run_id, "total": 0, "done": 0, "error": None}
    return s


def _settings() -> dict:
    from .run_service import _settings as rs
    return rs()


def _worker(run_id: int, cards: list[dict], cfg: dict):
    try:
        session = lp.LiepinSession(
            cfg["cookie"], cfg["cookie_type"],
            headless=cfg["headless"], channel=cfg["channel"], delays=cfg["delays"],
            proxy=cfg["proxy"], executable=cfg["executable"],
            attach=cfg.get("attach", False))
        session.validate()
        try:
            q = lp.LiepinQueue(session, delays=cfg["delays"], stop=_RESUME_BATCH_STOP)

            def progress(done, total, item):
                _STATE["done"] = done
                _STATE["total"] = total
                try:
                    if item.get("resume"):
                        apply_resume_to_candidate(item["candidate_id"], item["resume"])
                    else:
                        mark_resume_failed(item["candidate_id"],
                                           item.get("error") or "未取到简历内容")
                except Exception:  # noqa: BLE001 —— 单条落库失败不拖垮整批
                    log.exception("简历落库失败 candidate=%s", item.get("candidate_id"))

            q.fetch_resumes(cards, progress_cb=progress)
        finally:
            session.close()
    except (lp.LiepinLoginError, lp.LiepinRiskError) as e:
        # 整批失败：置 error，让人工介入；未抓的保持 resume_fetched=0 可重试
        _STATE["error"] = str(e)
    except lp.LiepinStopRequested:
        _STATE["error"] = "已手动停止"
    except Exception as e:  # noqa: BLE001
        log.exception("批量抓简历失败 run=%s", run_id)
        _STATE["error"] = f"{type(e).__name__}: {e}"
    finally:
        _STATE["running"] = False
        _RESUME_BATCH_LOCK.release()


def start_batch(run_id: int) -> dict:
    """对 run 内 quick_gate=1 且未抓的前 N 名（规则分降序，N=limits.max_resume_fetch_per_run）
    后台抓取简历。返回 {ok, total, message?}。"""
    if not db.get_run(run_id):
        raise ValueError(f"运行不存在: {run_id}")
    if db.running_runs():
        raise RuntimeError("搜索正在运行中（run 已含自动抓取），请等它完成后再手动批量")
    if not _RESUME_BATCH_LOCK.acquire(blocking=False):
        raise RuntimeError("已有批量抓取在运行中，请稍候")
    try:
        cfg = _settings()
        if not cfg["cookie"] and not cfg.get("attach", False):
            raise ValueError("未配置猎聘 Cookie，请先在设置页粘贴并测试连接"
                             "（或开启『liepin 浏览器』模式后先运行 liepin login）")
        cap = int(lp.CODES["limits"]["max_resume_fetch_per_run"])
        cards = db.top_fetch_candidates(run_id, cap)
        if not cards:
            return {"ok": True, "total": 0,
                    "message": "没有待抓取的简历（初筛通过且未抓过的候选都抓过了）"}
        _RESUME_BATCH_STOP.clear()
        _STATE.update({"running": True, "run_id": run_id, "total": len(cards),
                       "done": 0, "error": None})
        t = threading.Thread(target=_worker, args=(run_id, cards, cfg),
                             daemon=True, name=f"resume-batch-{run_id}")
        t.start()
        return {"ok": True, "total": len(cards)}
    except Exception:
        _RESUME_BATCH_LOCK.release()
        raise


def stop_batch() -> bool:
    _RESUME_BATCH_STOP.set()
    return True
