"""搜索运行编排：fire-and-forget + 实时状态落库（前端 4s 轮询）。

run 在独立后台线程执行；真正碰 playwright 的批次经 LiepinRunner 再转到
用后即弃的专用线程（见 adapter._run_in_dedicated_thread，防 anyio/greenlet 冲突）。
"""
import json
import logging
import threading
import time

from . import db
from . import search_planner
from . import matcher
from .liepin import adapter as lp

log = logging.getLogger("recruit.run")

# 全局：同一时刻只允许一个 run 在跑（browser profile 已被 adapter 锁串行，这里再守一层防重复提交）
_RUN_LOCK = threading.Lock()
_RUN_STOPS: dict[int, threading.Event] = {}


def _settings() -> dict:
    raw = db.get_settings()

    def plain(key):
        return raw.get(key, "")

    # cookie 是密文，这里解密
    cookie = plain("liepin_cookie")
    if cookie:
        from .security import decrypt_str
        try:
            cookie = decrypt_str(cookie)
        except Exception:
            cookie = ""
    return {
        "cookie": cookie,
        "cookie_type": plain("liepin_cookie_type") or "header",
        "delays": {
            "search": float(plain("delay_search") or 5),
            "resume": float(plain("delay_resume") or 15),
            "greet": float(plain("delay_greet") or 45),
        },
        "headless": str(plain("browser_headless")).lower() == "true",
        "channel": plain("browser_channel") or "chromium",
        "proxy": plain("http_proxy") or plain("https_proxy") or "",
        "executable": plain("browser_executable") or "",
        # 附件模式：复用 liepin login 的常驻 Chrome（browser_attach=true 时不再
        # 自拉浏览器实例，cookie 允许为空——登录态在浏览器 profile 里）
        "attach": str(plain("browser_attach")).lower() == "true",
    }


def _stats_update(run_id: int, stats: dict):
    conn = db.get_conn()
    try:
        conn.execute("UPDATE search_runs SET stats_json=? WHERE id=?",
                     (json.dumps(stats, ensure_ascii=False), run_id))
        conn.commit()
    finally:
        conn.close()


