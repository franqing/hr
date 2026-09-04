# 招聘系统「单机版 A 方案」设计:Windows 原生运行 + Inno Setup 全离线安装包

- 日期:2026-09-04
- 状态:待用户复审(§4.5 登录路径已于 2026-09-04 按用户新需求修订为「打开浏览器登录」)
- 范围:把当前 WSL2 版(开发机可用)改造成「公司普通 Win10/11 可直接安装运行的 Windows 原生单机版」

## 1. 背景与目标

当前系统只在开发机可用:FastAPI 后端跑在 WSL2,靠 Windows 侧 Chrome/Edge CDP 桥、PowerShell relay、netstat+taskkill 等互操作完成浏览器自动化与 Cookie 导入。公司 ≤5 位同事也想使用,每人各自猎聘企业版账号。

用户定稿约束:

- 目标电脑是**普通 Win10/11,无 WSL2、无开发环境**,可能无法访问外网依赖源;
- **不做集中服务器方案**:每台电脑各自装一套单机版,数据与密钥各自本机(Fernet key 每机独立生成);
- **打包方式 = 方案 1 全离线安装包(Inno Setup)**,双击安装即用;
- 登录由用户手动完成:**设置页点「打开浏览器登录」→ 弹出专用登录浏览器(独立 profile,不碰日常浏览器)→ 用户输入账号密码 → 系统约 20 秒内自动导入会话**(与开发机「跑 liepin login 自动导入」体验等价);粘贴 Cookie 作为兜底;系统**绝不自动登录**(只打开登录页,凭据一律人工输入);
- 开发机 WSL2 版保持可用,是回归基线;代码已存 GitHub,任何阶段失败可回退。

成功标准:一台干净 Win10/11 → 双击安装包 → 桌面图标 → 打开网页界面 → 设置页「打开浏览器登录」手动登录后自动导入、测试连接通过(粘贴 Cookie 兜底同样可用)→ 拉取岗位/跑任务与现版效果一致;重启电脑后无需手动操作即可继续使用。

## 2. 现状盘点(改造面)

### 2.1 架构事实(已核对代码)

- FastAPI 后端 `backend/main.py`:**全部路由无 /api 前缀**(`/health`、`/settings`、`/search-profiles`、`/invites`…),uvicorn CLI 启动;已有 lifespan(:70)、CORS 中间件(:82);**无 StaticFiles、无静态托管**。
- Vue3 前端 `frontend/`:`src/api.js` 固定 `baseURL='/api'`;开发期 vite 代理把 `/api` 剥前缀转发到 `localhost:8000`。`frontend/dist` 已在开发机构建过(1.7M)。
- 数据与程序同树:仓库根 `data/`(app.db、secret.key、exports/、uploads/、browser_profile/),由 `backend/db.py:6`、`backend/security.py:7` 以 `Path(__file__).resolve().parent.parent / "data"` 推导。
- `backend/liepin/adapter.py`:WSL/Windows 互操作点集中在 CDP 桥(PowerShell relay 9338→9337)、`_wsl_gateway`、netstat+taskkill 的 `_windows_kill_listener`、`/mnt/c/Program Files` 候选路径、`_resolve_browser` channel 判定。WSL 分支可原样保留。
- `backend/cookie_import.py`:node 候选 `/mnt/c/Program Files/nodejs/node.exe`、wslpath 双向转换、cmd.exe 探 `%USERPROFILE%`、监听 `.liepin-cli` profile Cookie 库 mtime。
- 仓库根 `.gitignore` 忽略 `data/`、`.venv/`、`node_modules/`、`dist/`、`__pycache__/` → 密文库不入 git。

### 2.2 改造点汇总

