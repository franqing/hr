"""猎聘企业版适配器。登录态=粘贴 Cookie（Fernet 加密存储），持久化上下文复用。

镜像 BOM-AI plm/adapter.py 的全部防坑模式：
- 全局 threading.Lock：同一 profile 目录同时只能有一个浏览器实例
- _cleanup_stale_profiles：清理残留 chromium（孤儿进程持 profile SingletonLock 会打死后续所有启动）
- _launch_with_retry：容忍残留进程尚未释放 profile 锁的瞬时失败
- _run_in_dedicated_thread：playwright sync API 的 greenlet 绑定启动线程，FastAPI 线程池复用
  会带 contextvars 报 "Event loop is closed"，必须用用后即弃的专用线程跑整个批次
- LiepinQueue 串行限速 + 整批 deadline：宁可部分结果也不无限挂死（否则全局锁被永久持有）

与 PLM 的关键差异：
- 无表单登录：先 clear_cookies 再 add_cookies 粘贴的 cookie，保证粘贴的会话权威
- 搜索走 BFF API（in-page fetch，避免导航触发风控），CORS 拦截时降级 httpx
- 风控检测：滑块/行为异常/境外 IP/headless —— 命中立即停止整批，让人工介入
- 绝不抓手机/邮箱；只存最小必要字段
"""
import json, logging, os, re, signal, subprocess, threading, time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright

from .. import db

log = logging.getLogger("recruit.liepin")

def user_data_dir() -> Path:
    """浏览器 profile 数据目录（= data_dir()/browser_profile）。call-time 解析。"""
    from ..paths import data_dir
    return data_dir() / "browser_profile"
CACHE_TTL = timedelta(hours=24)
_CODES = json.loads((Path(__file__).resolve().parent / "codes.json").read_text(encoding="utf-8"))
CODES = _CODES  # 公开别名：run_service / invite_service 读取限额等配置

# cookie 域兜底：粘贴的 Cookie 头不含域，统一挂到 .liepin.com（覆盖 lpt/api-lpt）
_DEFAULT_DOMAIN = ".liepin.com"

# 简历直达页：只带 resume_id + sfrom 的官方路由（liepin-cli greet.js:96 校准的简历页
# 链接形状）。任何候选只要 resume_id 非空即可构造；浏览器带企业账号登录态直接点开。
_RESUME_DETAIL_URL = "https://lpt.liepin.com/resume/detail?resIdEncode={rid}&sfrom=R_SEARCH_CONDITION"


def resume_detail_url(resume_id: str | None) -> str | None:
    """构造猎聘简历直达链接（候选人详情页/导出用）。resume_id 为空返回 None。"""
    if not resume_id:
        return None
    return _RESUME_DETAIL_URL.format(rid=quote(str(resume_id)))


class LiepinLoginError(RuntimeError):
    """登录态不可用/已失效。整批快速失败，提示重新粘贴 Cookie。"""


class LiepinRiskError(RuntimeError):
    """命中平台风控（滑块/行为异常/拦截）。立即停止，让人工在真实浏览器验证。"""


class LiepinStopRequested(Exception):
    """调用方主动停止本批（stop 标志）。不是错误。"""


# 串行化：同一 profile 目录同时只能有一个浏览器实例。
_LIEPIN_LOCK = threading.Lock()


def _cleanup_stale_profiles() -> int:
    marker = f"--user-data-dir={user_data_dir()}"
    try:
        out = subprocess.run(["pgrep", "-f", marker], capture_output=True,
                             text=True, timeout=10)
    except Exception:
        return 0
    me = os.getpid()
    n = 0
    for pid in out.stdout.split():
        try:
            p = int(pid)
        except ValueError:
            continue
        if p == me:
            continue
        try:
            os.kill(p, signal.SIGKILL)
            n += 1
        except ProcessLookupError:
            pass
    return n


# ---------- Windows 浏览器外部拉起 + CDP 桥（channel=msedge/chrome / 显式 exe） ----------
# playwright driver 用 fd-pipe 传输跟浏览器进程通信，fd 无法跨 WSL interop 传递
# （Windows Edge 收不到 pipe fd 直接 exitCode=13），且 executable_path 传非 Linux
# 路径会被 playwright 解析成畸形目录 —— “换浏览器后 run 不起来”的两个根因都在这里。
# 对策：Windows exe 一律由本进程外部拉起（Edge 直接收 Windows 真实路径），Edge 的
# DevTools 只绑 Windows loopback（Chromium 新版本忽略 --remote-debugging-address），
# 再由同机 PowerShell relay 把 0.0.0.0:RELAY → localhost:CDP 双向桥到 WSL 网关可达
# 地址，playwright connect_over_cdp 从 WSL 侧接入。端口为本模块私有常量，全局锁
# 保证同一时刻只有一次 run；每次启动前按端口精确清理残留监听者（只 taskkill 精确
# PID，绝不 /IM 整类浏览器——会误杀用户真实 Edge）。
_CDP_PORT = 9337          # Edge 自身 DevTools 监听端口（Windows loopback）
_RELAY_PORT = 9338        # PowerShell relay 监听端口（0.0.0.0，WSL 侧可达）
_CDP_STARTUP_TIMEOUT = 45.0
_RELAY_PS1 = Path(__file__).resolve().parent / "cdp_relay.ps1"

# 附件模式（attach）：复用 `liepin login`（@viyzhu/liepin-cli）的常驻 Chrome，
# 绝不新起浏览器实例。其调试端口协议与 win/liepin_export_cookie.mjs 的 PORTS 同源
# （53471=liepin-cli 默认；9222=曾被 LIEPIN_BROWSER_REMOTE_DEBUGGING_PORT 覆盖）。
# 命中端口属于用户真实浏览器（登录态载体）：只读探测，绝不 kill；由 relay 桥到
# 0.0.0.0:9338，WSL 侧 connect_over_cdp 接入。
_ATTACH_PORTS = (9222, 53471)
_ATTACH_STARTUP_TIMEOUT = 10.0


def _win_path(p: Path) -> str:
    """/mnt/d/... → D:\\...（仅给 Windows 子进程当参数；playwright 参数仍用 Linux 路径）。"""
    s = str(p)
    m = re.match(r"^/mnt/([a-zA-Z])/", s)
    if m:
        return f"{m.group(1).upper()}:\\{s[m.end():].replace('/', '\\')}"
    return s


def _wsl_gateway() -> str:
    """WSL2 NAT 下 Windows 主机的可达地址（默认路由网关）。"""
    try:
        out = subprocess.run(["ip", "-4", "route", "show", "default"],
                             capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"default via (\S+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    raise LiepinLoginError(
        "无法确定 WSL 网关地址（需要 WSL2 NAT 网络）：请检查 ip route")


def _win_native() -> bool:
    """Windows 原生判定（spec §4.4）：os.name == 'nt' 恒为真，不做任何启发式探测。"""
    return os.name == "nt"