def _worker(run_id: int, search_profile_id: int, trigger: str, stop: threading.Event):
    stats = None
    try:
        sp = db.get_search_profile(search_profile_id)
        if not sp:
            raise ValueError(f"搜索方案不存在: {search_profile_id}")
        jp = db.get_job_profile(sp["job_profile_id"]) if sp.get("job_profile_id") else None
        profile = (jp or {}).get("profile") if isinstance(jp, dict) else None
        preset = sp.get("plan_preset") or "full"

        cfg = _settings()
        if not cfg["cookie"] and not cfg["attach"]:
            raise ValueError("未配置猎聘 Cookie，请先在设置页粘贴并测试连接"
                             "（或开启『liepin 浏览器』模式后先运行 liepin login）")

        queries = search_planner.build_queries(profile, preset=preset,
                                               max_queries=lp.CODES["limits"]["max_queries_per_run"])
        if not queries:
            raise ValueError("画像未生成任何搜索词")

        stats = {"total_queries": len(queries), "done": 0, "queries": [], "candidates": 0,
                 "new": 0, "resumes": {"done": 0, "total": 0}, "error": None}
        _stats_update(run_id, stats)

        def progress(done, total, item):
            stats["done"] = done
            stats["queries"].append({"label": item["label"], "keyword": item["keyword"],
                                     "total": item["total"], "cached": item["cached"]})
            _stats_update(run_id, stats)

        def resume_progress(done, total, item):
            """搜索完成后同一会话内自动抓简历：逐条落库/重评分，中途停止也保留已抓部分。"""
            stats["resumes"]["done"] = done
            stats["resumes"]["total"] = total
            try:
                if item.get("resume"):
                    apply_resume_to_candidate(item["candidate_id"], item["resume"])
                else:
                    mark_resume_failed(item["candidate_id"],
                                       item.get("error") or "未取到简历内容")
            except Exception:  # noqa: BLE001 —— 单条落库失败不拖垮整批
                log.exception("简历落库失败 candidate=%s", item.get("candidate_id"))
            _stats_update(run_id, stats)

        def collect(results):
            """全部落库（全局去重）+ gate + 评分。返回自动抓取的候选列表（top N 规则分序）。
            评分保护：只对 is_new 候选写 M3 卡片级分 —— 老候选已有 M4 简历级/LLM 分，
            INSERT OR REPLACE 覆盖会退化评分精度。"""
            new = 0
            seen = 0
            for item in results:
                for card in item.get("cards", []):
                    seen += 1
                    try:
                        _cid, is_new = db.upsert_candidate(run_id, card)
                        q_ok, q_reason = matcher.quick_gate(card, profile)
                        h_ok, h_reason = matcher.hard_gate(card, profile=profile)
                        db.update_candidate_gate(_cid, int(q_ok), q_reason, int(h_ok), h_reason)
                        if is_new:
                            new += 1
                            sc = matcher.rule_score(profile, card)
                            db.save_score(_cid, "rule", sc["dims"], sc["total"],
                                          "规则打分（M3）", sc["explanation"])
                    except ValueError:
                        continue  # 缺 resume_id 的脏卡片跳过
            stats["candidates"] = seen
            stats["new"] = new
            _stats_update(run_id, stats)
            # 自动抓取候选：quick_gate=1 且未抓，规则分降序（DB 判定，含历史 run 未抓的）
            return db.top_fetch_candidates(run_id,
                                           lp.CODES["limits"]["max_resume_fetch_per_run"])

        results = lp.LiepinRunner(
            cfg["cookie"], cfg["cookie_type"],
            headless=cfg["headless"], channel=cfg["channel"], delays=cfg["delays"],
            proxy=cfg["proxy"], executable=cfg["executable"], attach=cfg["attach"],
        ).run_full(queries, collect, stop=stop, progress_cb=progress,
                   resume_progress_cb=resume_progress)

        stats["done"] = len(results)
        db.update_run_status(run_id, "done", stats=stats)
        db.touch_search_profile(search_profile_id)
        db.audit("search_run.done", detail=f"run={run_id} 候选={stats['candidates']} "
                                           f"新={stats['new']} 简历={stats['resumes']['done']}")
    except lp.LiepinLoginError as e:
        db.update_run_status(run_id, "failed", stats=stats, error=str(e))
    except lp.LiepinRiskError as e:
        db.update_run_status(run_id, "failed", stats=stats, error=str(e))
    except lp.LiepinStopRequested:
        db.update_run_status(run_id, "stopped", stats=stats, error="已手动停止")
    except TimeoutError as e:
        db.update_run_status(run_id, "stopped", stats=stats, error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("run %s 失败", run_id)
        db.update_run_status(run_id, "failed", stats=stats, error=f"{type(e).__name__}: {e}")
    finally:
        _RUN_STOPS.pop(run_id, None)
        _RUN_LOCK.release()


def start_run(search_profile_id: int, trigger: str = "manual") -> int:
    """创建 run 并后台执行。已有 running 的 run 时抛 RuntimeError（防重叠）。"""
    running = db.running_runs()
    if running:
        raise RuntimeError(f"已有正在运行的搜索（run #{running[0]}），请等待完成或先停止")
    # 互斥：手动批量抓简历进行中不开新 run（run 自身含自动抓取段，双浏览器同 profile 会打架）。
    # 函数内 import 避免与 resume_batch_service（依赖本模块的落库函数）循环导入。
    from . import resume_batch_service
    if resume_batch_service.is_active():
        raise RuntimeError("批量抓取简历进行中，请等待完成后再启动搜索")
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("已有正在运行的搜索，请稍候")
    run_id = db.create_search_run(search_profile_id, trigger)
    stop = threading.Event()
    _RUN_STOPS[run_id] = stop
    t = threading.Thread(target=_worker, args=(run_id, search_profile_id, trigger, stop),
                         daemon=True, name=f"search-run-{run_id}")
    t.start()
    return run_id


def stop_run(run_id: int) -> bool:
    ev = _RUN_STOPS.get(run_id)
    if ev:
        ev.set()
        return True
    # run 已结束的兜底：直接把状态改成 stopped
    r = db.get_run(run_id)
    if r and r["status"] == "running":
        db.update_run_status(run_id, "stopped", error="已手动停止")
        return True
    return False


def _profile_for_candidate(cand: dict) -> dict | None:
    """候选人 → 所属 run → 搜索方案 → 画像。用于简历重评分。"""
    run = db.get_run(cand.get("run_id")) if cand.get("run_id") else None
    sp = db.get_search_profile(run["search_profile_id"]) if run else None
    if sp and sp.get("job_profile_id"):
        jp = db.get_job_profile(sp["job_profile_id"])
        if jp and isinstance(jp.get("profile"), dict):
            return jp["profile"]
    return None


def apply_resume_to_candidate(candidate_id: int, resume: dict) -> dict:
    """简历抓取成功 → 落库（含文本）+ hard_gate/M4 重评（quick_gate 保留当前值）。

    自动抓取（resume_progress）、手动批量（resume_batch_service）、单条 refetch 共用。
    resume 形状 = adapter.fetch_resume 返回的 {text, 结构化字段…}。
    """
    cand = db.get_candidate(candidate_id)
    profile = _profile_for_candidate(cand) if cand else None
    card = (cand or {}).get("card") or {}
    db.update_candidate_resume(candidate_id, resume, resume.get("text", ""))
    h_ok, h_reason = matcher.hard_gate(card, resume, profile=profile)
    db.update_candidate_gate(candidate_id, (cand or {}).get("quick_gate", 0),
                             (cand or {}).get("quick_reason") or "", int(h_ok), h_reason)
    sc = matcher.rule_score(profile, card, resume)
    db.save_score(candidate_id, "rule", sc["dims"], sc["total"],
                  "规则打分（M4 简历重评）", sc["explanation"])
    return {"candidate_id": candidate_id, "resume_fetched": True,
            "hard_gate": int(h_ok), "hard_reason": h_reason, "score": sc}


def mark_resume_failed(candidate_id: int, error: str):
    """简历抓取失败 → resume_fetched=0 + error 存 blob。失败会进下次批量重试。"""
    db.update_candidate_resume(candidate_id, None, "", fetched=False, error=error)


def refetch_candidate(candidate_id: int) -> dict:
    """抓取候选人简历详情并重跑 hard_gate + rule_score（M4）。

    同步执行（前端点击后等待）；playwright 批次经专用线程隔离 greenlet。
    返回 {candidate_id, resume_fetched, hard_gate, hard_reason, score}。
    """
    cand = db.get_candidate(candidate_id)
    if not cand:
        raise ValueError(f"候选人不存在: {candidate_id}")
    if not cand.get("resume_id"):
        raise ValueError("候选人缺少 resume_id，无法抓取简历")
    cfg = _settings()
    if not cfg["cookie"] and not cfg["attach"]:
        raise ValueError("未配置猎聘 Cookie，请先在设置页粘贴并测试连接"
                         "（或开启『liepin 浏览器』模式后先运行 liepin login）")

    def _do():
        session = lp.LiepinSession(
            cfg["cookie"], cfg["cookie_type"],
            headless=cfg["headless"], channel=cfg["channel"], delays=cfg["delays"],
            proxy=cfg["proxy"], executable=cfg["executable"], attach=cfg["attach"])
        session.validate()
        try:
            return session.fetch_resume(cand["resume_id"], cand.get("user_id") or "")
        finally:
            session.close()

    resume = lp._run_in_dedicated_thread(_do)
    if not resume or not resume.get("text", "").strip():
        mark_resume_failed(candidate_id, "未取到简历内容（可能该简历不公开）")
        return {"candidate_id": candidate_id, "resume_fetched": False,
                "error": "未取到简历内容（可能该简历不公开）"}

    db.cache_put(f"resume:{cand['resume_id']}", json.dumps(resume, ensure_ascii=False))
    return apply_resume_to_candidate(candidate_id, resume)