| 位置 | 现状(WSL 版) | Windows 原生行为 |
|---|---|---|
| `db.py` / `security.py` 数据路径 | 仓库根 `data/` 硬推导 | `RECRUIT_DATA_DIR` 环境变量覆盖(见 §4.2),未设则维持现状 |
| `main.py` | 纯 API,无静态托管 | 探测到前端构建产物即挂 StaticFiles + `/api` 前缀重写中间件(见 §4.3) |
| `adapter.py` 浏览器启动 | `channel=chromium` 用 WSL 侧 Playwright bundled;`chrome/msedge` 走 `/mnt/c` 探测 + CDP 桥 | Playwright 原生直启本机 Chrome/Edge(注册表/标准路径解析);捆绑 chromium 用于默认通道。CDP 桥/WSL 网关整段在原生路径不调用(WSL 分支保留供回归) |
| `adapter.py` `_windows_kill_listener` | netstat+taskkill,PID 级 | 保留(PID 级 taskkill 在原生可用,与「绝不 taskkill 用户浏览器」约束一致) |
| `cookie_import.py` | `/mnt/c` node、wslpath、cmd 探主目录,监听 `.liepin-cli` profile | `os.environ["USERPROFILE"]` 直读;原生路径改走「自启专用登录浏览器 + Python CDP 导出」(见 §4.5),**不依赖 node/liepin-cli** |
| 前端 | baseURL `/api`,vite 代理 | **零改动**:生产同源下 `/api` 由后端重写中间件还原 |

## 3. 目标形态(前期已确认)

每人电脑各装一套;安装包 ~450MB 全离线;默认浏览器自动化走**捆绑 Playwright chromium(静默)**,与原版行为一致;可选本机 Chrome/Edge。

## 4. 技术设计

### 4.1 安装目录(%LOCALAPPDATA%\RecruitApp)

