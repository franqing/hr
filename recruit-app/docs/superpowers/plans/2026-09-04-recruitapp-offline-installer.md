# 单机版 A 方案（Windows 原生 + Inno Setup 全离线安装包）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前 WSL2 版招聘系统改造成「公司普通 Win10/11 可直接安装运行的 Windows 原生单机版」：`RECRUIT_DATA_DIR` 数据路径解耦 + 静态托管/`/api` 重写 + 登录浏览器原生分支（「打开浏览器登录」自动导入）+ 打包为 Inno Setup 全离线安装包。

**Architecture:** 分五层渐进落地：(0) WSL 回归基线快照；(1) 平台层改造——`backend/paths.py` 把全部数据落点改为 call-time 解析（env 覆盖），`main.py` 加静态托管 + `/api` 前缀重写中间件，`adapter.py`/`cookie_import.py` 加 `os.name == "nt"` 原生分支；(2) 设置页「打开浏览器登录」主路径（可见窗口专用登录浏览器 + 15s mtime 轮询 + Python CDP 导出 → 指纹比对 → 门户+BFF 双验证 → Fernet 落库）；(3) Windows 原生直启验收；(4) 打包（便携 Python + 离线依赖 + chromium-win + dist → Inno Setup）。WSL 版全部互操作代码原样保留作回归基线，在 `os.name == "nt"` 下**永不进入执行**。

**Tech Stack:** Python 3.12 + FastAPI/uvicorn + playwright 1.62 + sqlite（Fernet 密钥）· Vue3 + Element Plus（零改动 + 设置页文案分支）· Inno Setup 6（安装包）· WSL2 dev / Windows 目标双环境。

## Global Constraints

每条都是 spec（commit 4059678，`docs/superpowers/specs/2026-09-04-recruitapp-offline-installer-design.md`）逐字或用户定稿约束，**每个任务的要求都隐式包含本节**：

- 目标电脑是**普通 Win10/11，无 WSL2、无开发环境**，可能无法访问外网依赖源。
- **不做集中服务器方案**：每台电脑各自装一套单机版，数据与密钥各自本机（Fernet key 每机独立生成）。
- **打包方式 = 方案 1 全离线安装包（Inno Setup）**，双击安装即用；安装到 `%LOCALAPPDATA%\RecruitApp`（app\ + data\ 分离；升级先把旧 app\ 改名 app.old 再替换；卸载不删除 data\）。
- 登录由用户手动完成：设置页点「打开浏览器登录」→ 弹出专用登录浏览器（**独立 profile `login-profile`，绝不碰日常浏览器**）→ 用户输入账号密码（**绝不自动登录**，只打开登录页）→ 系统约 20 秒内自动导入会话；粘贴 Cookie 作为兜底。
- 自动导入 = 沿用 WSL 轮询语义（每 15s stat Cookie 库 mtime）→ 变化时 **Python 侧 CDP 导出**（playwright `connect_over_cdp` → liepin.com 域 cookie → Cookie 头）→ 登录态指纹比对 → 门户 + BFF 双验证 → Fernet 加密落库。**零 node 依赖**（node mjs 导出助手仅 WSL 版使用，原生路径不调用）。
- **平台分支机械判定**：按 `os.name == "nt"` 硬判断——Windows 下恒为真，**不做任何启发式探测**；WSL 互操作代码（CDP 桥、PowerShell relay、pgrep/wslpath、netstat+taskkill 等）在 `os.name == "nt"` 下**永不进入执行**——同事电脑不需要、不依赖、不触发任何 WSL 组件。
- 登录浏览器专用实例由后端启动时记录 PID，**只回收自己拉起的实例**（精确 PID kill）；**绝不 taskkill /IM、绝不 kill 用户进程、绝不操作/关闭用户自行启动的浏览器**。
- attach 模式（原生）：复用「打开浏览器登录」专用实例——同一 CDP 基址与已登录 profile；实例未开 → 明确报错引导先点「打开浏览器登录」；**绝不清除/注入 cookie、绝不关闭登录浏览器**。
- 只 relay `{ok, message/error}` 键；**明文 cookie 绝不进日志/输出/UI**。
- 默认浏览器通道 = 随包分发的 Playwright bundled chromium（静默跑任务，与原版一致，同事没装 Chrome 也能用）；选 chrome/edge → 本机浏览器 + 同一专用 profile；**探测不到 → 清晰报错，不静默换引擎**。
- WSL 版保持可用是回归基线：CDP 桥、PowerShell relay、node mjs、liepin login 导入、attach 现实现、前端代码与依赖全部原样保留。
- 数据路径统一：`data_dir() = env RECRUIT_DATA_DIR → <程序根>/data`；**程序根 = `main.py` 所在目录的上一级**（WSL dev = recruit-app 仓库根；安装版 = `%LOCALAPPDATA%\RecruitApp\app`）；WSL 开发机不设 env → 落点与现状完全一致（**零回归**）。
- 前端零改动原则：`baseURL='/api'` 由后端中间件还原前缀；前端 history 路由（`createWebHistory`）→ SPA 未命中路径回 `index.html`。
- `GET /health` 复用为启动器健康探测，无新增端点；端口固定 8000，仅绑定 127.0.0.1；launcher 检测到占用 → 明确提示，不静默切端口。
- 会话/密钥数据只在本机；不新增任何外发通道；审计沿用现状。
- 5 个既有陈旧 `test_adapter.py` 失败是基线问题，**忽略**（Task 1 快照记录）；不触碰 `/mnt/d/AI应用` 游离未跟踪文件；git commit message 一律以 `Co-Authored-By: Claude <noreply@anthropic.com>` 结尾，git root = `/mnt/d/AI应用`。
- 仓库内代码行尾/编码：Python 文件 utf-8，沿用现有注释密度与语言（中文注释）。

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `docs/wsl-regression-baseline-2026-09-04.txt` | WSL 基线 pytest 快照（记录 5 个既有失败） | Task 1 新建 |
| `backend/paths.py` | 数据目录 call-time 解析：`program_root()` / `data_dir()` | Task 2 新建 |
| `backend/db.py` | `_DB_PATH` 常量 → `get_conn` 内 call-time `data_dir()` | Task 2 修改 |
| `backend/security.py` | `_KEY_PATH` 常量 → `get_fernet` 内 call-time | Task 2 修改 |
| `backend/profile_parser.py` | `_UPLOAD_DIR` → `upload_dir()` 函数，`save_upload` 用之 | Task 2 修改 |
| `backend/liepin/adapter.py` | `USER_DATA_DIR` 常量 → `user_data_dir()`（6 处使用点）；原生分支 `_launch_native` / `_attach_native`；`close()` native guard | Task 2 + 5 修改 |
| `tests/conftest.py` | `_DB_PATH` monkeypatch-setattr → autouse env fixture（防测试写真实 data/） | Task 2 修改 |
| `tests/test_paths.py` | 数据路径中心化回归测试 | Task 2 新建 |
| `backend/main.py` | 静态托管（SPA fallback）+ `/api` 重写中间件 + `platform_native` + `POST /settings/liepin/open-login` + lifespan 关停登录浏览器 | Task 3 + 4 修改 |
| `tests/test_static_hosting.py` | `_find_web_dir` 顺序 / rewrite / `platform_native` | Task 3 新建 |
| `backend/liepin/login_browser.py` | 「打开浏览器登录」：可见窗口拉起专用登录浏览器（53471→9222）、CDP 导出、build_cookie_header、cookie_lib_paths、PID 回收 | Task 4 新建 |
| `tests/test_login_browser.py` | `build_cookie_header` 纯函数 + profile 路径 | Task 4 新建 |
| `backend/cookie_import.py` | 原生分支：watch 路径改登录浏览器 profile、`_export_header` 走 Python CDP | Task 6 修改 |
| `tests/test_cookie_import.py` | 原生分支 dispatch 纯测 | Task 6 新建 |
| `frontend/src/api.js` | 加 `openLoginBrowser` 一行 | Task 7 修改 |
| `frontend/src/views/Settings.vue` | `isNative` 文案分支 + 「打开浏览器登录」按钮 | Task 7 修改 |
| `docs/windows-native-check-2026-09-04.md` | Windows 原生直启手工验收清单（spec §6 items 3/4） | Task 8 新建 |
| `packaging/run.bat` | 安装版启动器：注入 env → pythonw uvicorn → `>>` 日志；/health 幂等 | Task 9 新建 |
| `packaging/RecruitApp.launch.vbs` | 桌面启动器：/health 确认 → 开 `http://127.0.0.1:8000` | Task 9 新建 |
| `packaging/install.iss` | Inno Setup：PrepareToInstall 自回收 + app.old + data\ 保留 + 计划任务 | Task 9 新建 |
| `packaging/BUILD.md` | stage 物料组装命令 + 构建产物 | Task 9 新建 |
| `docs/usage-guide-standalone-2026-09-04.md` | 中文图文使用说明（安装/首次配置/登录导入/任务/FAQ） | Task 10 新建 |

---

### Task 1: WSL 回归基线快照

**Files:**
- Create: `docs/wsl-regression-baseline-2026-09-04.txt`

**Interfaces:**
- Consumes: 无
- Produces: 基线快照文件（Task 2~7 每步回归对比用：**无新增失败** = 通过标准）

- [ ] **Step 1: 确认仓库状态与 venv**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git log --oneline -3
.venv/bin/python --version        # 预期 3.12.x
```

- [ ] **Step 2: 跑全量 pytest 并把输出存为基线快照**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests -q 2>&1 | tee docs/wsl-regression-baseline-2026-09-04.txt | tail -5
```