def _windows_kill_listener(port: int) -> int:
    """精确杀掉 Windows 侧监听指定端口的进程（netstat 定位 PID → taskkill /PID /F /T）。
    仅限本应用私有端口（_RELAY_PORT / _CDP_PORT 等自己拉起的）；绝不 taskkill /IM ——
    整类名匹配会误杀用户真实浏览器。/T 连带子进程树：只杀主进程会让 Edge 的孤儿
    子进程（gpu/network/crashpad）继续持有 profile 目录句柄，下次同目录启动即卡死。"""
    try:
        out = subprocess.run(
            ["cmd.exe", "/c", f"netstat -ano | findstr :{port} | findstr LISTENING"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return 0
    pids = {int(parts[-1]) for line in out.splitlines()
            if (parts := line.split()) and parts[-1].isdigit()}
    n = 0
    for pid in pids:
        try:
            subprocess.run(["taskkill.exe", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, text=True, timeout=20)
            n += 1
        except Exception:
            pass
    return n


_CDP_CONNECT_TIMEOUT_MS = 20_000  # ms；connectOverCDP 全流程受此 deadline 约束


def _connect_cdp(pw, base: str):
    """有界 connect_over_cdp —— 在调用方线程内直调，用 playwright 原生 timeout。

    playwright 1.62 的 connectOverCDP 全流程都挂在 ProgressController deadline 上：
    前置 /json/version 拉取（fetchData 经 progress.race，超时即 cancel 底层请求）
    与 WS 握手（transport connect 同样 race deadline）——传 timeout（毫秒）即整段
    有界。绝不能把调用丢进其它线程做看门狗：playwright sync API 的 greenlet 绑定
    启动线程，跨线程调用直接报 "Cannot switch to a different thread"（此前
    ThreadPoolExecutor 包装的教训：每次 connect 秒挂，45s 启动期限烧完才报错）。
    超时在此转终态 LiepinLoginError（端点僵死不会自愈，中止走上层清理路径——
    回收 relay/Edge、释放全局锁）；其它瞬时错误如实上抛，由调用方轮询重试到期限。
    """
    try:
        return pw.chromium.connect_over_cdp(base, timeout=_CDP_CONNECT_TIMEOUT_MS)
    except Exception as e:  # noqa: BLE001 —— 咽喉点：判定是否超时，决定终态或可重试
        detail = getattr(e, "message", None) or str(e)
        if type(e).__name__ == "TimeoutError" or detail.startswith("Timeout"):
            raise LiepinLoginError(
                f"CDP 连接未在 {_CDP_CONNECT_TIMEOUT_MS // 1000}s 内建立"
                "（浏览器 CDP 端点僵死或已被占用），已中止本次操作。") from None
        raise


def _windows_listening(ports: tuple[int, ...]) -> list[int]:
    """只读探测：Windows 侧哪些端口正处于 LISTENING（netstat，无任何副作用）。
    返回按入参顺序排列的命中子集——用于附件模式定位 liepin login 常驻 Chrome，
    只探测绝不 kill。"""
    try:
        out = subprocess.run(
            ["cmd.exe", "/c", "netstat -ano | findstr LISTENING"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    hit = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "TCP":
            continue
        local = parts[1]               # 形如 127.0.0.1:9222 / [::]:53471
        port = local.rsplit(":", 1)[-1]
        if port.isdigit() and int(port) in ports and int(port) not in hit:
            hit.append(int(port))
    return [p for p in ports if p in hit]   # 按候选顺序返回，探测确定性优先


def _clear_profile_locks() -> int:
    """删除 profile 目录里残留的 Singleton* 锁文件。Edge 被异常终止后，残留锁会让
    下次启动静默 exit 21（空日志即退）——外部拉起前必须清理。"""
    if not user_data_dir().is_dir():
        return 0
    n = 0
    for f in user_data_dir().glob("Singleton*"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


# ---------- 浏览器自检（WSL / 无图形界面环境） ----------
_WINDOWS_BROWSERS = [
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe",
]


def _resolve_browser(channel: str | None, executable: str | None = None) -> dict:
    """决定用哪个浏览器，返回 launch_persistent_context 的定位参数子集。

    优先级：
      1. 显式指定 browser_executable（设置页可配）——直接用 executable_path；
      2. channel=chrome/msedge 等真实浏览器通道时——Linux 通常没装，探测 Windows
         Chrome/Edge exe（WSL interop 可执行），命中则用 executable_path；
      3. 兜底 bundled chromium（channel=None）。
    找不到时返回 {}，让 launch 抛「未安装 chromium」的提示性错误。
    """
    if executable:
        exe = str(executable).strip()
        if exe and Path(exe).exists():
            return {"executable_path": exe}
        raise LiepinLoginError(f"浏览器可执行文件不存在: {exe}")
    if channel and channel not in (None, "", "chromium"):
        for cand in _WINDOWS_BROWSERS:
            if Path(cand).exists():
                log.info("channel=%s 在 Linux 不可用，改用 Windows 浏览器 %s", channel, cand)
                return {"executable_path": cand}
    return {}


# ---------- 简历脱敏与文本拼装（合规红线：绝不存手机/邮箱） ----------
_SECRET_KEY_RE = re.compile(
    r"phone|mobile|tel\b|email|wechat|wx|微信|手机|电话|邮箱|联系方式|身份证|idcard",
    re.I)


def _sanitize(v):
    """递归删除含敏感标识的键（手机/邮箱/微信/身份证等），防漏存联系方式。"""
    if isinstance(v, dict):
        return {k: _sanitize(val) for k, val in v.items()
                if not _SECRET_KEY_RE.search(str(k))}
    if isinstance(v, list):
        return [_sanitize(item) for item in v]
    return v


def _resume_to_text(data: dict) -> str:
    """把简历详情响应防御性地拼成纯文本（供 matcher 关键词扫描）。去重保序。"""
    seen: set = set()
    parts: list[str] = []

    def walk(v, depth=0):
        if depth > 5 or v is None or v == "" or v == []:
            return
        if isinstance(v, dict):
            for k, val in v.items():
                if val is None or val == "" or val == []:
                    continue
                if isinstance(val, (list, dict)):
                    walk(val, depth + 1)
                else:
                    s = str(val).strip()
                    if s and s not in seen:
                        seen.add(s)
                        parts.append(s)
        elif isinstance(v, list):
            for item in v:
                walk(item, depth + 1)
        else:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                parts.append(s)

    walk(_sanitize(data))
    return "\n".join(parts)


# ---------- cookie 解析 ----------
def parse_cookie_string(s: str, fmt: str = "header") -> list[dict]:
    """把三种格式的粘贴 Cookie 解析成 playwright add_cookies 需要的列表。

    header: 'a=1; b=2'（DevTools 复制）
    pairs:  每行 'name=value'
    json:   EditThisCookie 导出 [{"name","value","domain",...}]
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("Cookie 为空")
    if fmt == "json":
        data = json.loads(s)
        if not isinstance(data, list):
            raise ValueError("Cookie JSON 应为数组")
        out = []
        for c in data:
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                continue
            out.append({
                "name": str(c["name"]),
                "value": str(c["value"]),
                "domain": c.get("domain") or _DEFAULT_DOMAIN,
                "path": c.get("path") or "/",
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", True)),
            })
        if not out:
            raise ValueError("JSON 中未解析出任何 cookie")
        return out
    if fmt == "pairs":
        items = [line.strip() for line in s.splitlines() if "=" in line]
    else:  # header
        items = [seg.strip() for seg in s.split(";") if "=" in seg]
    out = []
    for item in items:
        name, _, value = item.partition("=")
        name, value = name.strip(), value.strip()
        if not name:
            continue
        out.append({"name": name, "value": value, "domain": _DEFAULT_DOMAIN,
                    "path": "/", "httpOnly": True, "secure": True})
    if not out:
        raise ValueError("未解析出任何 cookie，请检查粘贴内容")
    return out


# ---------- 会话 ----------
class LiepinSession:
    def __init__(self, cookie_str: str, cookie_type: str, codes: dict = None,
                 headless: bool = True, channel: str = "chrome", delays: dict = None,
                 proxy: str = "", executable: str = "", attach: bool = False):
        self.codes = codes or _CODES
        self.headless, self.channel = headless, channel
        self.delays = delays or {}
        self.proxy = proxy or ""
        self.executable = executable or ""
        self.attach = attach
        self._ctx = None
        self._cdp = None
        self._pw = None
        self._page = None
        self._lock_held = False
        # 附件模式没有粘贴 cookie 一说：登录态在 liepin 常驻 Chrome 的 profile 里，
        # cookie 参数必然为空串，跳过解析（parse_cookie_string 对空串会 raise）。
        self._cookies = ([] if attach
                         else parse_cookie_string(cookie_str, cookie_type))
        _LIEPIN_LOCK.acquire()
        self._lock_held = True
        self._pw = sync_playwright().start()
        try:
            self._ctx = self._launch_with_retry(attempts=4)
        except Exception:
            if self._cdp:
                # CDP 已接入（relay 已拉起）但后续初始化失败：收回自家 relay 端口，
                # 绝不碰目标（用户）浏览器的调试端口
                try:
                    _windows_kill_listener(_RELAY_PORT)
                except Exception:
                    pass
                self._cdp = None
            self._pw.stop()
            self._release_lock()
            raise
        if attach:
            # 附件模式永远新开自己的 tab：绝不动用户当前正开的页面（pages[0]
            # 可能正是用户在看的企业门户/简历页，不能劫持）。
            self._page = self._ctx.new_page()
        else:
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def _attach_existing(self):
        """附件模式：接入 `liepin login`（liepin-cli）的常驻 Chrome，复用其登录态。

        铁律（本方法 + close 共同遵守）：
        - 只只读探测 liepin-cli 调试端口（_ATTACH_PORTS），探测命中后由 relay 把
          0.0.0.0:_RELAY_PORT → localhost:<目标端口> 双向桥接（DevTools 只绑
          Windows loopback，WSL2 NAT 下必须经网关）；
        - 绝不 _cleanup_stale_profiles / 绝不 kill 目标端口 —— 那是用户登录中的
          真实浏览器；本方法唯一会清理的是自己拉起的 relay（_RELAY_PORT，本应用
          私有端口）；
        - 目标 Chrome 的默认 context 直接复用：浏览器 profile 原生登录态即会话，
          后续 _inject_cookies 必须跳过（clear_cookies 会杀掉用户真实登录）。
        """
        alive = _windows_listening(_ATTACH_PORTS)
        if not alive:
            raise LiepinLoginError(
                "未发现 liepin login 的常驻 Chrome（调试端口 "
                f"{'/'.join(str(p) for p in _ATTACH_PORTS)} 均无监听）。\n"
                "请在终端运行 `liepin login` 登录并保持 Chrome 打开，再点击测试连接。")
        log.info("附件模式：探测到 liepin 常驻 Chrome 调试端口 %s", alive)
        last_err = ""
        for port in alive:
            relay = None
            try:
                _windows_kill_listener(_RELAY_PORT)   # 只清自有 relay 端口残留
                relay = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", _win_path(_RELAY_PS1),
                     str(_RELAY_PORT), "localhost", str(port)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                base = f"http://{_wsl_gateway()}:{_RELAY_PORT}"
                deadline = time.time() + _ATTACH_STARTUP_TIMEOUT
                while time.time() < deadline:
                    if relay.poll() is not None:
                        raise LiepinLoginError(
                            f"relay（9338→localhost:{port}）提前退出，无法桥接调试端口")
                    try:
                        r = httpx.get(f"{base}/json/version", timeout=3)
                        if r.status_code == 200 and r.json().get("webSocketDebuggerUrl"):
                            browser = _connect_cdp(self._pw, base)
                            ctx = (browser.contexts[0] if browser.contexts
                                   else browser.new_context())
                            self._cdp = {"base": base, "attach": True,
                                         "target_port": port}
                            log.info("已接入 liepin 常驻 Chrome（CDP 端口 %d）", port)
                            # 注意：成功路径绝不在此清理 relay —— CDP 连接的生命线
                            # 就是这条 9338 桥，它要在 connect 期间一直存活，直到
                            # close()（按端口回收）。结构与 _launch_external_cdp
                            # 相同：只有失败路径才 terminate + 清端口。
                            return ctx
                    except Exception as e:
                        last_err = f"{type(e).__name__}: {e}"
                    time.sleep(0.5)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                # 仅失败路径：回收自己拉起的 relay，不碰用户 Chrome 的目标端口
                if relay is not None and relay.poll() is None:
                    try:
                        relay.terminate()
                    except Exception:
                        pass
                _windows_kill_listener(_RELAY_PORT)
        raise LiepinLoginError(
            "接入 liepin 常驻 Chrome 失败：所有候选调试端口均无法建立 CDP 会话。"
            f"（{last_err}）\n排查建议：① 确认 `liepin login` 的 Chrome 保持运行且"
            "处于登录态；② 调试端口（9222/53471）若被其它程序占用，请在 liepin-cli"
            "配置 LIEPIN_BROWSER_REMOTE_DEBUGGING_PORT 更换后重试。")

    def _launch_with_retry(self, attempts: int):
        if _win_native():
            # §4.4 硬门控：Windows 原生（同事机器零 WSL）——CDP 桥 / PowerShell relay /
            # wslpath / /mnt/c 探测等整段互操作代码在 os.name=='nt' 下**永不进入执行**。
            if self.attach:
                return self._attach_native()
            return self._launch_native()
        if self.attach:
            # 附件模式不重试：目标 Chrome 不在就是不在（新起实例违背用户意图），
            # 直接报错引导先跑 liepin login；也绝不 _cleanup_stale_profiles——
            # 那个清理针对的是本应用自拉实例的孤儿进程，与用户浏览器无关。
            return self._attach_existing()
        killed = _cleanup_stale_profiles()
        if killed:
            log.warning("已清理 %d 个占用猎聘 profile 的残留 chromium 进程", killed)
        browser = _resolve_browser(self.channel, self.executable)
        if browser.get("executable_path"):
            # Windows 浏览器（channel=msedge/chrome 或显式 executable_path）：driver 的
            # fd-pipe 传输无法跨 WSL interop（Edge 收不到 pipe fd，exitCode=13），且
            # executable_path 传非 Linux 路径会被 playwright resolve 成畸形目录——
            # “换浏览器后 run 不起来”的根因。改走外部拉起 + CDP 桥（见
            # _launch_external_cdp），直接返回接入好的 context。
            last = None
            for i in range(2):   # 外部启动偶发瞬时失败（残留锁/端口），重试一次
                try:
                    return self._launch_external_cdp(browser["executable_path"])
                except Exception as e:
                    last = e
                    time.sleep(5)
            raise LiepinLoginError(
                "Windows 浏览器（外部拉起）启动失败："
                f"{type(last).__name__}: {last}\n"
                "排查建议：① 确认 Edge/Chrome 已安装，设置页『浏览器通道/可执行文件』"
                "路径正确；② data/browser_profile 未被其它进程占用；③ 杀软/系统策略未"
                "拦截浏览器与 PowerShell；④ 如配置了代理，确认代理已启动且设置页已填。")
        # —— bundled chromium / 通道未命中 Windows exe：playwright 原生拉起 ——
        last = None
        for i in range(attempts):
            try:
                kwargs = {
                    "user_data_dir": str(user_data_dir()),
                    "headless": self.headless,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                kwargs["channel"] = (self.channel if self.channel
                                     and self.channel != "chromium" else None)
                if self.proxy:
                    kwargs["proxy"] = {"server": self.proxy}
                return self._pw.chromium.launch_persistent_context(**kwargs)
            except Exception as e:
                last = e
                time.sleep(5)
        raise LiepinLoginError(
            "浏览器启动失败："
            f"{type(last).__name__}: {last}\n"
            "排查建议：① 无图形界面请确认已安装浏览器，执行 "
            "`.venv/bin/playwright install chromium`（网络受限用 "
            "`PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ .venv/bin/playwright install chromium`）；"
            "② 或在设置页『浏览器可执行文件』直接填 Windows Chrome 路径；"
            "③ 如配置了代理，确认代理已启动且设置页已填。")

    def _launch_external_cdp(self, exe: str):
        """外部拉起 Windows 浏览器，经 PowerShell relay 桥接后 connect_over_cdp 接入。

        步骤：清 stale profile 锁 → 按端口精确清理 CDP/RELAY 残留 → Edge 以 Windows
        真实路径拉起（headless 用 --headless=new，代理走 --proxy-server）→ relay 把
        0.0.0.0:RELAY → localhost:CDP 双向转发 → 轮询 relay 直至 CDP 就绪 →
        connect_over_cdp 接入，取默认 BrowserContext 返回。下游 self._ctx / self._page
        语义不变（cookie 注入、BFF 调用、搜索等代码零改动）。
        """
        profile_locks = _clear_profile_locks()
        if profile_locks:
            log.warning("已清理 %d 个残留 profile 锁（Singleton*）", profile_locks)
        _windows_kill_listener(_CDP_PORT)
        _windows_kill_listener(_RELAY_PORT)
        argv = [exe,
                f"--user-data-dir={_win_path(user_data_dir())}",  # Windows 真实路径，绕开畸形路径
                f"--remote-debugging-port={_CDP_PORT}",
                "--no-first-run"]
        if self.headless:
            argv.append("--headless=new")
        if self.proxy:
            argv.append(f"--proxy-server={self.proxy}")
        log.info("外部拉起浏览器: %s", exe)
        edge = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        relay = None
        try:
            relay = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", _win_path(_RELAY_PS1),
                 str(_RELAY_PORT), "localhost", str(_CDP_PORT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            base = f"http://{_wsl_gateway()}:{_RELAY_PORT}"
            deadline = time.time() + _CDP_STARTUP_TIMEOUT
            last_err = ""
            while time.time() < deadline:
                if edge.poll() is not None and relay.poll() is not None:
                    raise LiepinLoginError(
                        "外部浏览器与 relay 进程均提前退出（启动即退：profile 被占用"
                        "或被杀软拦截）")
                try:
                    r = httpx.get(f"{base}/json/version", timeout=3)
                    if r.status_code == 200 and r.json().get("webSocketDebuggerUrl"):
                        browser = _connect_cdp(self._pw, base)
                        ctx = (browser.contexts[0] if browser.contexts
                               else browser.new_context())
                        self._cdp = {"base": base}
                        log.info("Edge CDP 已就绪: %s", base)
                        return ctx
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                time.sleep(1.0)
            raise LiepinLoginError(
                f"浏览器 CDP 桥未在 {int(_CDP_STARTUP_TIMEOUT)}s 内就绪。{last_err}")
        except Exception:
            # 兜底清理：失败也绝不残留端口监听者/relay
            for p in (relay, edge):
                if p is not None and p.poll() is None:
                    try:
                        p.terminate()
                    except Exception:
                        pass
            _windows_kill_listener(_CDP_PORT)
            _windows_kill_listener(_RELAY_PORT)
            raise

    def _launch_native(self):
        """Windows 原生启动（os.name=='nt'，spec §4.4）：playwright 在本机直接拉起。

        默认 channel=chromium → 随包分发的 playwright chromium（静默语义与 WSL 原版
        一致：headless=self.headless）；chrome/msedge → playwright 原生 channel=
        自动探测本机安装（Windows 上无需跨盘路径桥）；显式 executable 优先。
        探测不到/启动失败 → LiepinLoginError 清晰引导，**绝不静默换引擎**。
        每次失败退避节奏与既有 WSL bundled 分支一致（约 5s × 4 次）。
        """
        last: Exception | None = None
        for _attempt in range(4):
            try:
                # 调用与既有 bundled 启动段同源（kwargs 见 _native_launch_kwargs），
                # 只改数据目录来源为 str(user_data_dir())，其余 WSL 互操作逻辑不涉及。
                kwargs = self._native_launch_kwargs()
                return self._pw.chromium.launch_persistent_context(**kwargs)
            except LiepinLoginError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(5)
        raise LiepinLoginError(
            "浏览器启动失败（Windows 原生）："
            f"{type(last).__name__}: {last}\n"
            "排查建议：① 默认用随包分发的 Chromium，无需本机安装；"
            "② 通道选 Chrome 请确认已安装（或改回默认 Chromium）；"
            "③ data\\browser_profile 未被其它进程占用；"
            "④ 填了代理请确认代理已启动。")

    def _native_launch_kwargs(self) -> dict:
        """原生启动参数。与既有 bundled 分支同源，逐项核对既有 kwargs 后保留差异项：
        必含 user_data_dir=str(user_data_dir())、headless=self.headless；
        分支：显式 executable → executable_path=（不存在 → LiepinLoginError）；
        channel in ("chrome", "msedge") → channel=channel（playwright 自探测）；
        代理 → proxy={"server": ...}；既有分支里其它 args 照抄。
        """
        kwargs: dict = {"user_data_dir": str(user_data_dir()),
                        "headless": self.headless,
                        "args": ["--disable-blink-features=AutomationControlled"]}
        exe = (getattr(self, "executable", None) or "").strip()
        channel = (getattr(self, "channel", None) or "chromium").strip().lower()
        if exe:
            if not Path(exe).is_file():
                raise LiepinLoginError(f"浏览器可执行文件不存在: {exe}")
            kwargs["executable_path"] = exe
        elif channel in ("chrome", "msedge"):
            kwargs["channel"] = channel
        if self.proxy:
            kwargs["proxy"] = {"server": self.proxy}
        return kwargs

    def _attach_native(self):
        """原生 attach（spec §4.4/§4.5）：复用『打开浏览器登录』专用实例——同一 CDP
        基址与已登录 profile，页面以标签页开在专用窗口里（用户可见、可关）。

        绝不新起浏览器实例；实例未开/已手动关闭 → 明确报错引导先点「打开浏览器登录」；
        绝不清除/注入 cookie、绝不关闭该浏览器。收尾赋值照既有 attach 分支
        （self._ctx/self._page/self._cdp 键名一致），调用方无感知。
        """
        from . import login_browser
        base = login_browser.cdp_base_url()
        if not base:
            raise LiepinLoginError(
                "登录浏览器没在运行。请先在设置页点『打开浏览器登录』并手动完成猎聘登录，"
                "再开启『在登录的浏览器里开着页面操作』测试；或关掉开关用静默模式（默认）。")
        try:
            browser = self._pw.chromium.connect_over_cdp(base, timeout=10_000)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        except Exception as e:  # noqa: BLE001
            raise LiepinLoginError(
                f"连接登录浏览器失败: {type(e).__name__}: {e}\n"
                "登录浏览器可能已被手动关闭——重新点『打开浏览器登录』即可。")
        # 收尾赋值与既有 attach 分支一致（返回 ctx 供 __init__ 赋给 self._ctx）
        self._ctx = ctx
        self._page = None
        self._cdp = {"base": base, "attach": True, "native": True}
        return ctx

    def _release_lock(self):
        if self._lock_held:
            _LIEPIN_LOCK.release()
            self._lock_held = False

    # ---------- 登录态注入与验证 ----------
    def _inject_cookies(self):
        """先清空再注入粘贴的 cookie，保证粘贴的会话权威。"""
        if self.attach:
            # 附件模式登录态 = 常驻 Chrome profile 原生登录，绝不清不注入——
            # clear_cookies() 会杀掉用户在真实浏览器里的登录会话。
            return
        self._ctx.clear_cookies()
        self._ctx.add_cookies(self._cookies)

    def validate(self) -> dict:
        """打开企业端门户，判定登录态 / 风控。返回 {ok, message, url}。"""
        self._inject_cookies()
        portal = self.codes["portal"]["url"]
        try:
            self._page.goto(portal, timeout=60000, wait_until="load")
        except Exception as e:
            # 导航被拦截或超时也归入风控/网络异常，不让后续继续
            raise LiepinRiskError(f"打开猎聘门户失败: {type(e).__name__}: {e}") from e
        url = self._page.url
        markers = self.codes["_risk"]["url_markers"]
        login_hit = any(m in url for m in markers["login"])
        if any(m in url for m in markers["risk"]):
            raise LiepinRiskError(
                f"触发了平台验证/风控页（{url}）。请在本机真实浏览器手动完成验证后，"
                "重新登录并粘贴新 Cookie。")
        body = ""
        try:
            body = self._page.content()
        except Exception:
            pass
        bm = self.codes["_risk"]["body_markers"]
        if any(m in body for m in bm["risk"]):
            raise LiepinRiskError("页面出现风控提示（滑块/行为异常）。请本机真实浏览器验证后重粘 Cookie。")
        if login_hit:
            # 门户 UI 跳登录页 ≠ 会话失效：lpt 门户注入 secscan/fp 客户端风控 JS，
            # 对无痕/headless 浏览器会按指纹把页面导到 /login，但同一会话在 BFF
            # 接口层（probe_bff）完全存活（实测 raw httpx 直连返回 flag=1 全量数据）。
            # 因此这里只标记不判死，由 probe_bff 以接口返回码（-1701 等）作最终裁决。
            return {"ok": True,
                    "message": "会话有效（门户 UI 被客户端风控引导至登录页，以接口层探测为准）",
                    "url": url, "portal_login_redirect": True}
        return {"ok": True, "message": "会话有效，已登录企业版门户", "url": url}

    def probe_bff(self) -> dict:
        """登录态深度探测：只读调用「可沟通岗位」BFF，验证会话在接口层真实存活。

        门户能打开 ≠ BFF 会话有效——账号在他处重登（-1701）或风控时门户页照常
        加载，只有 BFF 调用会返回失效码。测试连接必须在门户检查后加这一步，
        否则死会话会被误报「会话有效」。"""
        flow = self.codes.get("invite_flow", {})
        body = flow.get("chat_jobs_body", "curPage=0&pageSize=10&_keyword=")
        res = self.fetch_bff(self.codes["endpoints"]["chat_jobs"], body,
                             client_id=flow.get("client_id"))
        if not res.get("ok"):
            # 无副作用的探测失败不归入风控重试语义，按登录态失效引导重粘
            raise LiepinLoginError(res.get("error") or "BFF 会话探测调用失败")
        self._check_bff_risk(res["data"])
        return {"ok": True}

    # ---------- BFF 调用（双层：in-page fetch → httpx 兜底） ----------
    def _xsrf_token(self) -> str:
        for c in self._ctx.cookies():
            if c["name"].lower().startswith("xsrf"):
                return c["value"]
        return ""

    def _bff_headers(self, client_id: str | None = None) -> dict:
        """请求头与 liepin-cli lptFetch 逐项一致：表单编码 + x-fscp 族（codes.json headers）。

        client_id 非空时覆写 x-fscp-std-info——IM 系列端点固定传 client_id=40342
        （codes.json invite_flow.client_id），区别于默认 40156。
        """
        h = self.codes["headers"]
        headers = {
            "Accept": h["accept"],
            "Content-Type": h["content_type"],
            "x-client-type": h["x_client_type"],
            "x-requested-with": h["x_requested_with"],
            "x-fscp-version": h["x_fscp_version"],
            "x-fscp-std-info": (json.dumps({"client_id": client_id})
                                if client_id else h["x_fscp_std_info"]),
            "x-fscp-fe-version": h["x_fscp_fe_version"],
            "x-fscp-trace-id": str(uuid4()),
            "x-fscp-bi-stat": h["x_fscp_bi_stat"],
            "origin": h["origin"],
            "referer": h["referer"],
        }
        token = self._xsrf_token()
        if token:
            headers["x-xsrf-token"] = token
        return headers

    def _form(self, fields: dict) -> str:
        """dict → url-encoded 表单串；值中的 dict/list 自动 JSON 序列化（LPT BFF 契约）。"""
        parts = []
        for k, v in fields.items():
            if isinstance(v, (dict, list, tuple)):
                v = json.dumps(v, ensure_ascii=False)
            parts.append(f"{quote(str(k))}={quote(str(v))}")
        return "&".join(parts)

    def fetch_bff(self, path: str, body: str, client_id: str | None = None) -> dict:
        """调用猎聘 BFF API（表单编码，原始 body 串）。返回 {ok, data, httpStatus, cors_blocked, error}。

        契约对照 liepin-cli（2026-09 校准）：所有 LPT BFF 都是
        application/x-www-form-urlencoded；页面内 fetch 带真实 cookie/XSRF，
        CORS 拦截时降级 httpx 带 Cookie 头直连。HTML/非 JSON 响应直接判失败
        （可能是反爬挑战或登录页），不吞错。client_id 非空时覆写
        x-fscp-std-info（IM 系列端点用 40342）。
        """
        headers = self._bff_headers(client_id)
        js = """async (args) => {
            try {
                const resp = await fetch(args.path, {
                    method: 'POST',
                    credentials: 'include',
                    headers: JSON.parse(args.headers),
                    body: args.body,
                });
                let text = '';
                try { text = await resp.text(); } catch (e) {}
                return { httpStatus: resp.status, body: text };
            } catch (e) {
                // CORS 拦截/网络错误：fetch 抛 TypeError('Failed to fetch')
                return { cors_blocked: true };
            }
        }"""
        try:
            out = self._page.evaluate(js, {
                "path": path,
                "headers": json.dumps(headers),
                "body": body,
            })
        except Exception as e:
            return {"ok": False, "error": f"页面内调用 BFF 失败: {type(e).__name__}: {e}",
                    "cors_blocked": True}
        if out.get("cors_blocked"):
            return self._fetch_bff_httpx(path, body, headers)
        raw = out.get("body") or ""
        if raw.lstrip().startswith("<"):
            return {"ok": False, "error": "BFF 返回了 HTML（疑似反爬挑战/登录页）。"
                                          "请在本机真实浏览器完成验证后重新粘贴 Cookie。"}
        try:
            data = json.loads(raw)
        except Exception:
            return {"ok": False, "error": f"BFF 返回非 JSON: {raw[:200]}"}
        return {"ok": True, "data": data, "httpStatus": out.get("httpStatus"),
                "cors_blocked": False}

    def _fetch_bff_httpx(self, path: str, body: str, headers: dict) -> dict:
        """降级：Python httpx 带 Cookie 头直连（浏览器 fetch 被 CORS 拦时）。"""
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in self._ctx.cookies())
        h = dict(headers)
        if cookie_header:
            h["cookie"] = cookie_header
        try:
            kwargs = {"timeout": 30}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            resp = httpx.post(path, content=body, headers=h, **kwargs)
            try:
                data = resp.json()
            except Exception:
                return {"ok": False, "error": f"httpx 兜底返回非 JSON: {resp.text[:200]}",
                        "httpStatus": resp.status_code, "cors_blocked": True}
            return {"ok": True, "data": data, "httpStatus": resp.status_code,
                    "cors_blocked": False, "fallback": True}
        except Exception as e:
            return {"ok": False, "error": f"httpx 兜底调用失败: {type(e).__name__}: {e}",
                    "cors_blocked": True}

    # ---------- 搜索 ----------
    def _check_bff_risk(self, data: dict):
        code = str(data.get("code", ""))
        if code in self.codes["risk_stop_codes"]:
            raise LiepinRiskError(
                f"搜索接口返回风控码 {code}。请本机真实浏览器验证后重新粘贴 Cookie。")
        flag = data.get("flag")
        msg = str(data.get("msg") or data.get("message") or "")
        if msg and any(m in msg for m in ("验证", "安全验证", "请完成验证", "操作频繁",
                                          "操作过于频繁", "访问异常", "存在风险",
                                          "风控", "行为异常", "captcha")):
            raise LiepinRiskError(f"BFF 风控提示: {msg}")
        if flag is not None and flag != 1:
            # 登录态失效契约（镜像 liepin-cli AUTH_EXPIRED_FLAGS 实测：-1401 未登录 /
            # -1701 会话失效；msg 兜底词与其 AUTH_EXPIRED_PATTERN 一致）。
            # 命中即整批快速失败并引导重登，不归入风控（风控语义是等/人工验证，重试加重）。
            if code in self.codes["auth_expired_codes"] or (
                    msg and any(m in msg for m in ("未登录", "登录已失效", "登录超时",
                                                   "重新登录", "请先登录", "会话失效",
                                                   "身份过期"))):
                raise LiepinLoginError(
                    f"猎聘登录态已失效（code={code} msg={msg or '(空)'}）。"
                    "请用企业账号在真实浏览器登录 lpt.liepin.com 后重新粘贴 Cookie。")
            # IM BFF account.checkin 安全门（code 103160306；Windows 原生验收 2026-09-05
            # 实证：每次新登录会话都会出现）。非风控（会话本身有效、重试无益）也非登录
            # 失效——解除动作 = 在登录浏览器窗口打开一次官方 IM 页让真实 SPA 完成 checkin。
            # 防呆：未知新码若 msg 指向同一 account.checkin / jump 网关 → 同映射，不落入兜底。
            if code in self.codes["account_gate_codes"] or (
                    "account/checkin" in msg or "account.checkin" in msg
                    or "jump.liepin.com/pc" in msg):
                raise LiepinRiskError(
                    f"猎聘账号需先完成一次 IM 消息页平台验证（BFF code={code} account.checkin）。"
                    "请在『打开浏览器登录』弹出的登录窗口里新开标签页打开 "
                    "https://lpt.liepin.com/chat/im ，等约 1-2 分钟页面自动完成验证后，"
                    "回来再点『立即导入』/『测试连接』。粘贴 Cookie 场景请从完成过验证的"
                    "官方会话重新导出。")
            raise LiepinRiskError(
                f"BFF 调用失败（契约/权限问题）: flag={flag} code={code} msg={msg or '(空)'}。"
                "未命中风控清单——对照 liepin-cli 源码校验 codes.json 中的契约。")

    def _template(self, key: str) -> dict:
        """codes.json 模板的深拷贝，剥掉 comment 说明键（它们不是请求字段，不能进 body）。"""
        t = json.loads(json.dumps(self.codes[key]))
        t.pop("comment", None)
        t.pop("_comment", None)
        return t

    def _translate_filters(self, vo: dict, filters: dict):
        """画像筛选（中文名）→ VO 码值。只翻译 liepin-cli 已验证的键，其余保持 VO 默认
        （未校准字段绝不塞进请求体——-1400 的教训）。companyKeys/用户状态等待单独校准。"""
        if not filters:
            return
        tables = self.codes["code_tables"]
        wy = filters.get("workyears")
        if wy:
            names = wy if isinstance(wy, list) else [wy]
            ranges = [tables["workyears"][n] for n in names
                      if isinstance(n, str) and n in tables["workyears"]]
            if ranges:
                lows = [int(r.split(",")[0]) for r in ranges]
                highs = [int(r.split(",")[1]) for r in ranges]
                vo["workyears"] = f"{min(lows)},{max(highs)}"
        edu = filters.get("eduLevels")
        if edu:
            names = edu if isinstance(edu, list) else [edu]
            codes: list[str] = []
            for n in names:
                for c in tables["degree_ladder"].get(n, []):
                    if c not in codes:
                        codes.append(c)
            if codes:
                vo["eduLevels"] = codes
        for key in ("dqs", "wantDqs"):
            val = filters.get(key)
            if val:
                names = val if isinstance(val, list) else [val]
                codes = [tables["city"][n] for n in names if n in tables["city"]]
                if codes:
                    vo[key] = ",".join(codes)
        age = filters.get("age")
        if age:
            vo["age"] = str(age)

    def search(self, query) -> list[dict]:
        """执行一次 BFF 搜索。真实契约（liepin-cli search.js 校准）：表单两字段
        cvSearchConditionInputVo / logForm，各自是 JSON 串；keys 关键词，curPage 0 基。
        返回 (cards, total, raw)。成功判定 flag == 1。"""
        vo = self._template("search_body_vo")
        vo["keys"] = query.keyword
        vo["curPage"] = (query.page or 1) - 1          # LPT 页码 0 基
        self._translate_filters(vo, query.filters or {})
        body = self._form({
            "cvSearchConditionInputVo": vo,
            "logForm": {"skId": "", "fkId": "", "ckId": str(uuid4()),
                        "searchScene": "button"},
        })
        res = self.fetch_bff(self.codes["endpoints"]["search"], body)
        if not res.get("ok"):
            raise LiepinRiskError(res.get("error") or "BFF 调用失败")
        self._check_bff_risk(res["data"])
        items, total = self._extract_cards(res["data"])
        return items, total, res["data"]

    # ---------- 简历详情（M4） ----------
    def fetch_resume(self, resume_id: str, user_id: str = "") -> dict | None:
        """抓取一份简历详情。返回 {text, 结构化字段…}，None 表示未取到内容。

        BFF 响应形状不确定：text 由全量字段防御性拼装（已脱敏），结构化字段
        按 codes.json resume_fields 路径取，供前端展示。
        """
        body = self._form({"pageParamVo": {
            "resIdEncode": resume_id,
            "sfrom": self.codes["resume_body"]["sfrom"],
        }})
        res = self.fetch_bff(self.codes["endpoints"]["resume"], body)
        if not res.get("ok"):
            raise LiepinRiskError(res.get("error") or "简历接口调用失败")
        self._check_bff_risk(res["data"])
        return self._extract_resume(res["data"])

    def _extract_resume(self, data: dict) -> dict | None:
        payload = data.get("data")
        if not isinstance(payload, dict):
            payload = data
        vo = payload.get("resumeDetailVo") if isinstance(payload, dict) else None
        if isinstance(vo, dict):
            payload = vo            # resume_fields 路径相对 resumeDetailVo（codes.json 校准）
            # 契约探测：真实抓取后据此把项目经历等真实键回填到 codes.json resume_fields
            log.info("resumeDetailVo 顶层键: %s", sorted(payload.keys()))
        text = _resume_to_text(payload)
        if not text.strip():
            return None
        struct = {}
        for field, paths in self.codes.get("resume_fields", {}).items():
            val = self._pick(payload, paths)
            if val is not None:
                struct[field] = _sanitize(val)
        return {"text": text, **struct}

    # ---------- 岗位与邀请（M6） ----------
    def list_jobs(self) -> list[dict]:
        """拉取「可发起沟通」的职位列表（get-job-chat-list，IM 契约，client_id=40342）。

        与 jobmanage 岗位列表不同：只有带沟通次数的在招职位才会出现，且带 jobKind
        （check-chat-privlege 需要）。返回 [{ejobId, title, jobKind}]。
        """
        flow = self.codes.get("invite_flow", {})
        body = flow.get("chat_jobs_body", "curPage=0&pageSize=10&_keyword=")
        res = self.fetch_bff(self.codes["endpoints"]["chat_jobs"], body,
                             client_id=flow.get("client_id"))
        if not res.get("ok"):
            raise LiepinRiskError(res.get("error") or "可沟通岗位接口调用失败")
        self._check_bff_risk(res["data"])
        payload = res["data"].get("data")
        items = payload.get("list") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []
        jf = flow.get("job_fields", {})
        jobs = []
        for it in items:
            if not isinstance(it, dict):
                continue
            ejob_id = self._pick(it, jf.get("ejob_id", [["ejobId"]]))
            title = self._pick(it, jf.get("title", [["jobTitle"]]))
            jobkind = self._pick(it, jf.get("jobkind", [["jobKind"]]))
            if ejob_id:
                jobs.append({"ejobId": str(ejob_id), "title": str(title or ""),
                             "jobKind": str(jobkind) if jobkind is not None else None})
        return jobs

    def send_invite(self, user_id: str, job_id: str) -> dict:
        """向候选人发起沟通（真实链路，逐行对照 liepin-cli greet.js）：

        ① has-chat 幂等查会话——已有会话直接返回，绝不重复打招呼；
        ② get-job-chat-list 定位岗位与 jobKind（不在可沟通列表 → 报错，不发）；
        ③ check-chat-privlege 权限码必须为 can_chat（无沟通次数/不可沟通 → 报错，不发）；
        ④ to-chat2 发起沟通（flag==1 才算成功，自动使用职位预设招呼语）。

        任一步失败即抛 LiepinRiskError / LiepinLoginError，绝不静默发送。
        返回 {ok, already_chatted?} / {ok, opened_new_chat, job_title}。
        """
        flow = self.codes.get("invite_flow", {})
        client_id = flow.get("client_id")
        eps = self.codes["endpoints"]

        def _call(name: str, body: str, ctx: str) -> dict:
            res = self.fetch_bff(eps[name], body, client_id=client_id)
            if not res.get("ok"):
                raise LiepinRiskError(res.get("error") or f"{ctx}接口调用失败")
            self._check_bff_risk(res["data"])
            return res["data"]

        # ① 幂等：已有会话不重复发起（data === true，greet.js 契约）
        d = _call("has_chat", f"encodeOppositeUserId={quote(str(user_id))}", "会话检查")
        if d.get("data") is True:
            return {"ok": True, "already_chatted": True}

        # ② 在可沟通岗位列表里定位目标岗位，取 jobKind（缺省按契约兜底 "2"）
        d = _call("chat_jobs", flow.get("chat_jobs_body", "curPage=0&pageSize=10&_keyword="),
                  "可沟通岗位")
        payload = d.get("data")
        items = payload.get("list") if isinstance(payload, dict) else []
        items = items if isinstance(items, list) else []
        jf = flow.get("job_fields", {})
        target = None
        for it in items:
            if not isinstance(it, dict):
                continue
            if str(self._pick(it, jf.get("ejob_id", [["ejobId"]])) or "") == str(job_id):
                target = it
                break
        if target is None:
            raise LiepinRiskError(
                "岗位不在可沟通岗位列表（可能是职位无沟通次数或已下线），请重新拉取岗位后选择")
        job_title = self._pick(target, jf.get("title", [["jobTitle"]])) or ""
        jobkind = self._pick(target, jf.get("jobkind", [["jobKind"]])) or flow.get("jobkind_default", "2")

        # ③ 权限检查：code 存在且非 can_chat → 不可沟通（如沟通次数已用完）
        d = _call("check_chat_priv",
                  self._form({
                      "oppositeUserId": user_id,
                      "enumLpScene": "b_others",
                      "jobId": str(job_id),
                      "jobkind": str(jobkind),
                  }), "沟通权限检查")
        code = self._pick(d, flow.get("priv_fields", {}).get(
            "result_code", [["data", "chatCheckResultCode"]]))
        if code is not None and str(code) != flow.get("ok_code", "can_chat"):
            raise LiepinRiskError(
                f"无法打招呼（chatCheckResultCode={code}）：该职位可能已无沟通次数或候选人不可沟通")

        # ④ 发起沟通 = 自动发送职位预设招呼语（body 逐字符对齐 greet.js）
        body = (f"usercIdEncode={quote(str(user_id))}&ejobId={quote(str(job_id))}"
                "&imSign=&source=R_SEARCH_CONDITION&ext=%7B%7D")
        d = _call("invite", body, "发起沟通")
        return {"ok": True, "opened_new_chat": True, "job_title": str(job_title),
                "jobkind": str(jobkind)}

    def _extract_cards(self, data: dict) -> tuple[list[dict], int]:
        """从搜索 BFF 响应抽 {items, total}。真实形状（liepin-cli search.js 校准）：
        data.cvSearchResultForm.cvSearchListFormList。"""
        payload = data.get("data")
        items: list = []
        total = 0
        if isinstance(payload, dict):
            form = payload.get("cvSearchResultForm")
            if isinstance(form, dict):
                items = form.get("cvSearchListFormList") or []
                total = form.get("totalCount") or form.get("totalNum") or len(items)
            else:
                items = payload.get("data") or payload.get("list") or []
                total = payload.get("total", len(items))
        elif isinstance(payload, list):
            items = payload
            total = len(items)
        if not isinstance(items, list):
            items = []
        fields = self.codes["card_fields"]
        cards = []
        for it in items:
            if not isinstance(it, dict):
                continue
            card = {}
            for field, paths in fields.items():
                if field == "comment":
                    continue
                card[field] = self._pick(it, paths)
            # 规范化
            if card.get("age") is not None:
                m = re.search(r"\d+", str(card["age"]))
                try:
                    card["age"] = int(m.group(0)) if m else None
                except (TypeError, ValueError):
                    card["age"] = None
            if card.get("resume_url") and not str(card["resume_url"]).startswith("http"):
                card["resume_url"] = "https://lpt.liepin.com" + str(card["resume_url"])
            if not card.get("resume_id"):
                continue          # 无唯一键不入库（db.upsert_candidate 按 resume_id 去重）
            cards.append(card)
        return cards, total

    @staticmethod
    def _pick(item: dict, paths: list[list[str]]):
        for path in paths:
            v = item
            for key in path:
                if isinstance(v, dict) and key in v:
                    v = v[key]
                elif isinstance(v, list) and str(key).isdigit():
                    idx = int(key)
                    v = v[idx] if idx < len(v) else None
                else:
                    v = None
                    break
            if v is not None and v != "" and v != []:
                return v
        return None

    def close(self):
        # _release_lock 必须无条件执行：ctx.close/pw.stop/进程清理抛错也不能留下死锁
        try:
            if self.attach and self._ctx:
                # 附件模式：目标 Chrome 归用户所有（登录态载体），这里只回收自己
                # 开的那一个 tab 和自己拉起的 relay——
                # · 绝不 close self._ctx：CDP 接入的默认 context 由浏览器所有，
                #   close 它可能直接关掉用户整个浏览器；
                # · 绝不 kill 目标调试端口（_cdp["target_port"]）：那是用户的 Chrome；
                # · 9338 是本应用私有 relay 端口，只清它。
                try:
                    if self._page is not None:
                        self._page.close()
                except Exception:
                    pass
                try:
                    if self._pw:
                        self._pw.stop()
                except Exception:
                    pass
                self._ctx = None
                self._page = None
                if self._cdp:
                    # 原生 attach（native=True）：登录浏览器归 login_browser 模块回收
                    # （lifespan 精确 PID），这里只清状态，绝不动其端口/进程。
                    if not self._cdp.get("native"):
                        _windows_kill_listener(_RELAY_PORT)
                    self._cdp = None
                return
            if self._ctx:
                try:
                    if self._cdp:
                        # CDP 接入的原始 context 不归 playwright 所有，close 抛错属
                        # 预期；实际关停交给下方按端口的进程级清理（精确 PID，绝不
                        # /IM 整类浏览器——会误杀用户真实 Edge）
                        try:
                            self._ctx.close()
                        except Exception:
                            pass
                    else:
                        self._ctx.close()
                finally:
                    if self._pw:
                        self._pw.stop()
                    self._ctx = None
            if self._cdp:
                _windows_kill_listener(_CDP_PORT)
                _windows_kill_listener(_RELAY_PORT)
                self._cdp = None
        finally:
            self._release_lock()


# ---------- 串行队列 ----------
class LiepinQueue:
    """串行限速队列。每次操作前查缓存（断点续跑）；stop 标志逐条检查；整批 deadline。"""

    def __init__(self, session: LiepinSession, delays: dict | None = None,
                 deadline_seconds: float = 3600, stop: threading.Event | None = None):
        self.session = session
        self.delays = delays or {"search": 5, "resume": 15, "greet": 45}
        self.deadline_seconds = deadline_seconds
        self.stop = stop or threading.Event()

    def _pause(self, op: str):
        if self.stop.is_set():
            raise LiepinStopRequested()
        time.sleep(self.delays.get(op, 5))

    def _check_deadline(self, started: float, done: int, total: int, op: str):
        if self.deadline_seconds and time.time() - started > self.deadline_seconds:
            raise TimeoutError(
                f"猎聘操作超时（已运行 {int(time.time() - started)}s，超过 {int(self.deadline_seconds)}s 上限），"
                f"已完成 {done}/{total} 条。已命中的已缓存，可稍后断点续跑。")

    def run_searches(self, queries: list, progress_cb=None) -> list[dict]:
        """逐条搜索并落库。返回 [{query_label, keyword, total, new}]。"""
        out = []
        started = time.time()
        for i, q in enumerate(queries, 1):
            self._check_deadline(started, i, len(queries), "search")
            self._pause("search")
            cache_key = f"search:{q.keyword}:{json.dumps(q.filters or {}, ensure_ascii=False)}"
            cached = db.cache_get(cache_key)
            if cached is not None:
                cards = json.loads(cached)
                out.append({"label": q.label, "keyword": q.keyword, "total": len(cards),
                            "cached": True, "cards": cards})
                if progress_cb:
                    progress_cb(i, len(queries), out[-1])
                continue
            cards, total, _raw = self.session.search(q)
            # 空结果不落缓存：0 命中可能是解析/契约回归的假象（曾出现全表 '[]' 毒缓存
            # 导致后续所有 run 重放空结果、永不打真实搜索）。空查询每次重跑代价可控。
            if cards:
                db.cache_put(cache_key, json.dumps(cards, ensure_ascii=False))
            out.append({"label": q.label, "keyword": q.keyword, "total": total,
                        "cached": False, "cards": cards})
            if progress_cb:
                progress_cb(i, len(queries), out[-1])
        return out

    def fetch_resumes(self, cards: list[dict], progress_cb=None) -> list[dict]:
        """批量抓取简历详情（限速、缓存、stop、deadline，上限 max_resume_fetch_per_run）。
        返回与入参卡片一一对应的列表，追加 {resume, cached, error?}。
        """
        limit = self.session.codes["limits"].get("max_resume_fetch_per_run", 30)
        cards = cards[:limit]
        out: list[dict] = []
        started = time.time()
        for i, card in enumerate(cards, 1):
            self._check_deadline(started, i, len(cards), "resume")
            rid = card.get("resume_id") or card.get("id")
            cache_key = f"resume:{rid}"
            cached = db.cache_get(cache_key)
            if cached is not None:
                out.append({**card, "resume": json.loads(cached), "cached": True})
                if progress_cb:
                    progress_cb(i, len(cards), out[-1])
                continue
            self._pause("resume")   # 缓存未命中才限速（命中=断点续跑，秒级回放）
            try:
                resume = self.session.fetch_resume(rid, card.get("user_id", ""))
                if resume is None:
                    out.append({**card, "resume": None, "error": "未取到简历内容"})
                else:
                    db.cache_put(cache_key, json.dumps(resume, ensure_ascii=False))
                    out.append({**card, "resume": resume, "cached": False})
            except (LiepinRiskError, LiepinLoginError):
                raise
            except Exception as e:  # noqa: BLE001 —— 单条失败不拖垮整批
                out.append({**card, "resume": None, "error": f"{type(e).__name__}: {e}"})
            if progress_cb:
                progress_cb(i, len(cards), out[-1])
        return out

    def run(self, queries):
        return self.run_searches(queries)


# ---------- 专用线程 ----------
def _run_in_dedicated_thread(fn, *args, **kwargs):
    """在全新专用线程执行 fn，收集返回值/异常原样重抛。

    为什么必须：playwright sync API greenlet 绑定启动线程，stop 后再 start 残留
    "Event loop is closed" contextvars——FastAPI 线程池复用线程会把上次的 contextvars
    带进下次请求，第二次 run 就报错。每次 run 用专属线程，天然免疫（BOM-AI 已验证）。"""
    result = {}

    def target():
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 —— 原样传回
            result["error"] = e

    t = threading.Thread(target=target, daemon=True, name="liepin-batch")
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


class LiepinRunner:
    """延迟到专用线程执行整个猎聘批次。构造时不做任何 playwright 操作。"""

    def __init__(self, cookie_str: str, cookie_type: str, codes: dict | None = None,
                 headless: bool = True, channel: str = "chrome", delays: dict | None = None,
                 proxy: str = "", executable: str = "", attach: bool = False):
        self.cookie_str, self.cookie_type = cookie_str, cookie_type
        self.codes, self.headless, self.channel = codes, headless, channel
        self.delays = delays or {}
        self.proxy, self.executable = proxy, executable
        self.attach = attach

    def run_searches(self, queries, stop: threading.Event | None = None,
                     progress_cb=None) -> list[dict]:
        def _batch():
            # validate() 抛错（登录态失效/风控）也必须走 close：否则 _LIEPIN_LOCK
            # 永不释放 + 浏览器成孤儿 → 之后所有搜索/导入永久卡死（曾实测复现）
            session: LiepinSession | None = None
            try:
                session = LiepinSession(self.cookie_str, self.cookie_type, self.codes,
                                        self.headless, self.channel, self.delays,
                                        proxy=self.proxy, executable=self.executable,
                                        attach=self.attach)
                session.validate()
                q = LiepinQueue(session, delays=self.delays, stop=stop)
                return q.run_searches(queries, progress_cb=progress_cb)
            finally:
                if session is not None:
                    session.close()
        return _run_in_dedicated_thread(_batch)

    def run_full(self, queries, cards_provider, stop: threading.Event | None = None,
                 progress_cb=None, resume_progress_cb=None) -> list[dict]:
        """完整批次：同一会话内 搜索 → 候选收集 → 自动抓简历（一次 run 只开一个浏览器）。

        cards_provider(search_results) 是普通闭包（sqlite 写入 + matcher 纯计算，
        非 playwright 操作），返回待抓简历的卡片列表——run_service._worker 传入
        collect 闭包，run 完自动抓 quick_gate=1 的前 N 条。"""
        def _batch():
            # 同 run_searches：validate() 抛错也必须 close，防会话锁泄漏/浏览器孤儿
            session: LiepinSession | None = None
            try:
                session = LiepinSession(self.cookie_str, self.cookie_type, self.codes,
                                        self.headless, self.channel, self.delays,
                                        proxy=self.proxy, executable=self.executable,
                                        attach=self.attach)
                session.validate()
                q = LiepinQueue(session, delays=self.delays, stop=stop)
                results = q.run_searches(queries, progress_cb=progress_cb)
                fetch_cards = cards_provider(results)
                if fetch_cards:
                    q.fetch_resumes(fetch_cards, progress_cb=resume_progress_cb)
                return results
            finally:
                if session is not None:
                    session.close()
        return _run_in_dedicated_thread(_batch)


def validate_cookie_settings(cookie_str: str, cookie_type: str, codes: dict | None = None,
                             headless: bool = True, channel: str = "chrome",
                             use_existing: bool = False, proxy: str = "",
                             executable: str = "", attach: bool = False) -> dict:
    """设置页『测试连接』：专用线程里建会话并验证。返回 {ok, message}。

    attach=True 时复用 liepin login 常驻 Chrome（cookie_str 传空串即可，登录态
    在浏览器 profile 里）；原 cookie 粘贴通道（use_existing/直接粘贴）保持可用。"""
    if use_existing:
        # 复用已存 cookie（加密解密由调用方处理成明文）
        pass

    def _validate():
        session = LiepinSession(cookie_str, cookie_type, codes, headless, channel,
                                proxy=proxy, executable=executable, attach=attach)
        try:
            r = session.validate()
            # 门户检查通过 ≠ 会话可用：再发一次只读 BFF 探测，-1701/未登录/风控
            # 在此被捕获，测试结果与真实搜索/邀请一致。门户 UI 被客户端风控导到
            # 登录页时同样以 BFF 返回码为准（UI 跳转是 JS 指纹判断，非会话死亡）。
            session.probe_bff()
            note = ("门户 UI 被客户端风控引导至登录页（接口层会话有效）"
                    if r.get("portal_login_redirect")
                    else "门户与 BFF 会话探测均通过")
            return {"ok": True,
                    "message": f"会话有效：{note}，可发起搜索/邀请",
                    "url": r["url"]}
        finally:
            session.close()
    try:
        return _run_in_dedicated_thread(_validate)
    except LiepinLoginError as e:
        return {"ok": False, "error": str(e)}
    except LiepinRiskError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