| 目录 | 内容 | 升级时 |
|---|---|---|
| `app\` | 后端代码 + 便携 Python + site-packages 离线依赖 + 捆绑 chromium + 前端产物(`web\`)+ run.bat | 整体覆盖(先改名旧目录为 `app.old` 再替换,失败可手动回退) |
| `data\` | app.db、secret.key、exports\、uploads\、logs\ | **保留不动** |

- 安装到 `%LOCALAPPDATA%` 无需管理员权限;卸载不删除 `data\`,卸载器文案提示用户手动删除(内含密钥与导出数据)。
- 不写注册表服务;自启用**当前用户计划任务**(登录时触发,无需管理员)。

### 4.2 数据路径解耦(零回归关键)

统一概念:**程序根** = `main.py` 所在目录的上一级(即 `Path(__file__).resolve().parent.parent`):WSL 开发 = recruit-app 仓库根;安装版 = `%LOCALAPPDATA%\RecruitApp\app`。

- 在 `backend` 内集中一个数据路径解析(如 `backend/paths.py`):`data_dir() = env RECRUIT_DATA_DIR → <程序根>/data`;`db.py`、`security.py`、exports/uploads/browser_profile 引用全部改走它。
- WSL 开发机不设 env → 落点与现在完全一致,**零回归**;安装版由 `run.bat` 注入 `RECRUIT_DATA_DIR=%LOCALAPPDATA%\RecruitApp\data`。
- 首次启动确保目录存在;`secret.key` 不存在时按现逻辑生成(每机独立)。

### 4.3 静态托管与 /api 重写(前端零改动)

- 后端启动时按序探测前端产物目录,命中第一个即挂载:`env RECRUIT_WEB_DIR` → `<程序根>/frontend/dist`(开发机 build 过)→ `<程序根>/web`(安装版)。均不存在 → 跳过托管,仅 API(与现版一致)。
- 挂载时同时注册中间件:**`/api/*` → `/*`** 前缀重写后交给路由(SPA 下所有请求默认回 `index.html`,静态资源直出)。
- 现有 `GET /health`(:92)复用为启动器健康探测,无需新增端点。
- 开发机日常走 vite dev(:5173),不受影响;直接访问 :8000 时同源托管也自然可用。

### 4.4 浏览器引擎(Windows 原生)

- **默认**(设置页 channel=chromium):Playwright bundled chromium,headless=new 静默跑任务——与 WSL 原版行为一致、确定性最高,同事电脑没装 Chrome 也能用;该浏览器随安装包分发。
- 用户显式改选 `chrome`/`edge`:Playwright 原生 `channel=` 启动,自动探测本机安装;**探测不到 → 「测试连接」返回清晰错误**(提示装 Chrome 或改回默认),不静默换引擎。
- **attach 模式(在已登录浏览器里可见操作)**:复用「打开浏览器登录」(§4.5)拉起的专用实例——同一调试端口与已登录 profile,语义同 WSL 版 liepin-cli 常驻 Chrome;实例未开 → 引导用户先点「打开浏览器登录」;连接失败 → 明确报错并引导切回静默模式。系统绝不操作/关闭用户自行启动的浏览器、绝不 kill 用户进程;只回收自己拉起的专用实例(按启动时记录的 PID)。
- **平台分支机械判定(同事机器零 WSL 保证)**:原生/互操作路径的选择按 `os.name == "nt"`(或等价 `sys.platform == "win32"`)硬判断——Windows 下恒为真,**不做任何启发式探测**;安装版跑在同事电脑上时,整条执行链 = 本机便携 Python + 捆绑 chromium,纯 Windows 原生。WSL 互操作代码(CDP 桥、relay、pgrep/wslpath 等)虽随仓库保留(开发机 WSL 版回归用),在 `os.name == "nt"` 下**永不进入执行**——同事电脑不需要、不依赖、不触发任何 WSL 组件。

### 4.5 登录与 Cookie 自动导入(「打开浏览器登录」主路径)

登录永远由用户手动完成——系统只负责打开登录页,账号密码/扫码一律人工输入(**绝不自动登录**)。两种 Cookie 来源:

**(1) 设置页「打开浏览器登录」(主路径,与开发机 liepin login 体验等价)**

- 按钮 → 后端以**可见窗口(headed)**拉起浏览器:`<浏览器> --remote-debugging-port=<53471;被占则 9222;都被占 → 明确报错> --user-data-dir=<data>\login-profile --no-first-run` 并打开猎聘登录页。浏览器跟随设置 channel:默认捆绑 chromium(Windows 完整版,随包分发,登录可见窗口与静默任务共用);选 chrome/edge 则本机浏览器 + **同一专用 profile**——绝不碰同事日常浏览器。
- 端口候选与现有 CDP 探测(9222/53471)对齐;登录专用实例由后端启动时记录 PID,只回收自己拉起的实例。
- 用户在弹出窗口手动登录(输账号密码或扫码;系统绝不代填、绝不自动登录)。
- 自动导入:后端沿用 WSL 版轮询语义,每 15s stat `<data>\login-profile` 的 Cookie 库 mtime;变化 → **Python 侧 CDP 导出**(playwright `connect_over_cdp` 连该端口 → 取 liepin.com 域 cookie → 拼 Cookie 头)→ 同一套登录态指纹比对、门户 + BFF 双验证 → Fernet 加密落库 + UI 提示成功。**零 node 依赖**(node mjs 导出助手仅 WSL 版使用,原生路径不调用)。
- 登录窗口可随时手动关闭(会话已入加密库,静默任务不受影响);留着不关时可供 attach 模式(§4.4)复用。
- 开发机 WSL 版「跑 liepin login 自动导入」机制原样保留,作回归基线。

**(2) 粘贴 Cookie(兜底)**:现状完整支持(粘贴 → 测试连接,后端校验逻辑不变)。

明文 cookie 仍只在「本机浏览器 → Fernet 加密库」之间流转,绝不进日志/输出/UI。

### 4.6 启动器、计划任务与自启

- **run.bat**(app\ 内):注入 `RECRUIT_DATA_DIR` → `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`,日志追加写 `data\logs\app.log`(必须 `>>`);仅绑定 127.0.0.1,无防火墙弹窗。
- **桌面「招聘系统」启动器**:先确认 8000 上 `/health` 通(说明后端已在跑)→ 直接用默认浏览器开 `http://127.0.0.1:8000`;不通则先起 run.bat(隐藏窗口)再轮询最多 ~15s 后打开。
- **计划任务**(登录时):执行同一 run.bat;若 8000 已被本程序占用(/health 通)则直接退出,防双份。
- 第一版端口固定 8000(同事各自本机,几乎不会冲突);launcher 检测到端口被其他程序占用时给出明确提示,不做端口切换(有真实需求再加,避免无谓复杂度)。

### 4.7 代理

设置页 http/https 代理已支持;原生直连,无 WSL 网络翻译差异,逻辑不动。

### 4.8 打包(Inno Setup,在开发机 Windows 侧构建)

- 物料清单:便携 Python(**首选:从开发机 venv 拷贝 site-packages + 官方 python 安装目录精简,与开发机完全同版本最稳;embeddable 版为后备,打包阶段先验证再定**)+ 全部 pip 依赖离线轮子 + playwright + **playwright chromium-win**(Windows 平台 `playwright install chromium` 产物,含完整 chromium——登录可见窗口用——与 headless shell)+ backend 代码 + `frontend/dist`(打包机 `npm run build`)→ 拷为 `web\` + run.bat/启动器。**不含 node/liepin-cli**(登录导入走 Python CDP,见 §4.5)。
- 单 Inno 脚本 `packaging\install.iss`,版本号驱动;产物 `recruit-app-setup-<版本>.exe`(约 450MB)。
- 安装动作:写 `%LOCALAPPDATA%\RecruitApp` → 建桌面快捷方式 → 注册当前用户计划任务 →(可选)立即启动。
- 升级:覆盖 `app\`(先改名旧目录为 app.old),`data\` 保留;卸载保留 `data\`。
- WSL 内不做 Windows 构建;先保证「Windows 原生直启后端」跑通再进打包(阶段二/三)。

### 4.9 安全

data 只在本机;Fernet 密钥每机独立生成;不新增任何外发通道;日志沿用现有脱敏规则(明文 cookie 绝不落日志/输出/UI)。

## 5. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| 0 WSL 回归基线 | 记录当前 pytest 结果(已知 5 个陈旧 test_adapter 失败忽略)+ 手动冒烟 | 基线快照记录在案 |
| 1 平台层改造 | `paths.py` 数据路径中心化;`main.py` 静态托管 + /api 重写;`adapter.py`/`cookie_import.py` 原生分支 | WSL 全回归:后端照常起、数据仍在仓库根 data、pytest 无新增失败 |
| 2 Windows 原生直启 | 开发机 Windows 侧用 venv 直启后端(不打包):/health、设置保存、测试连接(默认 chromium + 本机 Chrome 两路)、**打开浏览器登录 → 自动导入**、拉岗位 | 与 WSL 版行为一致 |
| 3 打包 | 便携 Python + 离线依赖 + chromium-win + dist → Inno Setup → 安装到干净目录自测 | 干净目录安装后按 §6 清单全过 |
| 4 同事试点 | 1 台同事机器安装试用 | 安装/自启/登录导入/任务全通 |
| 5 分发 | 正式安装包 + 图文使用说明(安装、首次配置、粘贴 Cookie、任务操作、常见问题) | 其余同事自助装通 |

## 6. 测试与验收清单

- **WSL 回归**:现有 pytest(排除 5 个既有失败)+ 手动冒烟(跑一次任务流程)。
- **Windows 自测(阶段 2/3,开发机)**:
  1. 安装后桌面图标双击 → 自动开 `http://127.0.0.1:8000`;
  2. /health 通;页面(同源静态)正常加载;
  3. 设置页保存/读取正常;**点「打开浏览器登录」→ 弹出专用登录浏览器 → 手动登录 → ~20s 自动导入 → 测试连接成功(默认 chromium 静默)**;粘贴 Cookie 兜底路径同样通过;
  4. 拉取岗位列表与 WSL 版结果一致;
  5. 重启电脑 → 计划任务自动拉起 → 启动器直开即用;
  6. 浏览器通道选 chrome 时走本机 Chrome;卸载后 `data\` 仍在。
- **试点验收(同事机器)**:同 1-5(其中 3 走「打开浏览器登录」路径);确认同事无 node/python/Chrome 前置要求。

## 7. 风险与回退

- **Playwright chromium-win 与便携 Python 兼容性**:阶段 2 先在开发机原生验证再打包。
- **drvfs/路径差异、杀软误报 exe**:安装包签名暂不做(内网分发),如杀软拦截以图文说明引导放行。
- **8000 占用**:launcher 明确提示,不静默。
- **升级破坏数据**:data\ 与 app\ 分离 + 升级前整目录手动复制即可回退;代码已存 GitHub,WSL 版随时可用。
- 每阶段独立提交点,失败可单阶段回退。

## 附:本设计不动的清单

WSL 版专属路径(CDP 桥、PowerShell relay、liepin login 自动导入、attach 模式现实现、前端代码与依赖)原样保留,作回归基线;原生分支以「打开浏览器登录」(§4.5)提供与 liepin login 等价的登录导入体验;全程不引入任何自动登录。