预期：`X failed, Y passed`，其中失败**恰为 5 个陈旧 `test_adapter.py` 用例**（`liepin` 网络相关），与既往一致。若失败数 ≠ 5 → **停下报告**，不要继续（基线不对则后面全错）。`tests/` 下若混有非 pytest 文件，命令改为 `.venv/bin/python -m pytest tests/test_*.py -q`，以实际通过的收集范围为准并写进快照头部一行。

- [ ] **Step 3: 记录既有失败用例清单**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
grep -E "^(FAILED|tests/.*F)" docs/wsl-regression-baseline-2026-09-04.txt | head -20
```

若 tee 文件里无 FAILED 明细行（`-q` 模式只有 summary），补跑一次带明细的写入快照尾部：

```bash
.venv/bin/python -m pytest tests -q --tb=no -rf 2>&1 | grep "^FAILED" >> docs/wsl-regression-baseline-2026-09-04.txt
```

- [ ] **Step 4: Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add docs/wsl-regression-baseline-2026-09-04.txt
git commit -m "test: snapshot WSL regression baseline before native split (5 known stale adapter failures)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 数据路径中心化（`paths.py` + 全部消费者 + conftest 重写）

**Files:**
- Create: `backend/paths.py`
- Create: `tests/test_paths.py`
- Modify: `tests/conftest.py`（9 行整体重写）
- Modify: `backend/db.py`（第 6 行常量 + `get_conn`）
- Modify: `backend/security.py`（第 7 行常量 + `get_fernet`）
- Modify: `backend/profile_parser.py`（第 33 行常量 + `save_upload` 使用点）
- Modify: `backend/liepin/adapter.py`（第 30 行常量 + 6 处 `USER_DATA_DIR` 使用点）

**Interfaces:**
- Consumes: 无
- Produces:
  - `backend/paths.py`：`program_root() -> Path`；`data_dir() -> Path`（只算路径不建目录；env `RECRUIT_DATA_DIR` 覆盖；未设 → `<程序根>/data`）
  - `backend/profile_parser.py` 新增模块函数 `upload_dir() -> Path`（= `data_dir() / "uploads"`）
  - `backend/liepin/adapter.py` 新增模块函数 `user_data_dir() -> Path`（= `data_dir() / "browser_profile"`，lazy import `from ..paths import data_dir` 防循环）
  - Task 3~6 依赖 `data_dir()` / `user_data_dir()` 的存在与语义

- [ ] **Step 1: 先重写 conftest（防现有测试静默写真实 data/app.db）**

`tests/conftest.py` 现在 monkeypatch `db._DB_PATH`——在 db.py call-time 化后会失效，现有测试将静默写仓库真实 `data/app.db`。整体重写为 autouse env fixture（9 行全文替换）：

```python
"""测试夹具：把数据目录整体指向 pytest 临时目录，保证任何用例都不碰真实 data/。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"
```

用 Write 全量覆盖 `tests/conftest.py`（重写前后都是 9 行）。

- [ ] **Step 2: 新建 `backend/paths.py`**

```python
"""数据目录解析（单机版 A 方案 spec §4.2）：env RECRUIT_DATA_DIR → <程序根>/data。

程序根 = backend/ 的上一级：WSL 开发 = recruit-app 仓库根；安装版 =
%LOCALAPPDATA%\\RecruitApp\\app。全部数据落点（app.db / secret.key / uploads /
browser_profile / login-profile）都经本模块在**调用时**解析——安装版由 run.bat 注入
RECRUIT_DATA_DIR，WSL 开发不设 env → 落点与现状完全一致（零回归）。
"""
from __future__ import annotations

import os
from pathlib import Path

_PROGRAM_ROOT = Path(__file__).resolve().parent.parent


def program_root() -> Path:
    """程序根：backend/ 的上一级（仓库根或安装版 app 目录）。"""
    return _PROGRAM_ROOT


def data_dir() -> Path:
    """数据目录：env RECRUIT_DATA_DIR 覆盖，未设 → <程序根>/data。

    只计算路径、绝不 mkdir——建目录由各消费者（db/security/uploads/浏览器启动）自理。
    """
    env = os.environ.get("RECRUIT_DATA_DIR", "").strip()
    if env:
        return Path(env)
    return _PROGRAM_ROOT / "data"
```

- [ ] **Step 3: 写失败测试 `tests/test_paths.py`**

```python
"""数据路径中心化（spec §4.2）回归：env 覆盖后各消费者落点跟随；绝不写真实 data/。"""
from __future__ import annotations

from pathlib import Path

from backend.paths import data_dir, program_root
from backend import db, security
from backend.liepin import adapter


def test_program_root_is_repo_root():
    # 程序根 = backend/ 的上一级（开发机即仓库根）
    assert program_root() == Path(db.__file__).resolve().parent.parent


def test_data_dir_default_is_program_root_data():
    assert data_dir() == program_root() / "data"


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    assert data_dir() == tmp_path / "d"


def test_db_file_lands_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    conn = db.get_conn()
    conn.close()
    assert (tmp_path / "d" / "app.db").is_file()


def test_fernet_key_lands_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    key = security.get_fernet()
    assert key.encrypt(b"x") != b"x"
    assert (tmp_path / "d" / "secret.key").is_file()


def test_upload_dir_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    from backend import profile_parser
    assert profile_parser.upload_dir() == tmp_path / "d" / "uploads"


def test_user_data_dir_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    assert adapter.user_data_dir() == tmp_path / "d" / "browser_profile"
```

- [ ] **Step 4: 跑测试确认失败**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_paths.py -v 2>&1 | tail -20
```

预期：`ImportError`/`AttributeError`（paths 模块或 user_data_dir 不存在）→ FAIL。

- [ ] **Step 5: 改造 `backend/db.py`**

现第 6 行 `_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"`（该文件里唯一的 Path/数据路径使用点）：

(a) import 区改为（`json/sqlite3/datetime` 原样保留，删掉 `from pathlib import Path`，若文件别处没有其它 Path 用法；否则保留并核对）：

```python
import json
import sqlite3
from datetime import datetime

from .paths import data_dir
```

(b) 删除第 6 行 `_DB_PATH` 常量，`get_conn()` 函数体开头改为 call-time 解析（读一下 70-80 行原文，把 `_DB_PATH` 全部替换干净）：

```python
def get_conn() -> sqlite3.Connection:
    db_path = data_dir() / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn
```

（`conn.row_factory`/`executescript`/`_migrate` 等行若原来就在 `get_conn` 内则保留原样，只把「路径常量 → 函数内解析」这部分替换。）

- [ ] **Step 6: 改造 `backend/security.py`**

现第 7 行 `_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "secret.key"`：

(a) import 区删 Path（若仅此一处用）、加 `from .paths import data_dir`；
(b) 删 `_KEY_PATH` 常量；`get_fernet()` 开头改为函数内解析：

```python
def get_fernet() -> Fernet:
    """取（必要时生成）本机 Fernet 密钥。密钥只在 data_dir 下，每机独立。"""
    key_path = data_dir() / "secret.key"
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(Fernet.generate_key())
        try:
            key_path.chmod(0o600)
        except OSError:  # noqa: BLE001 —— Windows 无 POSIX 权限位，忽略
            pass
    return Fernet(key_path.read_bytes())
```

（保留文件原有其余逻辑与注释风格；若原文件已有 `_ensure_key` 等内部结构，把 `key_path` 计算放进实际读/写密钥的函数内即可，保证模块内不再残留 `_KEY_PATH` 常量引用。）

- [ ] **Step 7: 改造 `backend/profile_parser.py`**

现第 33 行 `_UPLOAD_DIR = Path(...) / "data" / "uploads"`，使用点在 `save_upload`（131-132 行：`_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)` 与 `dest = _UPLOAD_DIR / f"upload-{int(__import__('time').time() * 1000)}.pdf"`）：

(a) 常量行替换为模块函数：

```python
def upload_dir() -> Path:
    return data_dir() / "uploads"
```

(b) import 区加 `from .paths import data_dir`（若原文件没有 Path 的其它用途则删其 import）；
(c) `save_upload` 函数体内对应两行改为：

```python
    dest = upload_dir() / f"upload-{int(__import__('time').time() * 1000)}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
```

（改完 `grep -n "_UPLOAD_DIR" backend/profile_parser.py` 应为 0 命中。）

- [ ] **Step 8: 改造 `backend/liepin/adapter.py` 的 `USER_DATA_DIR`**

(a) 第 30 行常量替换为函数（adapter 在 `backend/liepin/` 下，到 backend 是 `..`；import 放函数内防循环）：

```python
def user_data_dir() -> Path:
    """浏览器 profile 数据目录（= data_dir()/browser_profile）。call-time 解析。"""
    from ..paths import data_dir
    return data_dir() / "browser_profile"
```

(b) 全文 6 处使用点 `USER_DATA_DIR` 全部改为 `user_data_dir()` 调用（已核实散布点：约 67 行 f-string、212 行 `is_dir`、215 行 glob、497 行 bundled kwargs、533 行 `_win_path(USER_DATA_DIR)` argv 等——执行时 `grep -n "USER_DATA_DIR" backend/liepin/adapter.py` 逐个核对替换，改完 grep 应只剩 `user_data_dir()` 调用点、无裸常量名）。每个调用点保持原调用形态（f-string 内 `{USER_DATA_DIR}` → `{user_data_dir()}`；`_win_path(USER_DATA_DIR)` → `_win_path(user_data_dir())`）。

- [ ] **Step 9: 跑新测试 + 相关既有测试确认通过**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_paths.py -q 2>&1 | tail -3
.venv/bin/python -m pytest tests -q --ignore=tests/test_adapter.py 2>&1 | tail -3
```

预期：新测试全 PASS；除 test_adapter.py 外的既有测试**无新增失败**。注意 conftest 已把全部用例数据目录指向 tmp——跑完确认仓库真实 `data/app.db` 的 mtime 未被测试改动：

```bash
ls -la data/app.db 2>/dev/null && stat -c '%y' data/app.db
```

（人工对比改动前的 mtime；若不便，对比 git 忽略文件的存在性与 Task 1 快照即可——重点是跑测试前后 data/ 无新建/变更。）

- [ ] **Step 10: 全量回归对比基线（无新增失败）**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

与 Task 1 快照比对：失败数**不得增加**（仍恰为 5 个陈旧 adapter 失败）。

- [ ] **Step 11: Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add backend/paths.py backend/db.py backend/security.py backend/profile_parser.py backend/liepin/adapter.py tests/conftest.py tests/test_paths.py
git commit -m "feat: centralize data dir via paths.data_dir (env RECRUIT_DATA_DIR) with env-isolated test fixture" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: `main.py` 静态托管 + `/api` 重写 + `platform_native`

**Files:**
- Modify: `backend/main.py`（imports 1-25 区；GET /settings 127-137；module tail 追加在 `batch_refetch_stop` 之后——文件最末）
- Create: `tests/test_static_hosting.py`

**Interfaces:**
- Consumes: Task 2 产物（本任务对 `data_dir()` 无直接依赖）；`backend/main.py` 现有 `app`、文件顶部 logger（约 27 行，变量名以实际为准）、`_load_settings()`、`MASKED_PLACEHOLDER`、lifespan、既有路由
- Produces:
  - `backend/main.py` 模块级函数 `_find_web_dir(root: Path | None = None) -> Path | None`（env `RECRUIT_WEB_DIR` → `<程序根>/frontend/dist` → `<程序根>/web`，命中首个含 `index.html` 的；`root` 参数供测试注入）
  - `_StripApiPrefix`（`/api/*` → `/*`，改写 `scope["path"]`）
  - `_SPAStaticFiles`（404 → `index.html` 兜底；**前端 router 已确认是 `createWebHistory()` history 模式**）
  - `GET /settings` 新增键 `platform_native: bool`（= `os.name == "nt"`）→ Task 7 用它做 UI 分支
  - 中间件 + mount 只在探测到 web 目录时注册，且 `app.mount("/", ...)` 位于所有路由之后

- [ ] **Step 1: 写失败测试 `tests/test_static_hosting.py`**

```python
"""静态托管 /api 重写 / platform_native（spec §4.3）：确定性断言，不起真实服务。"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.main import _StripApiPrefix, _find_web_dir


