"""「打开浏览器登录」：Windows 原生登录导入主路径（spec §4.5）。

设置页点按钮 → 以**可见窗口（headed）**拉起专用登录浏览器（独立 profile
<data>\\login-profile，绝不碰日常浏览器）并打开猎聘登录页 → 用户**手动**输入账号
密码/扫码（系统绝不自动登录、绝不代填）→ cookie_import 每 15s stat 本 profile 的
Cookie 库 mtime，变化时经 Python CDP 导出 → 指纹比对 → 门户+BFF 双验证 → Fernet 落库。

边界与守则：
- 浏览器跟随设置 channel：默认 = 随包 playwright chromium（Windows 完整版，headed 可用，
  由 PLAYWRIGHT_BROWSERS_PATH 指向安装版 browsers\\ 目录）；选 chrome = 本机 Chrome +
  同一专用 profile；解析不到 → 报错引导，不静默换引擎。
- 只回收本进程**自己拉起**的实例（启动时记录 PID，精确 terminate/kill）；用户随手手动
  关闭窗口 = 幂等（后续 attach/导出会明确报错引导重新点按钮）；绝不动用户其它浏览器。
- 端口候选 (53471, 9222)，与 WSL 版 CDP 探测端口同集合；都被占 → 明确报错。
- 明文 cookie 只在「本机浏览器 → Fernet 加密库」之间流转，本模块绝不落日志。
- playwright sync API 的 greenlet 不能跨线程复用：所有连远端浏览器的操作（CDP 导出）
  都在**新线程 + 新 sync_playwright 实例**里完成（见 _with_cdp），只 connect_over_cdp
  + 取数，收尾仅 pw.stop() 断连，绝不 close/kill 远端浏览器（登录窗口保持可用）。
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from ..paths import data_dir

log = logging.getLogger("recruit.liepin.login_browser")

_LOGIN_URL = "https://passport.liepin.com/login"
# 启动优先 53471（与 liepin_export_cookie.mjs 的 PORTS=[9222, 53471] 同集合对齐）
_PORT_CANDIDATES = (53471, 9222)
_STARTUP_TIMEOUT = 25.0      # 从拉起浏览器到 CDP 端口可连（秒）
# 登录 profile 下 Chrome Cookie 库相对路径（Chrome >=130 在 Default/Network/）
_COOKIE_REL = (Path("Default/Network/Cookies"), Path("Default/Cookies"))

# 本进程自启实例记录（open/close 都持 _STATE_LOCK 串行化）
_STATE_LOCK = threading.Lock()
_browser: dict | None = None   # {"proc","port","base","profile"}


def profile_dir() -> Path:
    """专用登录浏览器 profile（= data_dir()/login-profile）。"""
    return data_dir() / "login-profile"


def cookie_lib_paths() -> list[Path]:
    """登录 profile 下 Chrome Cookie 库路径（供 cookie_import 原生分支 stat mtime）。"""
    base = profile_dir()
    return [base / r for r in _COOKIE_REL]


# ---------- 浏览器可执行文件解析 ----------
_CHROME_CANDIDATES = (
    ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
    ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
    ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
)


def _env_key(key: str) -> str:
    """env 键直通；唯一特殊化：PROGRAMFILES(X86) 里的括号在 .bat 注入 env 时是字面量，
    Python os.environ 读的是进程真实环境——直接用它原名即可。"""
    return key


def _resolve_login_exe(cfg: dict) -> str:
    """登录浏览器可执行文件。优先显式 executable；channel=chrome → 本机 Chrome 标准
    路径候选；默认 → 随包 playwright chromium（Windows 完整版，headed 可用）。
    探测不到 → RuntimeError 清晰引导，绝不静默换引擎。"""
    explicit = (cfg.get("browser_executable") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        raise RuntimeError(f"设置的浏览器可执行文件不存在: {explicit}")
    channel = (cfg.get("browser_channel") or "chromium").strip().lower()
    if channel == "chrome":
        for key, rel in _CHROME_CANDIDATES:
            base = os.environ.get(_env_key(key), "")
            p = Path(base) / rel
            if p.is_file():
                return str(p)
        raise RuntimeError("设置『本机 Chrome』但未找到 chrome.exe——装好 Chrome 或改回默认 Chromium")
    if channel != "chromium":
        raise RuntimeError(f"不支持的浏览器通道: {channel}（原生支持 chromium/chrome）")
    # playwright 随包 chromium 的完整版可执行文件（headed 可用；PLAYWRIGHT_BROWSERS_PATH
    # 指向安装版 browsers\\ 时自动落到随包目录）
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        return pw.chromium.executable_path


# ---------- 端口与进程 ----------
def _port_free(port: int) -> bool:
    try:
        with socket.socket() as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", port)) != 0
    except OSError:
        return True


def _cdp_alive(base: str) -> bool:
    try:
        import httpx
        httpx.get(base + "/json/version", timeout=1.0)
        return True
    except Exception:  # noqa: BLE001 —— 连接失败/超时一律视为不在跑
        return False


def cdp_base_url() -> str | None:
    """本进程记录中的登录浏览器实例若仍在跑 → 返回 CDP 基址；否则 None。"""
    with _STATE_LOCK:
        b = _browser
    if not b:
        return None
    if not _cdp_alive(b["base"]):
        with _STATE_LOCK:
            if _browser is b:      # 实例已死（用户手动关了窗口等）→ 清记录
                _browser = None
        return None
    return b["base"]


def _pick_port() -> int | None:
    """挑第一个空闲候选端口；全被占 → None。"""
    for port in _PORT_CANDIDATES:
        if _port_free(port):
            return port
    return None


def _wait_cdp(proc: subprocess.Popen, base: str) -> None:
    """轮询 CDP 端口直到可连；进程先退/超时 → 报错并收尾进程。"""
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"浏览器进程提前退出（code={proc.returncode}）——"
                               "多半被杀软拦截或该浏览器无法带调试端口启动")
        if _cdp_alive(base):
            return
        time.sleep(0.5)
    raise RuntimeError("登录浏览器启动超时（CDP 端口未就绪）")


def open_login_browser(cfg: dict) -> dict:
    """可见窗口拉起专用登录浏览器并打开猎聘登录页；登录由用户手动完成。

    只返回 {ok, message} / {ok: False, error}（明文 cookie 绝不出现）。WSL 开发
    模式（os.name != 'nt'）→ 引导文案（回归基线走 liepin login / 粘贴 Cookie）。
    """
    if os.name != "nt":
        return {"ok": False, "error":
                "『打开浏览器登录』是 Windows 原生（安装版）能力。WSL 开发请跑 liepin login "
                "自动导入，或直接粘贴 Cookie 兜底。"}
    with _STATE_LOCK:
        existing = _browser
    if existing and _cdp_alive(existing["base"]):
        return {"ok": True, "message":
                f"登录浏览器已在运行（端口 {existing['port']}）。如尚未登录请直接在那个窗口"
                "完成；系统约 20 秒后会自动导入。"}
    try:
        exe = _resolve_login_exe(cfg)
        port = _pick_port()
        if port is None:
            return {"ok": False, "error":
                    "登录浏览器端口 53471/9222 均被占用。请关闭占用这两个端口的程序后重试。"}
        profile = profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        argv = [exe,
                f"--user-data-dir={profile}",
                f"--remote-debugging-port={port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate,MediaRouter"]
        proxy = cfg.get("http_proxy") or cfg.get("https_proxy") or ""
        if proxy:
            argv.append(f"--proxy-server={proxy}")
        argv.append(_LOGIN_URL)
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_cdp(proc, base)
        except Exception:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            raise
        with _STATE_LOCK:
            _browser = {"proc": proc, "port": port, "base": base, "profile": str(profile)}
        log.info("登录浏览器已打开 port=%s（登录由用户手动完成）", port)
        return {"ok": True, "message":
                f"已弹出专用登录浏览器（端口 {port}）。请在弹出的窗口里<b>手动</b>输入账号"
                "密码或扫码登录（系统绝不自动登录）；约 20 秒后会自动把会话导入到这里。"}
    except Exception as e:  # noqa: BLE001 —— 任何失败如实回报，不外泄 cookie
        return {"ok": False, "error":
                f"打开登录浏览器失败: {type(e).__name__}: {e}"
                "（排查：杀软拦截 / 端口被占 / 所选浏览器未安装）"}


def close_login_browser() -> None:
    """回收本进程自启的登录浏览器（仅精确 PID，绝不碰用户其它进程）；幂等。"""
    with _STATE_LOCK:
        b = _browser
        _browser = None
    if not b:
        return
    proc = b.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001 —— 已退出则忽略
                pass
    log.info("登录浏览器已关闭 port=%s", b.get("port"))


# ---------- CDP 取数（跨线程隔离）----------
def _with_cdp(base: str, fn, timeout_s: float = 20.0):
    """在新线程 + 新 playwright 实例里执行 fn(browser)（greenlet 跨线程隔离）。

    只 connect_over_cdp 后调用 fn 取数；结束仅 pw.stop() 断连，**绝不 close/kill
    远端浏览器**（登录窗口保持可用，供 attach 复用/用户查看）。
    """
    result: dict = {}

    def run():
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(base, timeout=timeout_s * 1000)
                result["value"] = fn(browser)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=run, name="cdp-export", daemon=True)
    t.start()
    t.join(timeout=timeout_s + 15)
    if "error" in result:
        raise RuntimeError(result["error"])
    if t.is_alive() or "value" not in result:
        raise RuntimeError("连接登录浏览器超时")
    return result["value"]


def export_login_cookies() -> tuple[bool, str, str]:
    """经 Python CDP 导出登录浏览器里 liepin.com 域 cookie → Cookie 头。

    返回 (ok, header|"", info|error)，与 cookie_import._export_header 契约同构
    （Task 6 直接消费本函数）。窗口未开/空结果 → 明确引导，绝不含糊报错。
    """
    base = cdp_base_url()
    if not base:
        return False, "", "登录浏览器没在运行。请先在设置页点『打开浏览器登录』并手动完成猎聘登录。"
    try:
        def grab(browser):
            for ctx in browser.contexts:
                try:
                    return ctx.cookies()
                except Exception:  # noqa: BLE001 —— 换下一个 context
                    continue
            return []
        cookies = _with_cdp(base, grab)
    except Exception as e:  # noqa: BLE001
        return False, "", f"CDP 导出失败: {e}"
    header = build_cookie_header(cookies, time.time() * 1000)
    if not header:
        return False, "", "浏览器在线但未找到 liepin.com cookie —— 请先在该登录浏览器窗口手动完成猎聘登录"
    return True, header, "ok"


def build_cookie_header(cookies: list[dict], now_ms: float) -> str:
    """playwright/CDP cookie dicts → Cookie 头。筛选规则与 WSL 导出助手
    liepin_export_cookie.mjs 一致：仅 liepin.com 域；会话 cookie（expires 为
    None/-1/0/缺失）保留；已过期（expires*1000 <= now_ms）丢弃。排序取
    domain|name ASCII 升序，只为输出确定性：Cookie 头按集合解析、会话指纹
    按键值对比较（cookie_import._fingerprint），顺序无语义——mjs 侧用的是
    localeCompare（ICU locale 排序，大小写折叠），Python 不做字节级复刻。
    纯函数（唯一可单测的入口；明文不入日志）。"""
    def alive(c: dict) -> bool:
        exp = c.get("expires")
        if exp is None:
            return True
        try:
            return float(exp) in (-1.0, 0.0) or float(exp) * 1000 > now_ms
        except (TypeError, ValueError):
            return True

    wanted = [c for c in (cookies or [])
              if "liepin.com" in (c.get("domain") or "") and alive(c)]
    wanted.sort(key=lambda c: f"{c.get('domain', '')}|{c.get('name', '')}")
    return "; ".join(f"{c['name']}={c.get('value', '')}" for c in wanted)
