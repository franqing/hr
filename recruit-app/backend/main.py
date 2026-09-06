"""FastAPI 入口：/api 全部路由 + lifespan。

M1 提供：health + settings（含掩码占位）。后续里程碑在此文件追加路由
（search/liepin/profiles/runs/invites 见对应模块，mounted 于此）。
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from . import db
from .security import encrypt_str, decrypt_str
from .liepin import adapter as lp
from .liepin.adapter import validate_cookie_settings
from . import run_service
from . import profile_parser
from . import export
from . import scorer
from . import invite_service
from . import resume_batch_service
from . import scheduler
from . import cookie_import

log = logging.getLogger("recruit")

# ---------- settings schema ----------
# secret=True 的字段存 Fernet 密文；GET 返回掩码占位，保存时跳过占位值。
# 原生（Windows 安装版）默认浏览器通道 = 本机 msedge：Edge 为 Windows 自带（离线安装包
# 语义不变），且与用户日常 Edge 同引擎 → 「打开浏览器登录」会话与引擎一致不再每次重登。
# WSL 开发默认仍随包 chromium（无自带 Edge 可指，且与脚本基线一致）。
DEFAULT_BROWSER_CHANNEL = "msedge" if os.name == "nt" else "chromium"

SETTINGS_SCHEMA: list[dict] = [
    {"key": "liepin_cookie",      "secret": True,  "label": "猎聘 Cookie"},
    {"key": "liepin_cookie_type", "secret": False, "label": "Cookie 格式",
     "default": "header"},  # header | json | pairs
    {"key": "llm_base_url",       "secret": False, "label": "LLM Base URL"},
    {"key": "llm_api_key",        "secret": True,  "label": "LLM API Key"},
    {"key": "llm_model",          "secret": False, "label": "LLM 模型",
     "default": "deepseek-chat"},
    {"key": "delay_search",       "secret": False, "default": "5"},
    {"key": "delay_resume",       "secret": False, "default": "15"},
    {"key": "delay_greet",        "secret": False, "default": "45"},
    {"key": "invite_job_id",      "secret": False, "label": "默认邀请岗位 ejobId"},
    {"key": "greet_message",      "secret": False, "label": "打招呼模板"},
    {"key": "invite_verified",    "secret": False, "label": "邀请链路已验证",
     "default": False},  # 真实单条发送验证通过后勾选，才开放批量
    {"key": "browser_headless",   "secret": False, "default": "true"},
    {"key": "browser_channel",    "secret": False, "default": DEFAULT_BROWSER_CHANNEL},
    {"key": "browser_executable", "secret": False, "label": "浏览器可执行文件"},
    # 附件模式：默认开启——搜索/测试/邀请复用 `liepin login` 的常驻 Chrome 及其
    # 登录态，不自拉浏览器实例（用户登录态在 liepin-cli 启动的 Chrome profile 里）
    {"key": "browser_attach",     "secret": False, "default": "false"},
    {"key": "http_proxy",         "secret": False, "label": "HTTP 代理"},
    {"key": "https_proxy",        "secret": False, "label": "HTTPS 代理"},
]
MASKED_PLACEHOLDER = "已配置"
_SECRET_KEYS = {s["key"] for s in SETTINGS_SCHEMA if s["secret"]}
_VALID_KEYS = {s["key"] for s in SETTINGS_SCHEMA}


def _seed_default_profile():
    """首个默认画像未就绪时，用《技术1号位 V2》种子画像兜底（开箱可用）。"""
    if db.get_default_job_profile():
        return
    from . import profile as P
    db.save_job_profile("技术1号位（研发负责人）· 默认", P.default_seed_profile(),
                        version="V2.0", is_default=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保表存在 + 默认画像种子。
    db.get_conn().close()
    _seed_default_profile()
    scheduler.start()  # M7：定时搜索调度
    cookie_import.start()  # 监听 liepin login 会话 → 自动导入 Cookie
    yield
    cookie_import.stop()
    scheduler.stop()
    if os.name == "nt":
        # 回收本进程自启的登录浏览器（精确 PID，绝不碰用户浏览器）；幂等
        try:
            from .liepin import login_browser
            login_browser.close_login_browser()
        except Exception:  # noqa: BLE001 —— 关停失败不影响进程退出
            log.exception("关闭登录浏览器失败")


app = FastAPI(title="华联招聘 · 猎聘候选人智能搜寻", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- health ----------
@app.get("/health")
def health():
    return {"ok": True, "app": "recruit-app", "version": "M1"}


# ---------- settings ----------
class SettingsPut(BaseModel):
    values: dict[str, str | int | float | bool | None]


_NUMERIC_SETTINGS = {"delay_search", "delay_resume", "delay_greet"}  # 前端 ElInputNumber 要 number


def _load_settings() -> dict:
    """读出设置；密文字段解密。默认值兜底。delay_* 一律转 int（防 DB 残留字符串）。"""
    raw = db.get_settings()
    out = {}
    for s in SETTINGS_SCHEMA:
        key = s["key"]
        # 空串 = 已被网页清除（PUT ""），按未配置处理，避免对空密文解密报错。
        if key in raw and raw[key] not in (None, ""):
            v = raw[key]
            out[key] = decrypt_str(v) if s["secret"] else v
        elif "default" in s:
            out[key] = s["default"]
        else:
            out[key] = ""
    for key in _NUMERIC_SETTINGS:
        try:
            out[key] = int(out[key])
        except (TypeError, ValueError):
            out[key] = int(SETTINGS_SCHEMA[[s["key"] for s in SETTINGS_SCHEMA].index(key)].get("default", 0))
    return out


@app.get("/settings")
def get_settings_endpoint():
    out = {}
    for s in SETTINGS_SCHEMA:
        key = s["key"]
        val = _load_settings().get(key, "")
        if s["secret"] and val:
            out[key] = MASKED_PLACEHOLDER
        else:
            out[key] = val
    out["platform_native"] = os.name == "nt"   # spec §4.4：前端据此做原生/WSL 文案分支
    return out


@app.put("/settings")
def put_settings_endpoint(body: SettingsPut):
    changed: list[str] = []
    for key, value in body.values.items():
        if key not in _VALID_KEYS:
            continue
        # 掩码占位：跳过不覆盖；空串/None：清除
        if value in (MASKED_PLACEHOLDER, None):
            continue
        if value == "":
            db.save_setting(key, "")
            changed.append(key)
            continue
        stored = encrypt_str(str(value)) if key in _SECRET_KEYS else value
        db.save_setting(key, stored)
        changed.append(key)
    db.audit("settings.update", detail=",".join(changed))
    return {"ok": True, "changed": changed}


# ---------- 猎聘连接测试 ----------
class LiepinTest(BaseModel):
    liepin_cookie: str = ""
    liepin_cookie_type: str = "header"
    use_existing: bool = False


@app.post("/settings/liepin/test")
def test_liepin(body: LiepinTest):
    # 互斥（历史缺陷：测试连接点了没反应 = 请求无限挂起）：测试连接要新建浏览器会话
    # 并持有全局浏览器锁（adapter._LIEPIN_LOCK，一次 run 只开一个浏览器）；正在跑的
    # 搜索 run / 批量抓简历同样独占该锁且周期长。若不先查 DB 直接放行，本请求会在
    # 建会话时阻塞到对方结束——uvicorn 只在请求完成后才写访问日志，前端看到的
    # 就是按钮一直 loading、无任何响应。这里与 run_service.start_run 同款先检查互斥。
    busy = db.running_runs()
    if busy:
        rid = busy[0]
        stats = (db.get_run(rid) or {}).get("stats") or {}
        total_q = stats.get("total_queries") or 0
        done_q = stats.get("done") or 0
        res = stats.get("resumes") or {}
        if total_q and done_q >= total_q and not res.get("total"):
            phase = "搜索完成，收尾中"
        elif res.get("total"):
            phase = f"搜索 {done_q}/{total_q} 已完成，正在抓取简历 {res.get('done', 0)}/{res['total']}"
        elif total_q:
            phase = f"正在搜索 {done_q}/{total_q}"
        else:
            phase = "启动中"
        return {"ok": False,
                "error": f"有搜索任务 run #{rid} 正在运行（{phase}）。测试连接与搜索共用浏览器会话，"
                         f"请等待该任务结束，或先在智能搜索页点『停止』再测试连接"}
    if resume_batch_service.is_active():
        return {"ok": False, "error": "批量抓取简历进行中，测试连接需等待其结束后再试"}

    cfg = _load_settings()
    attach = str(cfg.get("browser_attach") or "false").lower() == "true"
    cookie = (body.liepin_cookie or "").strip()
    if not cookie:
        if body.use_existing:
            cookie = cfg.get("liepin_cookie", "")
        if not cookie and not attach:
            # 附件模式不需要 cookie（登录态在 liepin 常驻 Chrome 里）；仅关闭
            # 附件模式时仍要求粘贴/已存 cookie
            return {"ok": False,
                    "error": "未提供 Cookie（可先保存设置后点击测试）；"
                             "或开启『liepin 浏览器』模式后，先运行 liepin login 保持 Chrome 打开"}
    try:
        return validate_cookie_settings(
            cookie, body.liepin_cookie_type or "header",
            headless=str(cfg.get("browser_headless") or "false").lower() == "true",
            channel=cfg.get("browser_channel") or "chromium",
            proxy=cfg.get("http_proxy") or cfg.get("https_proxy") or "",
            executable=cfg.get("browser_executable") or "",
            attach=attach)
    except Exception as e:  # noqa: BLE001 —— 任何失败都转成 {ok:false} 展示给前端
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.post("/settings/liepin/import")
def import_liepin_cookie():
    """从 `liepin login` 的常驻 Chrome 立即导入会话 Cookie（手动触发版）。

    前提：用户已在终端跑过 liepin login（系统绝不自动登录）。流程与自动监听
    一致：CDP 导出 → 与库内指纹比对 → 门户 + BFF 双验证 → 通过才落库 + audit；
    任何失败不写库，返回 {ok:false, error} 供页面展示。
    """
    return cookie_import.import_now(force=True, source="manual")


# ---------- 搜索方案 ----------
class SearchProfileIn(BaseModel):
    name: str
    job_profile_id: int | None = None
    plan_preset: str = "full"
    filters: dict = {}
    every_hours: float = 0
    enabled: bool = True


@app.get("/search-profiles")
def list_search_profiles():
    return db.list_search_profiles()


@app.post("/search-profiles")
def create_search_profile(body: SearchProfileIn):
    d = body.model_dump()
    pid = db.save_search_profile(d)
    db.audit("search_profile.create", detail=f"id={pid} name={d['name']}")
    return {"id": pid}


@app.put("/search-profiles/{pid}")
def update_search_profile(pid: int, body: SearchProfileIn):
    db.save_search_profile(body.model_dump(), profile_id=pid)
    db.audit("search_profile.update", detail=f"id={pid}")
    return {"id": pid}


@app.delete("/search-profiles/{pid}")
def delete_search_profile(pid: int):
    db.delete_search_profile(pid)
    db.audit("search_profile.delete", detail=f"id={pid}")
    return {"ok": True}


@app.post("/search-profiles/{pid}/run")
def run_search_profile(pid: int):
    try:
        rid = run_service.start_run(pid, trigger="manual")
        return {"ok": True, "run_id": rid}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/search-profiles/{pid}/stop")
def stop_search_profile(pid: int):
    for r in db.list_runs():
        if r["search_profile_id"] == pid and r["status"] == "running":
            run_service.stop_run(r["id"])
            return {"ok": True, "run_id": r["id"]}
    return {"ok": False, "error": "该方案没有正在运行的搜索"}


# ---------- 运行与候选 ----------
@app.get("/runs")
def list_runs():
    return db.list_runs()


@app.get("/runs/{rid}")
def get_run(rid: int):
    r = db.get_run(rid)
    if not r:
        raise HTTPException(status_code=404, detail="运行不存在")
    return r


def _with_resume_links(items: list[dict]) -> list[dict]:
    """每条候选附 resume_link：企业账号登录态浏览器可直达猎聘简历页（未登录会跳登录页）。"""
    for c in items:
        c["resume_link"] = lp.resume_detail_url(c.get("resume_id"))
    return items


@app.get("/runs/{rid}/candidates")
def run_candidates(rid: int):
    cands = db.list_run_candidates(rid)
    return _with_resume_links(db.attach_scores(cands))


@app.api_route("/runs/{rid}/export", methods=["GET", "POST"])
def run_export(rid: int):
    """导出 run 的 xlsx（候选/评分/邀请三 sheet）。GET 供浏览器直接下载，POST 兼容旧调用。"""
    if not db.get_run(rid):
        raise HTTPException(status_code=404, detail="运行不存在")
    data = export.build_run_export(rid)
    headers = {"Content-Disposition": f'attachment; filename="run-{rid}.xlsx"'}
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument."
                                            "spreadsheetml.sheet", headers=headers)


# ---------- 人才画像 ----------
class JobProfileIn(BaseModel):
    name: str
    version: str = "1.0"
    profile: dict
    is_default: bool = False


@app.get("/profiles")
def list_profiles():
    return db.list_job_profiles()


@app.post("/profiles")
def create_profile(body: JobProfileIn):
    pid = db.save_job_profile(body.name, body.profile, version=body.version,
                              is_default=int(body.is_default))
    db.audit("profile.create", detail=f"id={pid} name={body.name}")
    return {"id": pid}


@app.get("/profiles/{pid}")
def get_profile(pid: int):
    p = db.get_job_profile(pid)
    if not p:
        raise HTTPException(status_code=404, detail="画像不存在")
    return p


@app.put("/profiles/{pid}")
def update_profile(pid: int, body: JobProfileIn):
    if not db.get_job_profile(pid):
        raise HTTPException(status_code=404, detail="画像不存在")
    db.save_job_profile(body.name, body.profile, version=body.version,
                        is_default=int(body.is_default), profile_id=pid)
    db.audit("profile.update", detail=f"id={pid}")
    return {"id": pid}


@app.delete("/profiles/{pid}")
def delete_profile(pid: int):
    if not db.get_job_profile(pid):
        raise HTTPException(status_code=404, detail="画像不存在")
    db.delete_job_profile(pid)
    db.audit("profile.delete", detail=f"id={pid}")
    return {"ok": True}


@app.post("/profiles/parse-pdf")
async def parse_profile_pdf(file: UploadFile):
    """上传岗位要求 PDF → 抽文本 → LLM/规则解析为画像（不进库，供前端核对）。"""
    path = profile_parser.save_upload(file.filename or "job.pdf", await file.read())
    try:
        return profile_parser.parse_pdf(path)
    except profile_parser.ScanPdfError as e:
        return {"ok": False, "error": str(e)}
    finally:
        path.unlink(missing_ok=True)


# ---------- M4 候选人详情 ----------
@app.get("/candidates/{cid}")
def get_candidate(cid: int):
    c = db.get_candidate(cid)
    if not c:
        raise HTTPException(status_code=404, detail="候选人不存在")
    return _with_resume_links(db.attach_scores([c]))[0]


@app.post("/candidates/{cid}/refetch")
def refetch_candidate(cid: int):
    """抓取候选人简历并重打分（M4）。浏览器批次较慢，前端等待返回。"""
    try:
        return run_service.refetch_candidate(cid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except lp.LiepinLoginError as e:
        return {"ok": False, "error": str(e)}
    except lp.LiepinRiskError as e:
        return {"ok": False, "error": str(e)}


# ---------- M5 LLM 深度评分 ----------
@app.post("/runs/{rid}/score")
def run_score(rid: int):
    """对 run 内过 hard_gate 的候选做 LLM 五维评分（LLM 不可用自动降级规则分）。"""
    try:
        return scorer.score_run(rid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------- M6 批量邀请 ----------
@app.get("/liepin/jobs")
def list_liepin_jobs():
    """拉取企业账号可发起的岗位（BFF jobs 端点）。playwright 批次较慢，前端等待返回。"""
    cfg = _load_settings()
    attach = str(cfg.get("browser_attach") or "false").lower() == "true"
    cookie = cfg.get("liepin_cookie", "")
    if not cookie and not attach:
        # 平台分支：原生 = 登录浏览器主路径；WSL 开发 = liepin login / 粘贴 Cookie
        native = os.name == "nt"
        raise HTTPException(
            status_code=400,
            detail=("未配置猎聘 Cookie。请先在设置页点『打开浏览器登录』，在弹出的专用窗口"
                    "手动登录，系统会自动导入并保存" if native else
                    "未配置猎聘 Cookie，请先在设置页粘贴并测试连接"
                    "（或开启『liepin 浏览器』模式后先运行 liepin login）"))

    def _do():
        session = lp.LiepinSession(
            cookie, cfg.get("liepin_cookie_type") or "header",
            headless=str(cfg.get("browser_headless") or "false").lower() == "true",
            channel=cfg.get("browser_channel") or "chromium",
            proxy=cfg.get("http_proxy") or cfg.get("https_proxy") or "",
            executable=cfg.get("browser_executable") or "",
            attach=attach)
        session.validate()
        try:
            return session.list_jobs()
        finally:
            session.close()

    try:
        jobs = lp._run_in_dedicated_thread(_do)
    except lp.LiepinLoginError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except lp.LiepinRiskError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return {"ok": True, "jobs": jobs}


class BatchInviteIn(BaseModel):
    candidate_ids: list[int]
    job_id: str
    run_id: int | None = None
    greet_message: str = ""


@app.post("/invites/batch")
def batch_invite(body: BatchInviteIn):
    """对一批候选人发起站内邀请（后台线程限速发送）。"""
    if not body.candidate_ids:
        raise HTTPException(status_code=400, detail="未选择候选人")
    try:
        return invite_service.start_batch(body.candidate_ids, body.job_id,
                                          run_id=body.run_id, greet_message=body.greet_message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/invites")
def list_invites(run_id: int | None = None, tag_id: int | None = None):
    """run_id=按 run 过滤；tag_id=标签模式（候选 ∈ 标签，跨 run）。"""
    return db.list_invites(run_id, tag_id)


@app.post("/invites/stop")
def stop_invites():
    return {"ok": True, "stopped": invite_service.stop_batch()}


@app.post("/invites/{iid}/retry")
def retry_invite(iid: int):
    try:
        return invite_service.retry_invite(iid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------- M8 候选标签（固定名单快照） ----------
class TagIn(BaseModel):
    name: str


class TagMembersIn(BaseModel):
    candidate_ids: list[int]


def _clean_tag_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="标签名不能为空")
    return name


@app.get("/tags")
def list_tags():
    return db.list_tags()


@app.post("/tags")
def create_tag(body: TagIn):
    name = _clean_tag_name(body.name)
    try:
        tid = db.create_tag(name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    db.audit("tag.create", detail=f"id={tid} name={name}")
    return {"id": tid}


@app.put("/tags/{tid}")
def update_tag(tid: int, body: TagIn):
    name = _clean_tag_name(body.name)
    try:
        db.rename_tag(tid, name)
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 409, detail=str(e))
    db.audit("tag.rename", detail=f"id={tid} name={name}")
    return {"ok": True}


@app.delete("/tags/{tid}")
def remove_tag(tid: int):
    if not db.delete_tag(tid):
        raise HTTPException(status_code=404, detail="标签不存在")
    db.audit("tag.delete", detail=f"id={tid}")
    return {"ok": True}


@app.get("/tags/{tid}/members")
def tag_members(tid: int):
    if not db.get_tag(tid):
        raise HTTPException(status_code=404, detail="标签不存在")
    rows = db.list_tag_candidates(tid)
    return _with_resume_links(db.attach_scores(rows))


@app.post("/tags/{tid}/members")
def add_tag_members(tid: int, body: TagMembersIn):
    if not db.get_tag(tid):
        raise HTTPException(status_code=404, detail="标签不存在")
    res = db.add_tag_members(tid, body.candidate_ids)
    db.audit("tag.add_members", detail=f"tag_id={tid} added={res['added']} skipped={res['skipped']}")
    return {"ok": True, "added": res["added"], "skipped": res["skipped"]}


@app.delete("/tags/{tid}/members/{cid}")
def remove_tag_member(tid: int, cid: int):
    if not db.get_tag(tid):
        raise HTTPException(status_code=404, detail="标签不存在")
    db.remove_tag_member(tid, cid)
    db.audit("tag.remove_member", detail=f"tag_id={tid} candidate_id={cid}")
    return {"ok": True}


# ---------- 批量抓取简历（run 自动抓取的手动补充） ----------
@app.post("/runs/{rid}/refetch-resumes")
def batch_refetch_resumes(rid: int):
    """对 run 内初筛通过且未抓的候选（规则分降序前 N）后台批量抓简历。"""
    if not db.get_run(rid):
        raise HTTPException(status_code=404, detail="运行不存在")
    try:
        return resume_batch_service.start_batch(rid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/runs/{rid}/refetch-resumes/status")
def batch_refetch_status(rid: int):
    return resume_batch_service.get_status(rid)


@app.post("/runs/{rid}/refetch-resumes/stop")
def batch_refetch_stop(rid: int):
    return {"ok": True, "stopped": resume_batch_service.stop_batch()}


# ================= 「打开浏览器登录」（spec §4.5）=================
from .liepin import login_browser  # noqa: E402 —— 就近引用（先 grep 确认顶部未 import 过，重复则删本行）


@app.post("/settings/liepin/open-login")
def open_login_browser_endpoint():
    """设置页『打开浏览器登录』：Windows 原生 → 可见窗口拉起专用登录浏览器并打开
    猎聘登录页（登录由用户**手动**完成，绝不自动登录）；WSL 开发 → 引导文案。
    只 relay {ok, message} / {ok: False, error}；明文 cookie 绝不出现。"""
    try:
        return login_browser.open_login_browser(_load_settings())
    except Exception as e:  # noqa: BLE001 —— 任何失败如实回报
        log.warning("open-login 失败: %s", type(e).__name__)
        return {"ok": False, "error": f"打开登录浏览器失败: {type(e).__name__}: {e}"}


# ================= 静态托管（前端零改动，spec §4.3）=================
# 挂在文件最末：全部既有路由先注册，Starlette 按 routes 顺序匹配——
# 新路由/既有路由天然优先于末尾 "/" mount。中间件靠后 add = 请求链外层，
# scope["path"] 改写先于路由匹配生效。只有探测到前端产物才注册，纯 API 模式
# 与现版行为一致。
from pathlib import Path  # noqa: E402 —— tail 专用局部 import


class _StripApiPrefix(BaseHTTPMiddleware):
    """/api/* → /*：api.js baseURL='/api' 生产同源零改动（vite dev 代理做的事后端补上）。"""

    async def dispatch(self, request, call_next):
        path = request.scope.get("path", "")
        if path.startswith("/api/"):
            request.scope["path"] = path[len("/api"):] or "/"
        elif path == "/api":
            request.scope["path"] = "/"
        return await call_next(request)


class _SPAStaticFiles(StaticFiles):
    """history 路由（前端 createWebHistory）兜底：静态文件未命中 → 回 index.html。
    index.html 缺失时（目录探测已保证存在，正常不可达）404 直接上抛。"""

    async def get_response(self, path: str, scope):
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if resp.status_code == 404:
            return await super().get_response("index.html", scope)
        return resp


def _find_web_dir(root: Path | None = None) -> Path | None:
    """按序探测前端产物目录：env RECRUIT_WEB_DIR → <程序根>/frontend/dist →
    <程序根>/web；命中首个含 index.html 的返回，都不在 → None（纯 API 模式）。"""
    base = root if root is not None else Path(__file__).resolve().parent.parent
    candidates: list[Path] = []
    env = os.environ.get("RECRUIT_WEB_DIR", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(base / "frontend" / "dist")
    candidates.append(base / "web")
    for d in candidates:
        try:
            if (d / "index.html").is_file():
                return d
        except OSError:
            continue
    return None


_web_dir = _find_web_dir()
if _web_dir:
    log.info("托管前端产物: %s（/api 由中间件还原）", _web_dir)
    app.add_middleware(_StripApiPrefix)
    app.mount("/", _SPAStaticFiles(directory=str(_web_dir), html=True), name="web")
else:
    log.info("未找到前端产物目录，纯 API 模式（与现版一致）")