def test_find_web_dir_prefers_env(tmp_path, monkeypatch):
    env_web = tmp_path / "env-web"
    env_web.mkdir()
    (env_web / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("RECRUIT_WEB_DIR", str(env_web))
    assert _find_web_dir() == env_web


def test_find_web_dir_falls_back_to_dist_then_web(tmp_path, monkeypatch):
    monkeypatch.delenv("RECRUIT_WEB_DIR", raising=False)
    for sub in ("frontend/dist", "web"):
        (tmp_path / sub).mkdir(parents=True)
        (tmp_path / sub / "index.html").write_text("x", encoding="utf-8")
    assert _find_web_dir(tmp_path) == tmp_path / "frontend" / "dist"
    (tmp_path / "frontend" / "dist").rename(tmp_path / "frontend" / "dist-gone")
    assert _find_web_dir(tmp_path) == tmp_path / "web"


def test_find_web_dir_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("RECRUIT_WEB_DIR", raising=False)
    assert _find_web_dir(tmp_path) is None


def _mini_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"])
    app.add_middleware(_StripApiPrefix)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def test_api_prefix_rewritten_to_route():
    c = TestClient(_mini_app())
    assert c.get("/api/health").json() == {"ok": True}   # /api 前缀被剥 → 命中 /health
    assert c.get("/health").json() == {"ok": True}       # 无前缀原样可通


def test_get_settings_exposes_platform_native():
    import backend.main as main_mod
    c = TestClient(main_mod.app)
    body = c.get("/settings").json()
    assert body["platform_native"] is (os.name == "nt")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_static_hosting.py -v 2>&1 | tail -20
```

预期：`ImportError: cannot import name '_find_web_dir'` / `'platform_native'` KeyError → FAIL。

- [ ] **Step 3: import 区两处小改（main.py 顶部 1-25 行区）**

Edit A —— 标准库加 `os`（插入在 `import logging` 之后；若 os 已 import 则跳过本步）：

```python
import logging
import os
from contextlib import asynccontextmanager
```

Edit B —— fastapi/starlette 补充（插在 `from pydantic import BaseModel` 之后；以文件实际相邻 import 为准）：

```python
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
```

- [ ] **Step 4: GET /settings 返回 `platform_native`**

Edit 目标 = GET /settings endpoint 的返回组装段（约 133-137 行；锚点含 mask 循环尾 + `return out`，保唯一——文件里另一处 `return out` 在 `_load_settings()` 中，不含 MASKED 分支，不要动）：

```python
        if s["secret"] and val:
            out[key] = MASKED_PLACEHOLDER
        else:
            out[key] = val
    out["platform_native"] = os.name == "nt"   # spec §4.4：前端据此做原生/WSL 文案分支
    return out
```

（old_string 需包含上方 mask 循环的尾几行以保证唯一；new_string 在 `return out` 前插入 `out["platform_native"] = ...` 一行。）

- [ ] **Step 5: module tail 追加（文件最末，`batch_refetch_stop` 定义之后）**

`main.py` 全部既有路由在尾块前已注册完毕。以下块追加在文件最末：SPA 类 → 中间件类 → `_find_web_dir` → 探测命中才注册中间件 + mount（mount 必须最后，保证已有路由优先）：

```python


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
    logging.getLogger("recruit.main").info("托管前端产物: %s（/api 由中间件还原）", _web_dir)
    app.add_middleware(_StripApiPrefix)
    app.mount("/", _SPAStaticFiles(directory=str(_web_dir), html=True), name="web")
else:
    logging.getLogger("recruit.main").info("未找到前端产物目录，纯 API 模式（与现版一致）")
```

注：若文件顶部已有 logger（如 `log = logging.getLogger("recruit.main")`），把 tail 里两处 `logging.getLogger("recruit.main")` 换成那个变量名，保持风格统一。

- [ ] **Step 6: 跑测试确认通过 + 真 app 冒烟**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_static_hosting.py -q 2>&1 | tail -5
.venv/bin/python - <<'PY'
from fastapi.testclient import TestClient
from backend.main import app, _find_web_dir
print("web_dir =", _find_web_dir())
c = TestClient(app)
print("GET /        ->", c.get("/").status_code)
print("GET /health  ->", c.get("/health").status_code)
r = c.get("/api/health"); print("GET /api/health ->", r.status_code, r.json())
print("platform_native ->", c.get("/settings").json().get("platform_native"))
PY
```

预期：pytest 全 PASS；仓库 `frontend/dist` 存在时 `GET /` = 200（页面）、`/api/health` = 200 `{"ok": true}`；WSL 上 `platform_native` = `False`。若 dist 不存在则 `web_dir = None`、`GET /` = 404（纯 API 模式，与现版一致——允许）。

- [ ] **Step 7: 全量回归（无新增失败）+ Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests -q 2>&1 | tail -3   # 与 Task 1 基线一致
git add backend/main.py tests/test_static_hosting.py
git commit -m "feat: serve SPA dist with /api prefix rewrite and expose platform_native in GET /settings" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `backend/liepin/login_browser.py` + open-login 端点 + lifespan 关停

**Files:**
- Create: `backend/liepin/login_browser.py`
- Create: `tests/test_login_browser.py`
- Modify: `backend/main.py`（lifespan `yield` 后收尾区；`POST /settings/liepin/open-login` 插在 Task 3 静态托管尾块**之前**——mount 之后再注册的路由永不命中，见 Step 6）

**Interfaces:**
- Consumes: `backend.paths.data_dir()`（Task 2）；`main._load_settings()`（endpoint 取 channel/proxy/executable）
- Produces（Task 5/6 全部依赖这些签名）:
  - 常量 `_LOGIN_URL = "https://passport.liepin.com/login"`；`_PORT_CANDIDATES = (53471, 9222)`（启动优先 53471；与 WSL 版 mjs 探测端口 `PORTS = [9222, 53471]` 同一集合）
  - `profile_dir() -> Path`（= `data_dir() / "login-profile"`）
  - `cookie_lib_paths() -> list[Path]`（`[profile_dir()/"Default/Network/Cookies", profile_dir()/"Default/Cookies"]`）
  - `open_login_browser(cfg: dict) -> dict`（只返回 `{"ok": True, "message": ...}` 或 `{"ok": False, "error": ...}`）
  - `close_login_browser() -> None`（精确 PID 回收自己拉起的实例，幂等）
  - `cdp_base_url() -> str | None`（记录中的实例仍在跑 → `http://127.0.0.1:<port>`；死实例自动清记录）
  - `export_login_cookies() -> tuple[bool, str, str]` → `(ok, header|"", info|error)`（与 cookie_import `_export_header` 契约同构）
  - `build_cookie_header(cookies: list[dict], now_ms: float) -> str`（纯函数；**筛选**规则与 WSL 导出助手 `liepin_export_cookie.mjs` 一致：仅 liepin.com 域 / 会话 cookie 保留 / 过期丢弃；排序取 `domain|name` **ASCII 升序**只为输出确定性——Cookie 头按集合解析、会话指纹按键值对比较，顺序无语义，不追求复刻 mjs 的 localeCompare ICU 排序）

- [ ] **Step 1: 写失败测试 `tests/test_login_browser.py`**

```python
"""登录浏览器模块（spec §4.5）纯函数与路径断言：不拉起真实浏览器/不走网络。"""
from __future__ import annotations

from backend.liepin import login_browser as lb


def _ck(name, value="v", domain=".liepin.com", expires=-1.0):
    return {"name": name, "value": value, "domain": domain, "path": "/",
            "expires": expires, "httpOnly": True, "secure": True, "sameSite": "Lax"}


def test_login_url_and_ports():
    assert lb._LOGIN_URL == "https://passport.liepin.com/login"
    assert lb._PORT_CANDIDATES == (53471, 9222)   # 启动优先 53471；与 mjs 的 PORTS 同集合


def test_build_header_keeps_session_and_drops_expired():
    now = 1_700_000_000_000
    cks = [
        _ck("lt_auth", expires=-1.0),                        # 会话 cookie → 保留
        _ck("smidV2", expires=None),                          # 无 expires → 保留
        _ck("acw_tc", expires=1_700_000_000),                 # 已过期 → 丢弃
        _ck("XSRF-TOKEN", expires=(now // 1000) + 3600),      # 未来过期 → 保留
    ]
    header = lb.build_cookie_header(cks, now)
    assert "lt_auth=v" in header and "XSRF-TOKEN=v" in header
    assert "acw_tc" not in header


def test_build_header_filters_non_liepin_domains():
    cks = [_ck("sessionid", domain="example.com"),
           _ck("lt_auth", domain="passport.liepin.com")]
    header = lb.build_cookie_header(cks, 1_700_000_000_000)
    assert "lt_auth" in header and "sessionid" not in header


def test_build_header_sorted_by_domain_then_name():
    now = 1_700_000_000_000
    cks = [
        _ck("UniqueKey", domain="liepin.com"),
        _ck("lt_auth", domain=".liepin.com"),
        _ck("_e_ld_auth_", domain=".passport.liepin.com"),
    ]
    # ASCII 升序（键 f"{domain}|{name}"）：'.'(0x2E) < 'l' → 两个点前缀域先排；
    # 同以 '.' 开头时 '.liepin.com' < '.passport.liepin.com'。期望顺序 lt_auth →
    # _e_ld_auth_ → UniqueKey —— 已用 node 实测确认与 mjs localeCompare 输出一致。
    assert lb.build_cookie_header(cks, now) == "lt_auth=v; _e_ld_auth_=v; UniqueKey=v"


def test_profile_and_cookie_paths_under_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    assert lb.profile_dir() == tmp_path / "d" / "login-profile"
    assert lb.cookie_lib_paths() == [
        tmp_path / "d" / "login-profile" / "Default" / "Network" / "Cookies",
        tmp_path / "d" / "login-profile" / "Default" / "Cookies",
    ]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_login_browser.py -v 2>&1 | tail -20
```

预期：`ModuleNotFoundError: backend.liepin.login_browser` → FAIL。

- [ ] **Step 3: 新建 `backend/liepin/login_browser.py`**

完整文件（中文注释风格与现有模块一致）：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_login_browser.py -q 2>&1 | tail -5
```

预期：全 PASS（纯函数/路径断言，不拉起浏览器）。

- [ ] **Step 5: lifespan 关停登录浏览器（main.py，`yield` 后收尾区）**

Edit 目标（lifespan 内 `yield` 之后的收尾序列，现约 76-79 行）：

```python
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
```

- [ ] **Step 6: 在静态托管尾块之前插入 open-login 端点**

⚠️ 注册位置有讲究：**不能**追加到文件最末。Starlette 按 routes **注册顺序**匹配：Task 3 的
`app.mount("/", …)` 位于文件最末（import 时执行），匹配**所有**路径——在它之后注册的路由
永远不会被命中（SPA 兜底还会把 POST 吞成 index.html 回 200）。因此把下面这块插到 Task 3 的
banner 注释 `# ================ 静态托管（前端零改动，spec §4.3）================` **之前**
（即 `batch_refetch_stop` 定义结束之后），保证它先于 mount 注册：

```python


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
```

（`log` 换成文件顶部实际 logger 变量名；确认 `_load_settings` 已定义——它就是 GET/POST /settings 内部调用的那个，别新造。）

- [ ] **Step 7: 冒烟 + 回归 + Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python - <<'PY'
from fastapi.testclient import TestClient
from backend.main import app
c = TestClient(app)
r = c.post("/settings/liepin/open-login")
print(r.status_code, r.json())   # WSL 预期 200 + {"ok": false, "error": "…WSL…liepin login…"}
PY
.venv/bin/python -m pytest tests -q 2>&1 | tail -3    # 失败数 = 基线 5
git add backend/liepin/login_browser.py tests/test_login_browser.py backend/main.py
git commit -m "feat: login browser module + POST /settings/liepin/open-login + lifespan teardown" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: `adapter.py` 原生分支（`_launch_native` / `_attach_native` / close native guard）

**Files:**
- Modify: `backend/liepin/adapter.py`（`_launch_with_retry` 顶部；`close()` 的 attach 分支；`_attach_existing` 与 `close` 之间插入两个新方法）

**Interfaces:**
- Consumes: `adapter.user_data_dir()`（Task 2）；`login_browser.cdp_base_url()`（Task 4）；`self._pw`/`self.headless`/`self.channel`/`self.executable`/`self.proxy`/`self.attach`/`self._ctx`/`self._page`/`self._cdp`（adapter 既有属性——开工先读 `__init__` 与现有启动/attach 段，确认赋值语义与异常类名 `LiepinLoginError`，以实际为准）
- Produces:
  - `_launch_native() -> <context>`（Windows 原生启动：chromium = playwright 原生 bundled（`user_data_dir()`、headless=self.headless）；chrome/msedge = playwright 原生 `channel=` 自探测；显式 executable 优先；proxy 透传；失败 → `LiepinLoginError` 引导文案，**绝不静默换引擎**）
  - `_attach_native() -> <context>`（复用登录浏览器 CDP；未开 → 引导先点「打开浏览器登录」；收尾赋值与现有 attach 分支一致，调用方无感知）
  - `close()` attach 分支：`self._cdp.get("native")` 时**跳过** `_windows_kill_listener(_RELAY_PORT)`（登录浏览器归 login_browser 模块 lifespan 回收）
  - 模块函数 `_win_native() -> bool`（= `os.name == "nt"`，§4.4 硬门控）

- [ ] **Step 1: 读 adapter 现状，锁定三类锚点（只读）**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
grep -n "def __init__\|self.attach\|self.headless\|self.channel\|self.executable\|self.proxy\|_ctx\|_page\|_cdp\b\|LiepinLoginError\|_windows_kill_listener\|def _launch_with_retry\|def close\|def _attach_existing" backend/liepin/adapter.py | head -70
sed -n '380,430p;455,545p;1030,1100p' backend/liepin/adapter.py
```

目的：确认 (a) attach 与非 attach 分支最后如何设置 `self._ctx`/`self._page`/`self._cdp`（`_launch_native`/`_attach_native` 必须做**相同的收尾赋值**，调用方其余代码不感知差异）；(b) bundled chromium 启动的 playwright 调用与 kwargs（`_launch_native` 的 chromium 分支照抄同一调用，只改数据目录解析、去掉一切 WSL 桥接）；(c) 异常类与错误文案风格。

- [ ] **Step 2: 插模块函数 `_win_native()` 与 `_launch_with_retry` 顶部原生门控**

(a) 在文件工具函数区（如 `_win_path` 附近）加：

```python
def _win_native() -> bool:
    """Windows 原生判定（spec §4.4）：os.name == 'nt' 恒为真，不做任何启发式探测。"""
    return os.name == "nt"
```

(b) `_launch_with_retry` 方法体第一行（`if self.attach:` 之前）插入：

```python
        if _win_native():
            # §4.4 硬门控：Windows 原生（同事机器零 WSL）——CDP 桥 / PowerShell relay /
            # wslpath / /mnt/c 探测等整段互操作代码在 os.name=='nt' 下**永不进入执行**。
            if self.attach:
                return self._attach_native()
            return self._launch_native()
```

- [ ] **Step 3: 插入 `_launch_native()` 与 `_attach_native()`（`close()` 定义之前）**

先照 Step 1 读到的既有 bundled 启动段，把它对 playwright 的 `launch_persistent_context(...)` 调用与 kwargs 原样搬入 chromium 分支。整体骨架：

```python
    def _launch_native(self):
        """Windows 原生启动（os.name=='nt'，spec §4.4）：playwright 在本机直接拉起。

        默认 channel=chromium → 随包分发的 playwright chromium（静默语义与 WSL 原版
        一致：headless=self.headless）；chrome/msedge → playwright 原生 channel=
        自动探测本机安装（Windows 上无需 /mnt/c 桥）；显式 executable 优先。
        探测不到/启动失败 → LiepinLoginError 清晰引导，**绝不静默换引擎**。
        每次失败退避节奏与既有 WSL bundled 分支一致（约 5s × 4 次）。
        """
        last: Exception | None = None
        for _attempt in range(4):
            try:
                # 照抄既有 bundled 启动段（Step 1 读到的 playwright 调用与 kwargs），只改：
                #   user_data_dir 来源 → str(user_data_dir())
                #   去掉一切 Windows 桥接（relay/CDP 桥只在 WSL 互操作路径需要）
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
        代理 → proxy={"server": ...}；既有分支里其它 args/ignore_default_args 照抄。
        """
        kwargs: dict = {"user_data_dir": str(user_data_dir()),
                        "headless": self.headless}
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
        绝不清除/注入 cookie、绝不关闭该浏览器。收尾赋值照 Step 1 抄既有 attach 分支
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
        # 照 Step 1 确认的既有 attach 收尾赋值
        self._ctx = ctx
        self._page = None
        self._cdp = {"base": base, "attach": True, "native": True}
        return ctx
```

- [ ] **Step 4: `close()` attach 分支 native guard**

现 attach 分支收尾（`_windows_kill_listener(_RELAY_PORT)` 那几行）改为：

```python
                if self._cdp:
                    # 原生 attach（native=True）：登录浏览器归 login_browser 模块回收
                    # （lifespan 精确 PID），这里只清状态，绝不动其端口/进程。
                    if not self._cdp.get("native"):
                        _windows_kill_listener(_RELAY_PORT)
                    self._cdp = None
                return
```

（old_string 取现有分支的完整尾段——含 `self._ctx = None` / `self._page = None` 的收尾，改完语义：非 native 分支行为与原来逐字节一致。）

- [ ] **Step 5: 静态自查 + 单元测试 `_win_native`**

`tests/test_adapter_native.py` 新建：

```python
"""adapter 原生门控（spec §4.4）：_win_native 恒等于 os.name == 'nt' 的机械判定。
真实浏览器差异由 Task 8 Windows 手工清单覆盖（本文件只保 WSL 回归确定性）。"""
from __future__ import annotations

import os

from backend.liepin import adapter as a


def test_win_native_is_os_name_gate():
    assert a._win_native() is (os.name == "nt")
```

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_adapter_native.py -q 2>&1 | tail -3
grep -n "USER_DATA_DIR" backend/liepin/adapter.py        # 应为 0 命中（Task 2 已清干净）
.venv/bin/python - <<'PY'
import backend.liepin.adapter as a
print("_win_native() =", a._win_native())                # WSL 预期 False
PY
```

并静态自查原生方法：`grep -n "_windows_kill_listener\|taskkill\|netstat\|relay\|wslpath\|/mnt/c" backend/liepin/adapter.py` 中命中的行号都不落在 `_launch_native`/`_attach_native` 方法体内（§4.4：原生分支永不执行互操作）。

- [ ] **Step 6: WSL 回归门 + 冒烟**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests -q 2>&1 | tail -3   # 失败数 = 基线 5，无新增
.venv/bin/python - <<'PY'
from fastapi.testclient import TestClient
from backend.main import app
c = TestClient(app)
print("GET /settings  ->", c.get("/settings").status_code)
print("POST /settings ->", c.put("/settings", json={"values": {}}).status_code)
PY
```

（WSL 上 `_win_native()=False` → 全部走既有路径，本条 = 回归确认。真实浏览器路径在 Task 8 的 Windows 清单验证。）

- [ ] **Step 7: Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add backend/liepin/adapter.py tests/test_adapter_native.py
git commit -m "feat: native launch/attach branches behind os.name gate in adapter (spec 4.4)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: `cookie_import.py` 原生分支（登录浏览器 watch + Python CDP 导出）

**Files:**
- Modify: `backend/cookie_import.py`（`_AUTH_KEYS` 之后插 `_native()`/`_login_browser()`；`_ensure_ready` 头部；`_watch_paths`；`_export_header` 头部）
- Create: `tests/test_cookie_import.py`

**Interfaces:**
- Consumes: `login_browser.cookie_lib_paths()`、`login_browser.export_login_cookies()`（Task 4）
- Produces: 原生分支自动导入闭环——`start()` 基线 stat 登录 profile Cookie 库 → 15s 轮询（`_tick` 零改动）→ 变化 → `_export_header`（Python CDP）→ 指纹比对/双验证/落库（既有逻辑零改动）

- [ ] **Step 1: 写失败测试 `tests/test_cookie_import.py`**

```python
"""cookie_import 原生分支（spec §4.5）dispatch 纯测：不跑真实轮询/不连浏览器。"""
from __future__ import annotations

import os

from backend import cookie_import as ci
from backend.liepin import login_browser as lb


def test_native_flag_is_os_gate():
    # §4.4 机械判定；真实平台差异由 Task 8 Windows 清单覆盖
    assert ci._native() is (os.name == "nt")


def test_watch_paths_native_uses_login_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setattr(ci, "_native", lambda: True)
    assert ci._watch_paths() == [
        tmp_path / "d" / "login-profile" / "Default" / "Network" / "Cookies",
        tmp_path / "d" / "login-profile" / "Default" / "Cookies",
    ]


def test_export_header_native_dispatches_to_python_cdp(monkeypatch):
    monkeypatch.setattr(ci, "_native", lambda: True)
    monkeypatch.setattr(ci, "_ensure_ready", lambda: None)
    calls = {}

    def fake_export():
        calls["hit"] = True
        return (True, "lt_auth=abc", "ok")

    monkeypatch.setattr(lb, "export_login_cookies", fake_export)
    ok, header, info = ci._export_header()
    assert ok and header == "lt_auth=abc" and calls.get("hit")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_cookie_import.py -v 2>&1 | tail -20
```

预期：`AttributeError: module has no attribute '_native'` → FAIL。

- [ ] **Step 3: `_AUTH_KEYS` 之后插分支函数（现 40-41 行之间）**

```python


# ---------- 原生分支（Windows 安装版）：自启专用登录浏览器 + Python CDP ----------
def _native() -> bool:
    """安装版判定（spec §4.4）：os.name == 'nt' —— 原生路径永不依赖 node/liepin-cli/WSL 组件。"""
    return os.name == "nt"


def _login_browser():
    """延迟 import：登录浏览器仅在原生分支用到（playwright 系 lazy 加载，模块级导入无副作用）。"""
    from .liepin import login_browser
    return login_browser
```

- [ ] **Step 4: `_ensure_ready` 原生早退**

现函数体开头是 `global _ready, ...` + `if _ready: return None`。在 `if _ready: return None` 之后插入：

```python
    if _native():
        # 原生分支工具链 = 专用登录浏览器 profile（Python CDP 导出），零 node /
        # liepin-cli / wslpath；目录由 login_browser 侧自理。首轮 _tick 会 stat
        # Cookie 库 mtime——文件尚未生成时 OSError 跳过，浏览器登录后自然接上。
        _ready = True
        return None
```

（插在 `if _win_home is None:` 块之前——保证原生分支绝不触碰 `%USERPROFILE%` 探测。）

- [ ] **Step 5: `_watch_paths` 原生分支**

现函数整体替换为：

```python
def _watch_paths() -> list[Path]:
    """Cookie 库路径：原生分支 = 登录浏览器 profile（Chrome>=130 在 Network/）；
    WSL 分支维持原解析（liepin-cli 常驻 Chrome 的 .liepin-cli 路径）。"""
    if _native():
        return _login_browser().cookie_lib_paths()
    if _win_home is None:
        return []
    base = Path(_wslpath(os.path.join(_win_home, ".liepin-cli")))
    return [base / r for r in _WATCH_REL]
```

- [ ] **Step 6: `_export_header` 原生分支**

现函数开头 `err = _ensure_ready()` 之后插入：

```python
    if _native():
        # Python CDP 直连登录浏览器（零 node）；三元组契约与 WSL 分支一致
        try:
            return _login_browser().export_login_cookies()
        except Exception as e:  # noqa: BLE001
            return False, "", f"导出失败: {e}"
```

- [ ] **Step 7: 跑测试确认通过 + 全量回归**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python -m pytest tests/test_cookie_import.py -q 2>&1 | tail -5
.venv/bin/python -m pytest tests -q 2>&1 | tail -3    # 失败数 = 基线 5
```

- [ ] **Step 8: Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add backend/cookie_import.py tests/test_cookie_import.py
git commit -m "feat: native cookie import via login-browser profile watch + python CDP export" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 设置页原生 UI 分支（Settings.vue + api.js）

**Files:**
- Modify: `frontend/src/api.js`（`importLiepin` 后加一行）
- Modify: `frontend/src/views/Settings.vue`（refs 6-8 区；`load()` 内 for 循环后/catch 前；`importLiepin` 之后加方法；模板 159-168 / 243-258 / 279-287 / 307-311 文案分支）

**Interfaces:**
- Consumes: `GET /settings` 新键 `data.platform_native`（Task 3）；`POST /settings/liepin/open-login`（Task 4）
- Produces: 原生机器设置页呈现「打开浏览器登录」主按钮组；WSL 保持原 liepin login 文案与交互不变

- [ ] **Step 1: `api.js` 加一行**

现文件第 75 行左右：

```js
  importLiepin: () => http.post('/settings/liepin/import'),
```

在其后加：

```js
  openLoginBrowser: () => http.post('/settings/liepin/open-login'),
```

- [ ] **Step 2: Settings.vue script 改造**

(a) refs（现 6-8 行）追加两行：

```js
const loading = ref(false)
const testing = ref(false)
const importing = ref(false)
const openingLogin = ref(false)
const isNative = ref(false)
```

(b) `load()`（现 34-51 行）在 for 循环结束后、`} catch (e) {` 之前插入：

```js
    // platform_native = Windows 原生（安装版）：控制登录导入按钮组与文案分支
    isNative.value = !!data.platform_native
```

(c) `importLiepin`（现 113-132 行）结束后追加方法：

```js
const openLoginBrowser = async () => {
  // Windows 原生主路径：可见窗口拉起专用登录浏览器；用户手动登录后 ~20s 自动导入
  openingLogin.value = true
  try {
    const { data } = await api.openLoginBrowser()
    if (data.ok) {
      ElMessage.success(data.message || '登录浏览器已打开，请在弹出的窗口手动登录')
      await load()
    } else {
      ElMessage.error(data.error || '打开登录浏览器失败')
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '打开登录浏览器失败'))
  } finally {
    openingLogin.value = false
  }
}
```

- [ ] **Step 3: 按钮组双分支（现模板 159-168 行整体替换）**

```html
        <el-form-item v-if="isNative">
          <el-button type="primary" :loading="openingLogin" @click="openLoginBrowser">打开浏览器登录</el-button>
          <el-button :loading="importing" @click="importLiepin">立即导入</el-button>
          <el-button type="primary" :loading="testing" @click="testLiepin">测试连接</el-button>
          <span class="muted" style="margin-left: 12px; display: block; margin-top: 4px">
            <b>点「打开浏览器登录」会弹出一个专用浏览器窗口</b>（独立资料目录，不会碰你日常用的
            Chrome/Edge）；在弹出的窗口里<b>手动</b>输入账号密码或扫码（系统绝不自动登录）。
            登录完成后约 20 秒，系统会自动把会话导入并加密保存在本机；也可点「立即导入」不等。
            粘贴 Cookie 仍可作为兜底。
          </span>
        </el-form-item>
        <el-form-item v-else>
          <el-button type="primary" plain :loading="importing" @click="importLiepin">从 liepin login 导入</el-button>
          <el-button type="primary" :loading="testing" @click="testLiepin">测试连接</el-button>
          <span class="muted" style="margin-left: 12px">
            <b>每次在终端里跑完 liepin login，系统都会在约 20 秒内自动把新会话导入到这里</b>
            （静默模式跑任务全靠这份 Cookie 登录，无需粘贴）；也可以点「从 liepin login 导入」立即导入。
            登录态失效（-1701）后跑一次 liepin login 即可自愈。
            系统绝不会自动登录，登录始终由你在终端手动完成。
          </span>
        </el-form-item>
```

- [ ] **Step 4: attach 开关区文案分支（现 243-258 行区域）**

(a) `active-text` 改绑定（现 247 行）：

```html
            :active-text="isNative ? '在登录浏览器里开着页面操作（登录窗口别关，可选）' : '在登录的 Chrome 里开着页面操作（可选）'"
```

(b) 其下说明 div（现 250-258 行）整段替换为：

```html
          <div class="muted" style="margin-top: 6px; line-height: 1.6">
            <template v-if="isNative">
              <b>默认（开关关闭）＝ 静默模式</b>：运行/拉岗位/邀请全程<b>不弹出任何猎聘页面</b>，
              靠已保存的 Cookie 在后台登录跑任务（默认用随安装包分发的 Chromium 引擎）。
              Cookie 来源：设置页点「打开浏览器登录」完成手动登录后自动导入；粘贴 Cookie 兜底。
              <br />
              仅在你想<b>亲眼看着</b>每一步操作时，再打开上面的开关：页面会开在你登录的那个
              专用浏览器窗口里（登录窗口别关；任务结束后自动关标签页），绝不影响你日常的浏览器。
            </template>
            <template v-else>
              <b>默认（开关关闭）＝ 原版静默模式</b>：运行/拉岗位/邀请全程<b>不弹出任何猎聘页面</b>，
              用下方设置的浏览器（默认 Chrome 无头引擎）在后台跑，靠已保存的 Cookie 保持登录。
              Cookie 来源：在终端跑一次 <b>liepin login</b>（会自动打开 Chrome 完成登录），
              系统约 20 秒内自动把会话导入；也可点「从 liepin login 导入」立即导入。
              <br />
              仅在你想<b>亲眼看着</b>每一步操作时，再打开上面的开关：页面会在你登录的那个 Chrome 里
              以标签页形式打开（结束后自动关闭），不另弹新窗口，也绝不会关闭你的 Chrome。
            </template>
          </div>
```

- [ ] **Step 5: 其余两处 WSL copy 分支**

(a) executable placeholder（现 279 行）改绑定：

```html
          <el-input v-model="form.browser_executable" :placeholder="isNative ? '留空自动探测（推荐）。填绝对路径可指定特定浏览器' : '留空自动探测。WSL 下可用：/mnt/c/Program Files/Google/Chrome/Application/chrome.exe'" />
```

(b) 下方 muted 说明（现 282-287 行）整段替换（去掉「本机为 WSL2 无图形界面」一句——原生下该句不成立；文案中性化后一个版本通吃两分支）：

```html
        <el-form-item>
          <span class="muted">
            默认静默模式使用下方「无头模式 / 浏览器通道 / 可执行文件」以已保存 Cookie 后台运行：
            <b>无头模式保持开启</b>即完全不弹任何页面（原版行为）；「浏览器通道」选 Chrome 即用
            本机 Chrome 引擎跑。仅当你打开上方「在登录的浏览器里开着页面操作」开关时，才忽略
            下方这些设置（页面开在登录浏览器窗口里）。
          </span>
        </el-form-item>
```

(c) 代理卡 muted（现 307-311 行）整段替换：

```html
        <el-form-item>
          <span class="muted" v-if="isNative">
            访问猎聘必需（通常是科学上网工具），公司网络直连猎聘被重置时必填。填本机代理端口即可，
            如 http://127.0.0.1:7890；「打开浏览器登录」和后台任务都会走这份代理设置。
          </span>
          <span class="muted" v-else>
            WSL 无法直连猎聘时（连接被重置）必填。填 Windows 侧代理端口即可，系统会自动走 WSL → 宿主机代理。
          </span>
        </el-form-item>
```

- [ ] **Step 6: 构建验证 + 同源冒烟**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app/frontend
npm run build 2>&1 | tail -8
```

预期：构建成功（`frontend/dist` 被 .gitignore 忽略，不入 git）。vue 语法错误则修复到构建通过。再验证同源托管（WSL 上 isNative=false 分支不炸）：

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
.venv/bin/python - <<'PY'
from fastapi.testclient import TestClient
from backend.main import app
c = TestClient(app)
r = c.get("/")
print("GET / ->", r.status_code, r.headers.get("content-type"))
PY
```

- [ ] **Step 7: Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add frontend/src/api.js frontend/src/views/Settings.vue
git commit -m "feat: settings page native branch (open login browser button, copy split by platform_native)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Windows 原生直启手工验收（文档 + 逐项执行）

**Files:**
- Create: `docs/windows-native-check-2026-09-04.md`（清单本体，勾选状态随执行回填）

**Interfaces:**
- Consumes: Task 2-7 全部产物；开发机 Windows 侧（同一 `D:\AI应用\hr\hr\recruit-app` 仓库，WSL 与 Windows 共享）
- Produces: 阶段 2 验收通过结论（spec §5 阶段 2 / §6 items 3、4）——打包（Task 9）的前置门槛；清单文档作为 Task 9 安装自测的对照模板

- [ ] **Step 1: 写清单文档全文**

`docs/windows-native-check-2026-09-04.md`：

```markdown
# Windows 原生直启验收清单（阶段 2，spec §6 items 3/4）

日期：2026-09-04 · 机器：开发机 Windows 侧（.venv-win 直启，不打包）

## 准备
- [ ] `.venv-win` 就绪（Task 8 Step 1 记录的命令完成）；npm 构建过 `frontend\dist`（Task 7）
- [ ] 数据隔离：本次全部落点 = 临时目录（不碰仓库 data/）：
      PowerShell: `$env:RECRUIT_DATA_DIR = "D:\AI应用\hr\hr\recruit-app\data-win-check"`

## 启动与静态托管
- [ ] 后端起（PowerShell，仓库根）：
      `.venv-win\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
- [ ] 浏览器开 `http://127.0.0.1:8000` → 页面正常（同源 dist，无跨域报错）
- [ ] 直接开深层路由 `http://127.0.0.1:8000/settings` → 仍出页面（history 兜底生效）

## 设置保存 / 平台态
- [ ] `GET /settings` 里 `platform_native = true`（浏览器 F12 Network 或后端冒烟）
- [ ] 原生按钮组出现：「打开浏览器登录 / 立即导入 / 测试连接」；WSL 专属「liepin login」文案不出现

## 登录导入主路径（§4.5）—— spec §6 第 3 项
- [ ] 设置 http/https 代理 = 本机科学上网端口（如 127.0.0.1:7890）并保存
- [ ] 点「打开浏览器登录」→ 弹出**独立窗口**（非日常浏览器窗口），地址为猎聘登录页
- [ ] 核对弹窗命令行含 `--user-data-dir=...\data-win-check\login-profile` 与
      `--remote-debugging-port=53471`（任务管理器 → 命令行）
- [ ] 在弹出窗口**手动**登录企业版账号（输账号密码/扫码；系统无任何自动代填）
- [ ] 约 15-25 秒后 toast「Cookie 已自动导入并保存」（或点「立即导入」立即生效）
- [ ] 再点「立即导入」→ 指纹相同返回「会话与库内一致」（不重复写库）
- [ ] 点「测试连接」（attach 开关保持关闭）→ 成功（默认 chromium 静默引擎）
- [ ] 粘贴 Cookie 兜底路径照旧可用（清掉已导入值 → 粘贴 → 测试连接成功）

## attach 复用登录浏览器（§4.4/§4.5）
- [ ] 登录浏览器窗口保持打开 → 设置开「在登录的浏览器里开着页面操作」→ 测试连接
      → 页面以标签页开进该窗口、成功
- [ ] 手动关掉登录浏览器窗口 → 测试连接 → 明确报错提示「先在设置页点『打开浏览器登录』」
- [ ] 重新点「打开浏览器登录」→ 新窗口正常、可再登录导入
- [ ] 登录浏览器窗口打开期间，任务管理器无任何被强杀的 Chrome/msedge 记录；日常浏览器不受影响

## 拉取岗位与 WSL 结果一致（spec §6 第 4 项）
- [ ] 拉取岗位 1 页，关键字段与 WSL 版同账号结果一致（job 标题/公司/薪资区间）
- [ ] 失败路径（断网/端口占用等）报错为中文可读信息，不裸抛堆栈

## 收尾
- [ ] 停后端 → 确认 login-profile 浏览器进程被回收（lifespan close_login_browser；仅精确 PID）
- [ ] 删 `data-win-check` 临时目录（验证完不留残余）
```

- [ ] **Step 2: Windows venv 准备（一次性，在开发机 Windows 侧执行）**

PowerShell 在 `D:\AI应用\hr\hr\recruit-app`：

```powershell
py -3 --version          # 需 3.12.x；没有则先装 python.org 3.12（勾 Add to PATH）
py -3 -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\python.exe -m pip install -r requirements.txt
.venv-win\Scripts\python.exe -m playwright install chromium
```

（若开发机 Windows 侧无法直连 pypi/playwright CDN：先 WSL 侧 `pip download` 离线轮子 + 拷贝 `~/.cache/ms-playwright` 产物过去——逐项记录，属 Task 9 离线打包前置调研。）

- [ ] **Step 3: 执行清单并回填勾选**

在开发机 Windows 侧逐项手测（真实浏览器 + 真实登录，无法自动化；手动登录那步需要用户参与）。每项结果（✓ / ✗ + 现象）回填进清单文件。**任一关键项失败 → 停下按 systematic-debugging 追根因修完再继续**，不要带病进 Task 9。

- [ ] **Step 4: Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add docs/windows-native-check-2026-09-04.md
git commit -m "docs: windows native direct-run acceptance checklist (spec stage 2)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: 打包（run.bat / 启动器 / Inno Setup / BUILD.md）

**Files:**
- Create: `packaging/run.bat`
- Create: `packaging/RecruitApp.launch.vbs`
- Create: `packaging/install.iss`
- Create: `packaging/BUILD.md`
- 产物（不 commit）：`packaging/stage/`、`packaging/output/recruit-app-setup-<版本>.exe`

**Interfaces:**
- Consumes: Task 2-7 全部后端/前端产物；spec §4.1/§4.6/§4.8
- Produces: 全离线安装包；spec §6 第 1/2/5/6 项在 Step 5 清单验证（安装/桌面图标/自启/升级/卸载保 data）

- [ ] **Step 1: 写 `packaging/run.bat`**

```bat
@echo off
rem RecruitApp 单机版后端启动器（安装版 app\run.bat；被桌面 launcher 与登录计划任务调用）
rem 注入数据/前端/浏览器目录 → 起 uvicorn（仅 127.0.0.1:8000）→ 日志追加写 data\logs\app.log。
rem 幂等：8000 上 /health 已通（本程序在跑）→ 直接退出，防双份（spec §4.6）。
setlocal
cd /d "%~dp0"
set "DATA_DIR=%LOCALAPPDATA%\RecruitApp\data"
if not exist "%DATA_DIR%\logs" mkdir "%DATA_DIR%\logs"
set "RECRUIT_DATA_DIR=%DATA_DIR%"
set "RECRUIT_WEB_DIR=%~dp0web"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8000/health -TimeoutSec 2).StatusCode } catch { exit 1 }" | findstr /C:"200" >nul
if not errorlevel 1 exit /b 0
"%~dp0python\pythonw.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 >> "%DATA_DIR%\logs\app.log" 2>&1
endlocal
```

- [ ] **Step 2: 写 `packaging/RecruitApp.launch.vbs`**

```vbscript
' RecruitApp 桌面启动器（spec §4.6）：后端已在跑(/health 通) → 直接开网页；
' 不在 → 隐藏窗口拉起 run.bat 并轮询最长 ~20s → 再开网页；失败给明确提示，不静默。
Option Explicit
Dim fso, appDir, sh, i
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
appDir = fso.GetParentFolderName(WScript.ScriptFullName) & "\"
sh.CurrentDirectory = appDir
If Not IsHealthUp() Then
    sh.Run """" & appDir & "run.bat""", 0, False
    For i = 1 To 40          ' 200ms × 40 = 最长 20s（uvicorn 冷启动余量）
        WScript.Sleep 200
        If IsHealthUp() Then Exit For
    Next
End If
If IsHealthUp() Then
    sh.Run "http://127.0.0.1:8000", 1, False
Else
    MsgBox "招聘系统启动失败：8000 端口可能被其它程序占用。" & vbCrLf & _
           "请关闭占用 8000 端口的程序后，重新双击桌面的『招聘系统』。", 48, "招聘系统"
End If

Function IsHealthUp()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.open "GET", "http://127.0.0.1:8000/health", False
    http.send
    IsHealthUp = (http.status = 200)
    On Error GoTo 0
End Function
```

- [ ] **Step 3: 写 `packaging/install.iss`**

```ini
; RecruitApp 单机版安装脚本（spec §4.1/§4.6/§4.8）。开发机 Windows 侧构建（Inno Setup 6）。
; 构建前先照 BUILD.md（Task 9 Step 4）组好 stage\app：
;   python\（便携 3.12 + venv site-packages）+ backend\ + web\ + browsers\ + run.bat + 本 vbs
#define MyAppName "招聘系统"
#define MyAppVersion "0.9.0"
#define MyAppPublisher "华联招聘团队"
#define StageDir "stage"

[Setup]
AppId={{0F7E5C2A-8D9B-4C21-A6F3-9B1D2E4F5A60}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\RecruitApp
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=recruit-app-setup-{#MyAppVersion}
Compression=lzma2/ultra
SolidCompression=yes
UninstallDisplayName={#MyAppName}
WizardStyle=modern
; 卸载保留 data\（含 Fernet 密钥/导出/上传）；[Code] 卸载末尾提示用户手动删除
CloseApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "__pycache__","*.pyc",".venv*"
Source: "{#StageDir}\app\data\*"; DestDir: "{app}\data"; Flags: recursesubdirs createallsubdirs skipifsourcedoesntexist; Excludes: "__pycache__","*.pyc"

[Icons]
Name: "{autodesktop}\招聘系统"; Filename: "wscript.exe"; Parameters: """{app}\app\RecruitApp.launch.vbs"""; WorkingDir: "{app}\app"; Tasks: desktopicon; Comment: "启动招聘系统（本机单机版）"

[Run]
Filename: "schtasks.exe"; Parameters: "/Create /F /TN ""RecruitApp"" /TR """"{app}\app\run.bat"""" /SC ONLOGON /RL LIMITED"; Flags: runhidden; StatusMsg: "注册开机自启（当前用户登录时）…"
Filename: "wscript.exe"; Parameters: """{app}\app\RecruitApp.launch.vbs"""; Flags: nowait skipifsilent; StatusMsg: "启动招聘系统…"; Description: "立即启动招聘系统"

[UninstallDelete]
; 升级改名残留的 app.old 一并清掉（data\ 永不在此列）
Type: filesandordirs; Name: "{app}\app.old"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  // 只回收本程序自己的后端进程：pythonw.exe 且命令行含 backend.main:app → 精确 PID kill。
  // 绝不 taskkill /IM 整类、绝不碰用户其它进程（spec §4.4 + 全局约束）。
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "' +
    'Get-CimInstance Win32_Process -Filter ''Name=''pythonw.exe'''' | ' +
    'Where-Object { $_.CommandLine -match ''backend.main:app'' } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // 升级：旧 app\ 改名 app.old（Inno 随后全新覆盖写 app\）；data\ 不在其中，天然保留。
  // 首装时无 app\，判存在再改名。
  if DirExists(ExpandConstant('{app}\app')) then
    if not RenameFile(ExpandConstant('{app}\app'), ExpandConstant('{app}\app.old')) then
      Result := '无法备份旧版本目录（app.old）。' + #13#10 +
                '请关闭正在运行的招聘系统后重试；如仍失败请手动把 ' +
                ExpandConstant('{app}\app') + ' 改名后重装。';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('卸载完成。你的数据（含加密密钥、导出文件、上传简历）仍在 ' + #13#10 +
           ExpandConstant('{app}\data') + #13#10 + #13#10 +
           '如需彻底删除请手动删除整个 RecruitApp 文件夹。', mbInformation,
           MB_OK);
end;
```

- [ ] **Step 4: 写 `packaging/BUILD.md`（组装 + 构建命令全文）**

````markdown
# 单机版安装包构建（spec §4.8）—— 全程在开发机 **Windows 侧**执行

前置：Task 8 的 `.venv-win` 已就绪（3.12 与开发机同版本 = 最稳路线：便携 Python = 官方
python 安装目录精简 + 从 .venv-win 拷贝 site-packages 覆盖，见 §4.8「首选」路线）。

## 1) 前端产物
```powershell
cd D:\AI应用\hr\hr\recruit-app\frontend
npm run build          # → frontend\dist
```

## 2) 组 stage（PowerShell，仓库根下）
```powershell
$stage = "D:\AI应用\hr\hr\recruit-app\packaging\stage"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$stage\app" | Out-Null

# 便携 Python：官方安装根（python.exe/pythonw.exe/DLLs/Lib 等）全量拷，
# 再把 .venv-win 的 site-packages 覆盖进去（同版本、已含全部离线依赖 + playwright 包）
# 从 .venv-win 反查真实基础 Python 安装根（与运行版同版本同根，最可靠）；
# 别用 (Get-Command py) 推导——py launcher 常解析到 C:\Windows\py.exe，不是安装根
$pyRoot = & "D:\AI应用\hr\hr\recruit-app\.venv-win\Scripts\python.exe" -c "import sys; print(sys.base_prefix)"
robocopy "$pyRoot" "$stage\app\python" /E /XD "__pycache__" /NFL /NDL /NJH /NJS /NC /NS
robocopy "D:\AI应用\hr\hr\recruit-app\.venv-win\Lib\site-packages" "$stage\app\python\Lib\site-packages" /E /XD "__pycache__" /NFL /NDL /NJH /NJS /NC /NS

# playwright 浏览器：Windows 侧 playwright install chromium 产物整包搬入
# （chromium-*\chrome-win = 登录可见窗口用；chromium_headless_shell-* = 无头任务引擎）
robocopy "$env:LOCALAPPDATA\ms-playwright" "$stage\app\browsers" /E /NFL /NDL /NJH /NJS /NC /NS

# 后端代码（含 liepin/win/*.mjs —— WSL 回归基线随包保留，原生路径永不调用）
robocopy "D:\AI应用\hr\hr\recruit-app\backend" "$stage\app\backend" /E /XD "__pycache__" /NFL /NDL /NJH /NJS /NC /NS

# 前端产物 → web\（run.bat 的 RECRUIT_WEB_DIR 指向这里）
robocopy "D:\AI应用\hr\hr\recruit-app\frontend\dist" "$stage\app\web" /E /NFL /NDL /NJH /NJS /NC /NS

# 启动器
Copy-Item "D:\AI应用\hr\hr\recruit-app\packaging\run.bat" "$stage\app\"
Copy-Item "D:\AI应用\hr\hr\recruit-app\packaging\RecruitApp.launch.vbs" "$stage\app\"
```

核对清单（缺一不可）：
- [ ] `stage\app\python\pythonw.exe` 存在；`python\Lib\site-packages\playwright\__init__.py` 存在
- [ ] `stage\app\browsers\chromium-*\chrome-win\chrome.exe` 存在（headed 登录窗口用）
- [ ] `stage\app\web\index.html` 存在；`stage\app\backend\main.py` 存在
- [ ] `stage\app\python\Lib\site-packages` 里无 node.exe / liepin-cli（BOM 不含，§4.8）

## 3) 构建安装包
Inno Setup 6（ISCC.exe 在 PATH 或写全路径）：
```powershell
cd D:\AI应用\hr\hr\recruit-app\packaging
ISCC.exe install.iss          # → packaging\output\recruit-app-setup-0.9.0.exe（约 450MB）
```

版本升级：只改 `install.iss` 顶部 `MyAppVersion` 再跑 ISCC。

## 4) 干净目录安装自测（spec §6 全过才交付试点）
按 `docs/windows-native-check-2026-09-04.md` 的清单在**已装机器**上换 `%LOCALAPPDATA%\RecruitApp`
实路径逐项执行 + Task 9 Step 5 清单，结果回填文档。
````

- [ ] **Step 5: 安装自测清单（在开发机 Windows 侧执行并回填勾选，spec §6 第 1/2/5/6 项）**

- [ ] 双击 `recruit-app-setup-0.9.0.exe` → 安装完成；桌面出现「招聘系统」图标
- [ ] 双击图标 → 自动开 `http://127.0.0.1:8000` → 页面正常（`app\web` 同源）
- [ ] `/health` 通；`%LOCALAPPDATA%\RecruitApp\data\` 下生成 app.db / secret.key / logs\app.log（首次启动）
- [ ] 设置页「打开浏览器登录」全流程通过（登录 → ~20s 自动导入 → 测试连接成功；默认 chromium 静默）
- [ ] 拉取岗位列表与开发机结果一致
- [ ] 重启电脑 → 不手动操作 → ~1 分钟内计划任务拉起后端（ONLOGON）→ 双击图标即用
- [ ] 后端已在跑时双击图标 → 不重复起进程（run.bat /health 幂等）
- [ ] 浏览器通道切「本机 Chrome」→ 走本机 Chrome 引擎（装过 Chrome 的机器）
- [ ] 升级：重跑新版安装包 → `app\` 全新、`app.old` 残留被清理、`data\` 原样保留、登录态仍在
- [ ] 卸载 → 桌面图标/计划任务随卸载清理（schtasks 如残留则手动删除一条）→ `data\` 仍在
- [ ] 8000 被其它程序占用时双击图标 → 明确中文提示（launcher MsgBox）

- [ ] **Step 6: Commit 打包脚本与文档**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add packaging/run.bat packaging/RecruitApp.launch.vbs packaging/install.iss packaging/BUILD.md docs/windows-native-check-2026-09-04.md
git commit -m "feat: offline installer packaging (run.bat launcher, vbs opener, inno iss, build guide)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

（`packaging/stage/`、`packaging/output/` 不入 git——显式不 add，确认 .gitignore 含 packaging/stage 与 packaging/output，或跳过。）

---

### Task 10: 试点与分发文档（中文图文使用说明）

**Files:**
- Create: `docs/usage-guide-standalone-2026-09-04.md`

**Interfaces:**
- Consumes: 全部产物；spec §5 阶段 4/5
- Produces: 分发物料（供 Task 8/9 全部通过后直接交付同事）

- [ ] **Step 1: 写使用说明全文**

`docs/usage-guide-standalone-2026-09-04.md`：

````markdown
# 招聘系统单机版 · 使用说明（同事版）

> 适用：普通 Windows 10/11 电脑（无需安装任何开发环境）。安装包
> `recruit-app-setup-0.9.0.exe`（约 450MB，全离线）。每人安装自己那一套，数据只在本机。

## 一、安装
1. 双击安装包 → 一路「下一步」（建议勾选「创建桌面快捷方式」）。
2. 完成后桌面出现「招聘系统」图标；首次双击会自动启动并打开网页 `http://127.0.0.1:8000`。
   （如浏览器弹出「是否打开本地网页」类提示 → 允许。）
3. 以后每次使用：双击「招聘系统」即可；开机后电脑会自动把系统拉起，无需手动开。

## 二、首次配置（登录你的猎聘企业版账号）
1. 打开系统后进入「设置」页。
2. 「代理」区填上你电脑的科学上网端口（如 HTTP/HTTPS 都填 `http://127.0.0.1:7890`），点「保存设置」。
   填法与你平时浏览器/工具里的代理一致；直连猎聘正常的话可以不填。
3. 回「猎聘企业版账号」区，点 **「打开浏览器登录」** —— 会弹出一个**专用浏览器窗口**
   （独立资料目录，不会碰你日常用的 Chrome/Edge）。
4. 在那个窗口里**手动**输入猎聘企业版的账号密码（或扫码）完成登录。系统绝不代填、绝不自动登录。
5. 登录完成后**约 20 秒**，系统自动把会话导入（页面会提示成功）；也可以马上点「立即导入」。
   —— 以后登录态失效（比如提示 -1701）时，重做 3-5 步即可自愈。

> 兜底：不会用弹窗登录时，也可从日常浏览器 DevTools 复制 Cookie 头，粘贴到
> 「猎聘 Cookie」框 → 点「测试连接」（系统会先自动保存）。

## 三、日常使用
- 先用「测试连接」确认左上角绿勾：说明登录态有效。
- 搜索岗位/批量邀请：与开发机版一致；跑任务全程后台静默，不弹猎聘页面。
- 「浏览器通道」默认「Playwright Chromium」（随软件自带）即可；装了 Chrome 的电脑也可以
  切「本机 Chrome」。
- 高级：想**亲眼看着**每一步操作时，在设置页打开「在登录的浏览器里开着页面操作」——
  页面会开在登录的那个专用浏览器窗口里（登录窗口别关），不影响日常浏览器。

## 四、常见问题
| 现象 | 处理 |
|---|---|
| 双击图标提示「8000 端口被占用」 | 关闭占用程序（如其它软件用了 8000）后重试；确认没有其它招聘系统在跑 |
| 弹窗打开后网页打不开 | 等 10-20 秒再双击图标（后端冷启动中）；仍不行看 `%LOCALAPPDATA%\RecruitApp\data\logs\app.log` |
| 测试连接报「登录浏览器没在运行」 | 设置页重新点「打开浏览器登录」，再在弹窗里登录一次 |
| 登录后超过 1 分钟没自动导入 | 点「立即导入」；仍失败多半是代理没填对/没保存 |
| 杀软提示拦截 | 本软件全部在本机运行、只连 127.0.0.1 与猎聘官网；内网分发未签名，放行即可 |
| 电脑换人/换机 | 数据只在本机。旧机数据在 `%LOCALAPPDATA%\RecruitApp\data`（卸载不会删），
  新机重新安装 + 重新登录即可 |

## 五、卸载
控制面板 → 卸载「招聘系统」。卸载**不会删除你的数据**；确认不再需要时，手动删除整个
`%LOCALAPPDATA%\RecruitApp` 文件夹即可（内含加密密钥与导出数据，请妥善处理）。
````

- [ ] **Step 2: 试点交付核对**

- [ ] 从 Task 9 产物拷 `recruit-app-setup-0.9.0.exe` + 本使用说明（转 PDF 或打印版）到 1 台同事机器
- [ ] 按说明走完整流程：安装 → 首次配置 → 打开浏览器登录 → 自动导入 → 测试连接 → 拉岗位
- [ ] 同事机器重启后自启可用；任何卡点回填本文档 FAQ
- [ ] 试点通过 → 分发其余同事（同一安装包 + 说明；每台各自安装/各自登录）

- [ ] **Step 3: Commit**

```bash
cd /mnt/d/AI应用/hr/hr/recruit-app
git add docs/usage-guide-standalone-2026-09-04.md
git commit -m "docs: standalone edition end-user usage guide (install/login/FAQ) for pilot & rollout" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```
