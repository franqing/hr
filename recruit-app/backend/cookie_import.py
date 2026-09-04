"""liepin cookie 自动导入：监听 `liepin login` 常驻 Chrome 的 Cookie 库变化。

用户每次在终端**手动**完成 `liepin login`（系统绝不自动登录）后，新会话写入
Windows 侧 liepin-cli profile 的 Cookie 库。本模块定期 stat 其 mtime，发现变化
→ 拉起 Windows 侧 CDP 导出助手（liepin/win/liepin_export_cookie.mjs）取 cookie
→ 与库内已存会话做关键登录态比对 → 门户 + BFF 双验证 → 通过才 Fernet 加密落库 + audit。

守则：
- 登录永远由用户手动完成；本模块只读取浏览器导出结果，绝不驱动/弹出浏览器。
- cookie 只在 Windows 本机 → 本地文件 → Fernet 加密库之间流转；日志不落明文。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import db
from .security import encrypt_str, decrypt_str
from .liepin.adapter import validate_cookie_settings

log = logging.getLogger("recruit.cookie_import")

# liepin-cli 常驻 Chrome 的 Cookie 库相对路径（Chrome >=130 在 Network/，旧版在 Default/）
_WATCH_REL = [
    Path("user-data/Default/Network/Cookies"),
    Path("user-data/Default/Cookies"),
]
_POLL_SECONDS = 15
_EXPORT_TIMEOUT = 60  # Windows 侧 CDP 导出助手超时
_NODE_CANDIDATES = [
    Path("/mnt/c/Program Files/nodejs/node.exe"),
]
_SCRIPT_REL = Path("liepin/win/liepin_export_cookie.mjs")
# 参与会话比对的登录态 cookie；acw_tc/smidV2 等杂项每次访问都变，不比
_AUTH_KEYS = ("lt_auth", "liepin_login_valid", "UniqueKey",
              "XSRF-TOKEN", "__sessionId", "_e_ld_auth_")


# ---------- 原生分支（Windows 安装版）：自启专用登录浏览器 + Python CDP ----------
def _native() -> bool:
    """安装版判定（spec §4.4）：os.name == 'nt' —— 原生路径永不依赖 node/liepin-cli/WSL 组件。"""
    return os.name == "nt"


def _login_browser():
    """延迟 import：登录浏览器仅在原生分支用到（playwright 系 lazy 加载，模块级导入无副作用）。"""
    from .liepin import login_browser
    return login_browser

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_ready = False
_first_run = True
_baseline: dict[str, float] = {}  # Cookie 库文件 → 已处理到的 mtime
_win_home: str | None = None
_cookie_file_win: str | None = None
_node_exe: str | None = None
_script_win: str | None = None


# ---------- Windows 工具链解析 ----------
def _wslpath(win_path: str) -> str:
    """Windows 路径 → WSL 路径；wslpath 不可用时手动映射盘符兜底。"""
    try:
        out = subprocess.run(["wslpath", "-u", win_path],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    p = win_path.replace("\\", "/")
    if len(p) > 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


def _winpath(upath: str) -> str:
    """WSL 路径 → Windows 路径（node.exe 是 Windows 二进制，必须收 Windows 路径）。"""
    try:
        out = subprocess.run(["wslpath", "-w", upath],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    if upath.startswith("/mnt/"):
        return upath[5].upper() + ":" + upath[6:].replace("/", "\\")
    return upath


def _windows_home() -> str | None:
    """探测 Windows 用户主目录（C:\\Users\\<用户>），供读 .liepin-cli 文件。"""
    for exe in ("/mnt/c/Windows/System32/cmd.exe", "cmd.exe"):
        try:
            out = subprocess.run([exe, "/c", "echo %USERPROFILE%"],
                                 capture_output=True, text=True, timeout=10)
            v = out.stdout.strip()
            if len(v) > 1 and v[1] == ":":
                return v
        except Exception:  # noqa: BLE001
            continue
    return None


def _resolve_node() -> str | None:
    for p in _NODE_CANDIDATES:
        if p.exists():
            return str(p)
    found = shutil.which("node.exe")
    return found


def _ensure_ready() -> str | None:
    """一次性解析工具链。返回错误串；None=就绪。调用方需持有 _LOCK。"""
    global _ready, _win_home, _cookie_file_win, _node_exe, _script_win
    if _ready:
        return None
    if _native():
        # 原生分支工具链 = 专用登录浏览器 profile（Python CDP 导出），零 node /
        # liepin-cli / wslpath；目录由 login_browser 侧自理。首轮 _tick 会 stat
        # Cookie 库 mtime——文件尚未生成时 OSError 跳过，浏览器登录后自然接上。
        _ready = True
        return None
    if _win_home is None:
        _win_home = _windows_home()
        if not _win_home:
            return "无法解析 Windows 用户主目录（%USERPROFILE% 为空），自动导入不可用"
        _cookie_file_win = os.path.join(_win_home, ".liepin-cli", "cookie_header.txt")
    if _node_exe is None:
        _node_exe = _resolve_node()
    if not _node_exe:
        return "未找到 Windows node.exe，自动导入不可用"
    if _script_win is None:
        rel = Path(__file__).resolve().parent / _SCRIPT_REL
        if not rel.exists():
            return f"导出助手缺失: {rel}"
        _script_win = _winpath(str(rel))  # 仓库路径本就在 WSL 侧 → 转 Windows 供 node.exe 用
    _ready = True
    return None


def _watch_paths() -> list[Path]:
    """Cookie 库路径：原生分支 = 登录浏览器 profile（Chrome>=130 在 Network/）；
    WSL 分支维持原解析（liepin-cli 常驻 Chrome 的 .liepin-cli 路径）。"""
    if _native():
        return _login_browser().cookie_lib_paths()
    if _win_home is None:
        return []
    base = Path(_wslpath(os.path.join(_win_home, ".liepin-cli")))
    return [base / r for r in _WATCH_REL]


# ---------- 导出与比对 ----------
def _export_header() -> tuple[bool, str, str]:
    """拉起 Windows 侧 CDP 助手，读回 cookie 头。返回 (ok, header|"", info|error)。"""
    err = _ensure_ready()
    if _native():
        # Python CDP 直连登录浏览器（零 node）；三元组契约与 WSL 分支一致
        try:
            return _login_browser().export_login_cookies()
        except Exception as e:  # noqa: BLE001
            return False, "", f"导出失败: {e}"
    if err:
        return False, "", err
    try:
        proc = subprocess.run([_node_exe, _script_win],
                              capture_output=True, text=True, timeout=_EXPORT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "", "导出助手超时（Windows 浏览器无响应？）"
    except Exception as e:  # noqa: BLE001
        return False, "", f"拉起导出助手失败: {e}"
    err_txt = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return False, "", err_txt or f"导出助手退出码 {proc.returncode}"
    cookie_file = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("OUT="):
            cookie_file = line[4:].strip()
    if not cookie_file:
        return False, "", "导出助手未返回输出文件路径"
    try:
        header = Path(_wslpath(cookie_file)).read_text(encoding="utf-8", errors="replace").strip()
    except OSError as e:
        return False, "", f"读取导出文件失败: {e}"
    if not header:
        return False, "", "导出的 cookie 为空"
    return True, header, (proc.stdout or "").strip()


def _stored_cookie() -> str:
    raw = db.get_settings().get("liepin_cookie", "")
    if not raw:
        return ""
    try:
        return decrypt_str(raw)
    except Exception:  # noqa: BLE001 —— 密文损坏按未配置处理
        return ""


def _fingerprint(header: str) -> tuple:
    """只取登录态 cookie 的值做会话指纹（忽略每次访问都变的杂项）。"""
    pairs: dict[str, str] = {}
    for seg in (header or "").split(";"):
        seg = seg.strip()
        if "=" in seg:
            k, v = seg.split("=", 1)
            pairs[k.strip()] = v.strip()
    return tuple((k, pairs.get(k, "")) for k in _AUTH_KEYS)


def _load_cfg() -> dict:
    """延迟引用 main（防循环导入）：复用同一套设置读写与默认值。"""
    from . import main as m
    return m._load_settings()


def _save(header: str, source: str) -> None:
    db.save_setting("liepin_cookie", encrypt_str(header))
    db.save_setting("liepin_cookie_type", "header")  # 导入格式固定为 Cookie 头
    db.audit("settings.import", detail=f"source={source} cookie_type=header")
    log.info("liepin cookie 已%s导入（门户 + BFF 双验证通过）",
             "自动" if source == "auto" else "手动")


def import_now(force: bool = False, source: str = "manual") -> dict:
    """导出 → 会话指纹比对 → 双验证 → 落库。

    force=True（手动按钮）：指纹相同也跑一次真实验证，用真实结果回报；
    force=False（自动轮询）：指纹相同直接跳过（同会话不重复写库/audit）。
    返回 {ok, changed, message} 或 {ok: False, error}；绝不打印 cookie 明文。
    """
    with _LOCK:
        ok, header, info = _export_header()
        if not ok:
            return {"ok": False, "error": info}
        stored = _stored_cookie()
        same = bool(stored) and _fingerprint(header) == _fingerprint(stored)
        if same and not force:
            return {"ok": True, "changed": False,
                    "message": "liepin 登录会话与库内一致，无需更新"}
        cfg = _load_cfg()
        try:
            res = validate_cookie_settings(
                header, "header",
                # 导入/校验一律无头：绝不因用户设置了「可见浏览器」就在桌面上弹窗
                headless=True,
                channel=cfg.get("browser_channel") or "chromium",
                proxy=cfg.get("http_proxy") or cfg.get("https_proxy") or "",
                executable=cfg.get("browser_executable") or "")
        except Exception as e:  # noqa: BLE001 —— 与测试连接一致，任何失败如实返回
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error") or "会话验证未通过，未保存"}
        if same:
            # 指纹相同但（手动）验证通过：不重写密文，避免无谓轮换
            return {"ok": True, "changed": False,
                    "message": res.get("message") or "会话有效，与库内一致"}
        _save(header, source)
        return {"ok": True, "changed": True,
                "message": (res.get("message") or "会话验证通过") + "；Cookie 已自动导入并保存"}


# ---------- 轮询监听 ----------
def _tick():
    global _first_run
    changed = _first_run
    _first_run = False
    for p in _watch_paths():
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if _baseline.get(str(p), 0) < mt:
            changed = True
        _baseline[str(p)] = mt  # 无论结果如何都推进基线，防同一变化反复触发
    if not changed:
        return
    log.info("检测到 liepin 登录浏览器 Cookie 库变化，尝试自动导入")
    try:
        res = import_now(force=False, source="auto")
    except Exception:  # noqa: BLE001 —— 常驻轮询单轮异常不影响后续
        log.exception("cookie 自动导入异常")
        return
    if not res.get("ok"):
        log.warning("cookie 自动导入未生效（等下次会话变化重试）: %s", res.get("error"))
    elif res.get("changed"):
        log.info("liepin cookie 自动导入完成")
    else:
        log.info("liepin cookie 自动导入：%s", res.get("message"))


def _loop():
    log.info("liepin cookie 自动导入监听启动（每 %ss stat profile）", _POLL_SECONDS)
    while not _STOP.wait(_POLL_SECONDS):
        try:
            _tick()
        except Exception:  # noqa: BLE001 —— 常驻轮询单轮异常不影响后续
            log.exception("cookie 导入轮询异常")


def start() -> None:
    """幂等启动监听线程；启动即做一次基线探测 + 首轮导入尝试。

    首轮尝试覆盖「后端停机期间用户已完成 liepin login」的场景：profile 里的
    新会话与库内指纹不同 → 自动导入（这正是 -1701 踢出后的自愈路径）。
    """
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    with _LOCK:
        err = _ensure_ready()
        if err:
            log.warning("liepin cookie 自动导入未启动：%s", err)
            return
        for p in _watch_paths():
            try:
                _baseline[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="cookie-import", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()
    if _THREAD:
        _THREAD.join(timeout=_POLL_SECONDS)
