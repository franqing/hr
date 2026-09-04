"""M6：批量站内邀请。后台线程发送，delay_greet 限速，stop 事件可中止。

同一时刻只允许一个邀请批次（_INVITE_LOCK）；每批次在专用线程建 LiepinSession，
避免 FastAPI 线程池复用线程带来的 playwright greenlet 冲突。
"""
from __future__ import annotations

import logging
import threading

from . import db
from .liepin import adapter as lp

log = logging.getLogger("recruit.invite")

_INVITE_LOCK = threading.Lock()
_INVITE_STOP = threading.Event()


def _settings() -> dict:
    from .run_service import _settings as rs
    return rs()


def _greet_message() -> str:
    return str(db.get_setting("greet_message") or "")


def _send_one(session: lp.LiepinSession, invite_id: int, job_id: str, message: str) -> None:
    inv = db.get_invite(invite_id)
    if not inv or inv["status"] in ("sent", "failed"):
        return
    cand = db.get_candidate(inv["candidate_id"]) if inv.get("candidate_id") else None
    user_id = (cand or {}).get("user_id")
    if not user_id:
        db.set_invite_status(invite_id, "failed", "候选缺少 user_id（需先抓取简历）")
        return
    if message:
        # 本轮仅职位预设招呼语；自定义文本需 IM 面板 UI 自动化（后续里程碑）。
        # 明确标失败而非忽略，避免用户误以为自定义招呼语已发出。
        db.set_invite_status(invite_id, "failed",
                             "自定义招呼语暂未支持：本轮仅发送职位预设招呼语，请清空招呼语后重试")
        return
    r = session.send_invite(user_id, job_id)
    if r.get("ok"):
        if r.get("already_chatted"):
            db.set_invite_status(invite_id, "sent", "已有会话，未重复发起")
        else:
            db.set_invite_status(invite_id, "sent")
    else:
        db.set_invite_status(invite_id, "failed", r.get("error"))


def _worker(invite_ids: list[int], job_id: str, message: str, cfg: dict):
    try:
        session = lp.LiepinSession(
            cfg["cookie"], cfg["cookie_type"],
            headless=cfg["headless"], channel=cfg["channel"], delays=cfg["delays"],
            proxy=cfg["proxy"], executable=cfg["executable"],
            attach=cfg.get("attach", False))
        session.validate()
        try:
            for iid in invite_ids:
                if _INVITE_STOP.is_set():
                    raise lp.LiepinStopRequested()
                time_sleep(cfg["delays"].get("greet", 45))
                _send_one(session, iid, job_id, message)
        finally:
            session.close()
    except (lp.LiepinLoginError, lp.LiepinRiskError) as e:
        # 整批失败：剩余 pending 统一标失败，让人工介入
        for iid in invite_ids:
            inv = db.get_invite(iid)
            if inv and inv["status"] == "pending":
                db.set_invite_status(iid, "failed", str(e))
    except lp.LiepinStopRequested:
        pass  # 已发出的保持 sent，未发出的保持 pending（可重试）
    finally:
        _INVITE_LOCK.release()


def time_sleep(sec: float):
    import time
    time.sleep(sec)


def start_batch(candidate_ids: list[int], job_id: str, run_id: int | None = None,
                greet_message: str = "") -> dict:
    """为候选建邀请记录并后台发送。返回 {created, total}。"""
    if not _INVITE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有邀请批次在发送中，请稍候")
    try:
        cfg = _settings()
        if not cfg["cookie"] and not cfg.get("attach", False):
            raise ValueError("未配置猎聘 Cookie，请先在设置页粘贴并测试连接"
                             "（或开启『liepin 浏览器』模式后先运行 liepin login）")
        if not job_id:
            raise ValueError("未选择邀请岗位")
        message = greet_message if greet_message else _greet_message()
        # 单批硬上限（防失控）。超出的候选直接丢弃，不建记录。
        cap = int(lp.CODES.get("limits", {}).get("max_invite_per_batch", 30))
        candidate_ids = candidate_ids[:cap]
        # 真实发送护栏：链路未经真实单条验证前只允许 1 条（用户首发的验证位）。
        # 超过 1 条需设置页勾选「邀请链路已验证」。
        if len(candidate_ids) > 1 and not bool(db.get_setting("invite_verified")):
            raise ValueError(
                "邀请链路尚未经真实发送验证：请先只勾选 1 位候选人完成首次发送，"
                "成功后到设置页勾选「邀请链路已验证」再批量")

        invite_ids, created = [], 0
        for cid in candidate_ids:
            iid, is_new = db.create_invite(cid, run_id or 0, job_id)
            invite_ids.append(iid)
            created += int(is_new)
        if not invite_ids:
            return {"ok": True, "created": 0, "total": 0, "message": "无可发送的候选"}
        _INVITE_STOP.clear()
        t = threading.Thread(target=_worker, args=(invite_ids, job_id, message, cfg),
                             daemon=True, name="invite-batch")
        t.start()
        return {"ok": True, "created": created, "total": len(invite_ids)}
    except Exception:
        _INVITE_LOCK.release()
        raise


def stop_batch() -> bool:
    _INVITE_STOP.set()
    return True


def retry_invite(invite_id: int) -> dict:
    """单条重试：重置状态为 pending 后重新后台发送。"""
    inv = db.get_invite(invite_id)
    if not inv:
        raise ValueError(f"邀请不存在: {invite_id}")
    if not _INVITE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有邀请批次在发送中，请稍候")
    try:
        cfg = _settings()
        if not cfg["cookie"] and not cfg.get("attach", False):
            raise ValueError("未配置猎聘 Cookie")
        db.set_invite_status(invite_id, "pending")
        message = _greet_message()
        _INVITE_STOP.clear()
        t = threading.Thread(target=_worker, args=([invite_id], inv["job_id"], message, cfg),
                             daemon=True, name="invite-retry")
        t.start()
        return {"ok": True}
    except Exception:
        _INVITE_LOCK.release()
        raise
